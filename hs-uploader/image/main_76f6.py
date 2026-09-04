#!/usr/bin/env python3
"""
SOYAB API BOT - FastAPI + Telegram Bot Integration
For Android Termux
"""

import os
import secrets
import logging
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import json

# Third-party imports
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==================== CONFIGURATION ====================
# Environment variables are preferred. Fallback to hardcoded values.

# Telegram Bot Token from @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "8913578902:AAHwFUBXAxd94bYfffQpJnPURdmpks5hAog")

# Your Telegram User ID (numeric)
ADMIN_ID = int(os.getenv("ADMIN_ID", "2057359727"))

# API Key for FastAPI authentication
API_KEY = os.getenv("API_KEY", "GENERATE_A_SECURE_RANDOM_KEY")

# Authorized API endpoint (replace with your provider)
AUTHORIZED_API_URL = os.getenv("AUTHORIZED_API_URL", "https://YOUR-AUTHORIZED-API.example/api")

# Mock mode for testing
MOCK_MODE = os.getenv("MOCK_MODE", "True").lower() == "true"

# Server configuration
HOST = "0.0.0.0"
PORT = 8000

# Rate limiting
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60  # seconds

# Validation constants
MAX_COUNT = 100
MIN_UID_LENGTH = 6
MAX_UID_LENGTH = 20
VALID_REGIONS = ["India", "USA", "UK", "Europe", "Asia", "Global"]

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('soyab_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Mask sensitive data in logs
class SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        # Mask API keys and tokens
        if API_KEY and API_KEY != "GENERATE_A_SECURE_RANDOM_KEY":
            message = message.replace(API_KEY, "***API_KEY***")
        if BOT_TOKEN and BOT_TOKEN != "PASTE_TELEGRAM_BOT_TOKEN":
            message = message.replace(BOT_TOKEN, "***BOT_TOKEN***")
        record.msg = message
        return True

logger.addFilter(SensitiveDataFilter())

# ==================== PYDANTIC MODELS ====================
class RequestModel(BaseModel):
    uid: str = Field(..., min_length=MIN_UID_LENGTH, max_length=MAX_UID_LENGTH)
    region: str
    count: int = Field(..., gt=0, le=MAX_COUNT)
    
    @validator('uid')
    def validate_uid(cls, v):
        if not v.isalnum():
            raise ValueError('UID must be alphanumeric')
        return v
    
    @validator('region')
    def validate_region(cls, v):
        if v not in VALID_REGIONS:
            raise ValueError(f'Invalid region. Valid regions: {", ".join(VALID_REGIONS)}')
        return v

class APIResponse(BaseModel):
    success: bool
    status_code: int
    uid: str
    region: str
    requested_count: int
    provider_response: Dict[str, Any]
    timestamp: str

# ==================== AUTHORIZED API CLIENT ====================
class AuthorizedAPIClient:
    """Client for communicating with authorized external API"""
    
    def __init__(self, base_url: str = AUTHORIZED_API_URL, timeout: int = 30):
        self.base_url = base_url
        self.timeout = timeout
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "SOYAB-API-Bot/1.0"
        }
    
    async def send_request(self, uid: str, region: str, count: int) -> Dict[str, Any]:
        """Send request to authorized API provider"""
        
        if MOCK_MODE:
            return self._mock_response(uid, region, count)
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "uid": uid,
                    "region": region,
                    "count": count,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers=self.headers
                )
                
                response.raise_for_status()
                
                # Parse JSON response
                try:
                    provider_response = response.json()
                except json.JSONDecodeError:
                    provider_response = {"raw": response.text}
                
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "provider_data": provider_response
                }
                
        except httpx.TimeoutException:
            logger.error("Timeout while connecting to authorized API")
            return {
                "success": False,
                "status_code": 408,
                "error": "Request timeout"
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from authorized API: {e.response.status_code}")
            return {
                "success": False,
                "status_code": e.response.status_code,
                "error": f"Provider error: {e.response.status_code}"
            }
        except Exception as e:
            logger.error(f"Error communicating with authorized API: {str(e)}")
            return {
                "success": False,
                "status_code": 500,
                "error": "Internal provider error"
            }
    
    def _mock_response(self, uid: str, region: str, count: int) -> Dict[str, Any]:
        """Return clearly labeled mock response for testing"""
        return {
            "success": True,
            "status_code": 200,
            "provider_data": {
                "mock": True,
                "message": "This is a MOCK response for testing purposes only",
                "uid": uid,
                "region": region,
                "requested_count": count,
                "processed_at": datetime.utcnow().isoformat()
            }
        }

# ==================== RATE LIMITER ====================
class RateLimiter:
    def __init__(self):
        self.requests = {}
    
    def is_allowed(self, client_id: str) -> bool:
        now = datetime.now()
        if client_id not in self.requests:
            self.requests[client_id] = []
        
        # Remove old requests
        self.requests[client_id] = [
            req_time for req_time in self.requests[client_id]
            if now - req_time < timedelta(seconds=RATE_LIMIT_WINDOW)
        ]
        
        if len(self.requests[client_id]) >= RATE_LIMIT_REQUESTS:
            return False
        
        self.requests[client_id].append(now)
        return True

# ==================== FASTAPI APP ====================
app = FastAPI(title="SOYAB API BOT", version="1.0.0")
api_client = AuthorizedAPIClient()
rate_limiter = RateLimiter()

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key

@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "SOYAB API BOT",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "mock_mode": MOCK_MODE
    }

@app.post("/request")
async def make_request(
    request_data: RequestModel,
    x_api_key: str = Depends(verify_api_key),
    request: Request = None
):
    """Process API request with validation and forwarding"""
    
    client_ip = request.client.host if request else "unknown"
    
    # Rate limiting
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    # Forward to authorized API
    provider_response = await api_client.send_request(
        uid=request_data.uid,
        region=request_data.region,
        count=request_data.count
    )
    
    # Build response
    response = APIResponse(
        success=provider_response["success"],
        status_code=provider_response["status_code"],
        uid=request_data.uid,
        region=request_data.region,
        requested_count=request_data.count,
        provider_response=provider_response.get("provider_data", {}),
        timestamp=datetime.utcnow().isoformat()
    )
    
    logger.info(f"Request processed for UID: {request_data.uid}, Region: {request_data.region}, Count: {request_data.count}")
    
    return response

# ==================== TELEGRAM BOT ====================
class TelegramBot:
    def __init__(self, token: str, admin_id: int):
        self.token = token
        self.admin_id = admin_id
        self.application = None
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send welcome message"""
        welcome_message = (
            "🤖 Welcome to SOYAB API BOT!\n\n"
            "Available commands:\n"
            "/start - Show this message\n"
            "/id - Get your Telegram ID\n"
            "/status - Check API status\n"
            "/request UID REGION COUNT - Make a request\n"
            "/help - Show help"
        )
        await update.message.reply_text(welcome_message)
    
    async def id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send user's Telegram ID"""
        user_id = update.effective_user.id
        await update.message.reply_text(f"Your Telegram ID: {user_id}")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check API status"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Unauthorized. Admin only command.")
            return
        
        status_message = (
            f"📊 API Status:\n"
            f"• Mode: {'Mock' if MOCK_MODE else 'Production'}\n"
            f"• Endpoint: {AUTHORIZED_API_URL if not MOCK_MODE else 'Mock Mode'}\n"
            f"• Time: {datetime.utcnow().isoformat()}"
        )
        await update.message.reply_text(status_message)
    
    async def request_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /request command"""
        user_id = update.effective_user.id
        
        # Check authorization
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ Unauthorized. Admin only command.")
            return
        
        # Parse arguments
        args = context.args
        if len(args) != 3:
            await update.message.reply_text(
                "❌ Invalid format. Use: /request UID REGION COUNT\n"
                "Example: /request 123456789 India 50"
            )
            return
        
        uid, region, count_str = args
        
        # Validate inputs
        try:
            count = int(count_str)
            request_data = RequestModel(uid=uid, region=region, count=count)
        except ValueError as e:
            await update.message.reply_text(f"❌ Validation error: {str(e)}")
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Invalid request: {str(e)}")
            return
        
        # Send processing message
        processing_msg = await update.message.reply_text("🔄 Processing your request...")
        
        # Forward to API
        try:
            provider_response = await api_client.send_request(
                uid=request_data.uid,
                region=request_data.region,
                count=request_data.count
            )
            
            if provider_response["success"]:
                response_text = (
                    f"✅ Request Successful!\n"
                    f"• UID: {uid}\n"
                    f"• Region: {region}\n"
                    f"• Count: {count}\n"
                    f"• Provider Response: {json.dumps(provider_response.get('provider_data', {}), indent=2)}"
                )
            else:
                response_text = (
                    f"❌ Request Failed\n"
                    f"• Error: {provider_response.get('error', 'Unknown error')}"
                )
            
            await processing_msg.edit_text(response_text)
            
        except Exception as e:
            logger.error(f"Error processing Telegram request: {str(e)}")
            await processing_msg.edit_text(f"❌ Internal error: {str(e)}")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        help_text = (
            "📚 **Available Commands**\n\n"
            "/start - Start the bot\n"
            "/id - Get your Telegram ID\n"
            "/status - Check API status (Admin only)\n"
            "/request UID REGION COUNT - Make a request (Admin only)\n"
            "/help - Show this help\n\n"
            f"**Valid Regions:** {', '.join(VALID_REGIONS)}\n"
            f"**Max Count:** {MAX_COUNT}"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id == self.admin_id
    
    async def setup(self):
        """Setup and start bot"""
        self.application = Application.builder().token(self.token).build()
        
        # Register handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("id", self.id_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("request", self.request_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        
        # Start polling
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logger.info("Telegram bot started successfully")
    
    async def stop(self):
        """Stop bot"""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram bot stopped")

# ==================== MAIN FUNCTION ====================
async def run_servers():
    """Run FastAPI and Telegram bot together"""
    
    # Start Telegram bot
    bot = TelegramBot(BOT_TOKEN, ADMIN_ID)
    
    if BOT_TOKEN != "PASTE_TELEGRAM_BOT_TOKEN":
        try:
            await bot.setup()
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {str(e)}")
            logger.info("Continuing with FastAPI only...")
    else:
        logger.warning("No valid Telegram bot token provided. Running FastAPI only.")
    
    # Start FastAPI
    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info"
    )
    server = uvicorn.Server(config)
    
    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Cleanup
        if BOT_TOKEN != "PASTE_TELEGRAM_BOT_TOKEN":
            await bot.stop()

def generate_api_key():
    """Generate secure API key"""
    return secrets.token_urlsafe(32)

if __name__ == "__main__":
    # Print startup message
    print("=" * 50)
    print("SOYAB API BOT Starting...")
    print(f"Mode: {'MOCK' if MOCK_MODE else 'PRODUCTION'}")
    print(f"Server: http://{HOST}:{PORT}")
    print(f"Telegram Bot: {'Enabled' if BOT_TOKEN != 'PASTE_TELEGRAM_BOT_TOKEN' else 'Disabled'}")
    print("=" * 50)
    
    # Run servers
    asyncio.run(run_servers())