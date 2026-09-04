import os
import re
import asyncio
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ============================================================
# CONFIG
# Put these values in a .env file next to this script:
#
# BOT_TOKEN=123456:ABC...
# ADMIN_ID=123456789
#
# Optional:
# MAX_REDIRECTS=15
# TIMEOUT=20
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8944804268:AAGjmxBHS2-mwjZWF23XwE6Q900BsdmIdFY").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "2057359727").strip()
MAX_REDIRECTS = int(os.getenv("MAX_REDIRECTS", "15"))
TIMEOUT = float(os.getenv("TIMEOUT", "20"))

if not BOT_TOKEN:
    raise SystemExit("ERROR: BOT_TOKEN is missing in .env")
if not ADMIN_ID.isdigit():
    raise SystemExit("ERROR: ADMIN_ID must be your numeric Telegram user ID")
ADMIN_ID = int(ADMIN_ID)

URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; Mobile) "
        "AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Common HTML redirect patterns. This handles normal redirects,
# meta refresh and simple JavaScript location assignments.
META_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\']+)',
    re.I
)
JS_RE = re.compile(
    r'(?:window\.location(?:\.href)?|location(?:\.href)?|document\.location)\s*'
    r'=\s*["\']([^"\']+)["\']',
    re.I
)

def clean_url(url: str) -> str:
    return url.strip().strip("()[]{}<>\"'.,;")

def valid_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme.lower() in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

def extract_next_url(html: str, current_url: str):
    # Meta refresh
    m = META_RE.search(html)
    if m:
        candidate = m.group(1).strip()
        return urljoin(current_url, candidate)

    # Simple JavaScript redirect
    m = JS_RE.search(html)
    if m:
        candidate = m.group(1).strip()
        return urljoin(current_url, candidate)

    # Canonical URL can be useful when a page immediately points to
    # its canonical destination, but only accept an absolute HTTP(S) URL.
    try:
        soup = BeautifulSoup(html, "html.parser")
        canonical = soup.find("link", rel=lambda x: x and "canonical" in x.lower())
        if canonical and canonical.get("href"):
            candidate = urljoin(current_url, canonical["href"].strip())
            if valid_http_url(candidate) and candidate != current_url:
                # Canonical is not necessarily the destination, so use it
                # only as a weak fallback.
                return candidate
    except Exception:
        pass

    return None

async def resolve_url(start_url: str):
    current = clean_url(start_url)
    chain = []
    visited = set()

    timeout = httpx.Timeout(TIMEOUT, connect=TIMEOUT)
    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=False,
        timeout=timeout,
        verify=True,
    ) as client:

        for _ in range(MAX_REDIRECTS + 1):
            if not valid_http_url(current):
                return None, chain, "Invalid URL"

            if current in visited:
                return None, chain, "Redirect loop detected"

            visited.add(current)
            chain.append(current)

            try:
                r = await client.get(current)
            except httpx.TooManyRedirects:
                return None, chain, "Too many redirects"
            except httpx.TimeoutException:
                return None, chain, "Request timed out"
            except httpx.HTTPError as e:
                return None, chain, f"HTTP error: {type(e).__name__}"

            # Standard HTTP redirect.
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("location")
                if not location:
                    return current, chain, None
                current = urljoin(current, location)
                continue

            # Some sites return 200 and redirect in HTML.
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" in content_type or not content_type:
                try:
                    html = r.text
                    nxt = extract_next_url(html, current)
                    if nxt and nxt != current and valid_http_url(nxt):
                        current = nxt
                        continue
                except Exception:
                    pass

            return current, chain, None

    return None, chain, "Redirect limit reached"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔗 URL Resolver Bot\n\n"
        "Send me a normal HTTP/HTTPS short link and I will try to resolve "
        "its final URL.\n\n"
        "Example:\n"
        "https://example.com/short"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send a short URL as a message.\n"
        "The bot follows standard redirects and common HTML/meta/JS redirects."
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🟢 Bot is online.")

async def resolve_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    urls = [clean_url(x) for x in URL_RE.findall(text)]

    if not urls:
        await update.message.reply_text(
            "❌ No HTTP/HTTPS URL found.\nSend a link like:\nhttps://example.com/short"
        )
        return

    # Keep one URL per message to avoid abuse and excessive requests.
    url = urls[0]

    msg = await update.message.reply_text("⏳ Resolving link...")

    final_url, chain, error = await resolve_url(url)

    if final_url:
        if final_url == url and len(chain) == 1:
            text_out = (
                "ℹ️ I could not find a redirect.\n\n"
                f"🔗 URL:\n{final_url}"
            )
        else:
            text_out = (
                "✅ Final URL found\n\n"
                f"🔗 {final_url}\n\n"
                f"↪️ Redirect steps: {max(0, len(chain)-1)}"
            )

        # Telegram messages have a length limit; URLs can be long.
        await msg.edit_text(text_out[:3900])
    else:
        reason = error or "Unknown error"
        await msg.edit_text(
            "⚠️ I could not resolve this link.\n\n"
            f"Reason: {reason}\n"
            "Some shorteners require JavaScript, CAPTCHA, login, "
            "cookies, or anti-bot checks and cannot be resolved by a "
            "simple HTTP client."
        )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text(
        "👑 Admin Panel\n\n"
        f"Your ID: {ADMIN_ID}\n"
        f"Max redirects: {MAX_REDIRECTS}\n"
        f"Timeout: {TIMEOUT}s"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, resolve_message))

    print("SOYAB URL Resolver Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
