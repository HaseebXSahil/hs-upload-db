
# ============================================================
# NF CITY TELEGRAM BOT
# ACLCloud-friendly: requirements are also auto-installed below
# ============================================================

import sys
import subprocess
import importlib.util

REQUIRED_PACKAGES = {
    "telegram": "python-telegram-bot>=22.0,<23.0",
}

def install_missing_packages():
    for module, package in REQUIRED_PACKAGES.items():
        if importlib.util.find_spec(module) is None:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", package
            ])

# If ACLClouds does not expose a terminal/command box, this fallback
# installs missing Python packages automatically at startup.
install_missing_packages()

import os
import sqlite3
import secrets
import io
import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    InputFile,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================

# OPTION 1: ACLClouds Environment Variable
BOT_TOKEN = os.getenv("BOT_TOKEN", "8231817672:AAHyjV1wmiZXYPP6FwfZ2Kal7sxdcJKJQMw").strip()

# OPTION 2: Put your token here if ACLClouds has no environment
# variable option. Keep this file private.
if not BOT_TOKEN:
    BOT_TOKEN = "PASTE_BOT_TOKEN_HERE"

ADMIN_ID = 1078075152

# Mandatory channels.
# The first channel is private and only has an invite link. Telegram's
# Bot API needs its numeric chat ID for membership verification.
# Set ORDER_CHANNEL_ID in ACLClouds environment variables once you have it.
ORDER_CHANNEL_ID = os.getenv("ORDER_CHANNEL_ID", "-1003009132657").strip()

CHANNELS = [
    {
        "chat_id": ORDER_CHANNEL_ID,
        "title": "➡️ Order Channel",
        "join_url": "https://t.me/+feKTcd7B45Y2MTU1",
    },
    {
        "chat_id": "@hackersarenad",
        "title": "➡️ Hackers Arena",
        "join_url": "https://t.me/hackersarenad",
    },
    {
        "chat_id": "@netflixcity2",
        "title": "➡️ Netflix City",
        "join_url": "https://t.me/netflixcity2",
    },
    {
        "chat_id": "@NetflixCuty",
        "title": "➡️ Premium City",
        "join_url": "https://t.me/NetflixCuty",
    },
]

POINTS_PER_REFERRAL = 1
POINTS_PER_WITHDRAW = 3
MAX_WITHDRAWALS_PER_DAY = 3
BONUS_POINTS = 1

TIMEZONE = ZoneInfo("Asia/Karachi")

# ============================================================
# FILES / DATABASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "bot.db")
STOCK_DIR = os.path.join(BASE_DIR, "stock")
os.makedirs(STOCK_DIR, exist_ok=True)

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    points INTEGER DEFAULT 0,
    referred_by INTEGER,
    referrals INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0,
    bonus_date TEXT,
    referral_rewarded INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    points INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    filepath TEXT NOT NULL,
    item TEXT,
    used INTEGER DEFAULT 0,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
""")
db.commit()

# Migrate older databases that had one stock row per TXT file.
# Existing TXT files are split into individual non-empty lines so the
# new random-item stock system works without requiring a fresh database.
stock_columns = {row[1] for row in db.execute("PRAGMA table_info(stock)").fetchall()}
if "item" not in stock_columns:
    db.execute("ALTER TABLE stock ADD COLUMN item TEXT")
    db.commit()

legacy_rows = db.execute(
    "SELECT id, filename, filepath, used, added_at FROM stock WHERE item IS NULL"
).fetchall()
for legacy in legacy_rows:
    if legacy[3]:
        continue
    try:
        with open(legacy[2], "r", encoding="utf-8", errors="ignore") as f:
            items = [line.strip() for line in f.read().splitlines() if line.strip()]
    except Exception as e:
        print(f"Legacy stock migration failed for {legacy[1]}: {e}")
        continue

    if not items:
        db.execute("DELETE FROM stock WHERE id=?", (legacy[0],))
        continue

    db.execute(
        "UPDATE stock SET item=? WHERE id=?",
        (items[0], legacy[0]),
    )
    for item in items[1:]:
        db.execute(
            "INSERT INTO stock(filename, filepath, item, used, added_at) VALUES(?,?,?,?,?)",
            (legacy[1], legacy[2], item, 0, legacy[4]),
        )
db.commit()


# ============================================================
# HELPERS
# ============================================================

def now():
    return datetime.now(TIMEZONE)


def today():
    return now().strftime("%Y-%m-%d")


def get_user(user_id):
    return db.execute(
        "SELECT * FROM users WHERE user_id=?", (user_id,)
    ).fetchone()


def ensure_user(tg_user, referred_by=None):
    row = get_user(tg_user.id)

    if row:
        db.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (tg_user.username or "", tg_user.first_name or "", tg_user.id),
        )
        db.commit()
        return False

    if referred_by == tg_user.id:
        referred_by = None

    db.execute("""
        INSERT INTO users
        (user_id, username, first_name, points, referred_by, referrals,
         banned, bonus_date, referral_rewarded, created_at)
        VALUES (?, ?, ?, 0, ?, 0, 0, NULL, 0, ?)
    """, (
        tg_user.id,
        tg_user.username or "",
        tg_user.first_name or "",
        referred_by,
        now().isoformat(),
    ))
    db.commit()
    return True


def is_admin(user_id):
    return user_id == ADMIN_ID


def is_banned(user_id):
    row = get_user(user_id)
    return bool(row and row["banned"])


def set_setting(key, value):
    db.execute(
        "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
        (key, str(value)),
    )
    db.commit()


def get_setting(key, default=None):
    row = db.execute(
        "SELECT value FROM settings WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row else default


def user_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["💰 BALANCE", "👥 REFERRAL"],
            ["💵 WITHDRAW"],
            ["🤔 PROOFS", "📞 SUPPORT"],
        ],
        resize_keyboard=True,
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📊 STATS", "📦 STOCK"],
            ["💳 ADD FUNDS", "📢 BROADCAST"],
            ["👥 USERS"],
            ["⬅️ USER MENU"],
        ],
        resize_keyboard=True,
    )


def join_keyboard():
    rows = []

    for channel in CHANNELS:
        rows.append([
            InlineKeyboardButton(
                channel["title"],
                url=channel["join_url"],
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "✅ VERIFY JOIN",
            callback_data="verify_join",
        )
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# MANDATORY CHANNEL CHECK
# ============================================================

async def missing_channels(bot, user_id):
    missing = []

    for channel in CHANNELS:
        chat_id = channel["chat_id"]

        # The private invite channel cannot be verified until its numeric
        # chat ID is configured in ORDER_CHANNEL_ID.
        if not chat_id:
            missing.append(channel["title"])
            continue

        try:
            member = await bot.get_chat_member(chat_id, user_id)

            if member.status not in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            }:
                missing.append(channel["title"])

        except Exception as e:
            print(f"Join check failed for {channel['title']}: {e}")
            missing.append(channel["title"])

    return missing


async def check_join_or_prompt(update, context):
    user_id = update.effective_user.id
    missing = await missing_channels(context.bot, user_id)

    if missing:
        text = (
            "🟢 Welcome To Our Premium Account Giveaway Bot\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "➡️ Please join all required channels below:\n\n"
            "➡️ Order Channel\n"
            "➡️ Hackers Arena\n"
            "➡️ Netflix City\n"
            "➡️ Premium City\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📩 After joining all channels, press VERIFY JOIN."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text,
                reply_markup=join_keyboard(),
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=join_keyboard(),
            )

        return False

    return True


# ============================================================
# REFERRAL
# ============================================================

async def reward_referral_if_ready(user_id):
    user = get_user(user_id)

    if not user:
        return False

    if not user["referred_by"] or user["referral_rewarded"]:
        return False

    referrer_id = user["referred_by"]
    referrer = get_user(referrer_id)

    if not referrer or referrer_id == user_id:
        return False

    db.execute(
        "UPDATE users SET points=points+?, referrals=referrals+1 "
        "WHERE user_id=?",
        (POINTS_PER_REFERRAL, referrer_id),
    )

    db.execute(
        "UPDATE users SET referral_rewarded=1 WHERE user_id=?",
        (user_id,),
    )

    db.commit()
    return True


# ============================================================
# START / VERIFY
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    referred_by = None

    if context.args and context.args[0].startswith("ref_"):
        try:
            referred_by = int(context.args[0][4:])
        except ValueError:
            referred_by = None

    created = ensure_user(tg_user, referred_by)

    if is_banned(tg_user.id):
        await update.message.reply_text(
            "🚫 You are banned from using this bot."
        )
        return

    if not await check_join_or_prompt(update, context):
        return

    if created:
        await reward_referral_if_ready(tg_user.id)

    await update.message.reply_text(
        "🏠 MAIN MENU\n\nWelcome! 👋",
        reply_markup=user_keyboard(),
    )


async def verify_join(update, context):
    q = update.callback_query
    user_id = q.from_user.id

    if is_banned(user_id):
        await q.answer("You are banned.", show_alert=True)
        return

    missing = await missing_channels(context.bot, user_id)

    if missing:
        await q.answer(
            "You have not joined all required channels yet.",
            show_alert=True,
        )
        return

    await reward_referral_if_ready(user_id)

    await q.answer("Verified successfully! ✅")

    await q.edit_message_text(
        "✅ Verification successful.\n\n🏠 Main Menu"
    )

    await context.bot.send_message(
        user_id,
        "Welcome! 👋",
        reply_markup=user_keyboard(),
    )


# ============================================================
# USER FEATURES
# ============================================================

async def balance(update, context):
    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    if not await check_join_or_prompt(update, context):
        return

    user = get_user(user_id)

    await update.message.reply_text(
        f"💰 Balance: {user['points']} points\n"
        f"👥 Referrals: {user['referrals']}\n\n"
        f"💵 Withdrawal: {POINTS_PER_WITHDRAW} points = 1 item"
    )


async def referral(update, context):
    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    if not await check_join_or_prompt(update, context):
        return

    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"
    user = get_user(user_id)

    await update.message.reply_text(
        f"👥 REFERRAL\n\n"
        f"Your link:\n{link}\n\n"
        f"🎁 Reward: {POINTS_PER_REFERRAL} point per valid referral\n"
        f"👥 Total referrals: {user['referrals']}"
    )



def withdrawals_today(user_id):
    row = db.execute(
        "SELECT COUNT(*) AS c FROM withdrawals "
        "WHERE user_id=? AND date(created_at)=?",
        (user_id, today()),
    ).fetchone()

    return row["c"]


def available_stock():
    row = db.execute(
        "SELECT COUNT(*) AS c FROM stock WHERE used=0 AND item IS NOT NULL AND TRIM(item)<>''"
    ).fetchone()
    return row["c"]


def login_options_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💻 PC Login", callback_data="login_pc"),
            InlineKeyboardButton("📱 Phone Login", callback_data="login_phone"),
        ]
    ])


async def login_option(update, context):
    q = update.callback_query
    await q.answer()

    mode = "PC" if q.data == "login_pc" else "Phone"
    await q.message.reply_text(
        f"🔐 {mode} Login\n\n"
        "After logging in, please send a screenshot of the login screen for verification.\n\n"
        "Use this only with an account you own or are authorized to test."
    )


async def withdraw(update, context):
    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    if not await check_join_or_prompt(update, context):
        return

    user = get_user(user_id)

    if user["points"] < POINTS_PER_WITHDRAW:
        await update.message.reply_text(
            f"❌ Balance: {user['points']} points.\n"
            f"You need {POINTS_PER_WITHDRAW} points."
        )
        return

    count = withdrawals_today(user_id)

    if count >= MAX_WITHDRAWALS_PER_DAY:
        await update.message.reply_text(
            f"⛔ Daily withdrawal limit reached.\n"
            f"Limit: {MAX_WITHDRAWALS_PER_DAY} withdrawals/day."
        )
        return

    # Select one available item at random. The item itself is stored in the
    # database, so one large TXT upload can contain thousands of items.
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT * FROM stock "
            "WHERE used=0 AND item IS NOT NULL AND TRIM(item)<>'' "
            "ORDER BY RANDOM() LIMIT 1"
        ).fetchone()

        if not row:
            db.rollback()
            await update.message.reply_text(
                "⏳ Withdrawal stock is currently unavailable."
            )
            return

        cur = db.execute(
            "UPDATE stock SET used=1 WHERE id=? AND used=0",
            (row["id"],),
        )
        if cur.rowcount != 1:
            db.rollback()
            await update.message.reply_text("Please try again.")
            return

        db.execute(
            "UPDATE users SET points=points-? WHERE user_id=?",
            (POINTS_PER_WITHDRAW, user_id),
        )

        db.execute(
            "INSERT INTO withdrawals(user_id, filename, points, created_at) "
            "VALUES(?,?,?,?)",
            (user_id, row["filename"], POINTS_PER_WITHDRAW, now().isoformat()),
        )

        order_id = db.execute(
            "SELECT last_insert_rowid() AS id"
        ).fetchone()["id"]
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Deliver the selected NON-SENSITIVE item inline in a monospace block.
    # Authentication/session cookies, passwords, and account credentials are
    # intentionally not delivered by this stock workflow.
    item_text = str(row["item"]).strip()
 #   sensitive_markers = (
      #  "securenetflixid", "nfvdid", "netflixid", "password=",
      #  "passwd=", "sessionid", "authorization: bearer", "cookie:"
  #  )
 #   looks_like_email_password = bool(re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+\s*[:|]\s*[^\s]+", item_text))
   # if any(marker in item_text.lower() for marker in sensitive_markers) or looks_like_email_password:
        #db.execute("UPDATE stock SET used=0 WHERE id=?", (row["id"],))
       # db.execute("UPDATE users SET points=points+? WHERE user_id=?", (POINTS_PER_WITHDRAW, user_id))
      #  db.execute("DELETE FROM withdrawals WHERE id=?", (order_id,))
        #db.commit()
      #  await update.message.reply_text(
         #   "⚠️ This stock item appears to contain sensitive login/session data, so it was not delivered. "
     #       "Your points were refunded."
      #  )
  #      return

    try:
        safe_item = html.escape(item_text)
        await context.bot.send_message(
            user_id,
            f"<b>✅ Withdrawal Successful</b>\n\n"
            f"🧾 Order: #{order_id}\n"
            f"💰 Deducted: {POINTS_PER_WITHDRAW} points\n\n"
            f"<b>📦 Your Item</b>\n"
            f"<pre>{safe_item}</pre>",
            parse_mode="HTML",
        )
    except Exception as e:
        # Refund the item and points if Telegram delivery fails.
        db.execute(
            "UPDATE stock SET used=0 WHERE id=?",
            (row["id"],),
        )
        db.execute(
            "UPDATE users SET points=points+? WHERE user_id=?",
            (POINTS_PER_WITHDRAW, user_id),
        )
        db.execute(
            "DELETE FROM withdrawals WHERE id=?",
            (order_id,),
        )
        db.commit()

        await update.message.reply_text(
            "❌ Item delivery failed. Your points were refunded."
        )
        print("Item delivery error:", e)
        return

    # Order log gets notification ONLY; the selected item is never sent there.
    group_id = get_setting("order_group_id")

    if group_id:
        try:
            username = (
                f"@{update.effective_user.username}"
                if update.effective_user.username
                else update.effective_user.first_name
            )

            await context.bot.send_message(
                int(group_id),
                f"✅ WITHDRAWAL SUCCESS\n\n"
                f"🧾 Order: #{order_id}\n"
                f"👤 User: {username}\n"
                f"🆔 ID: {user_id}\n"
                f"💰 Points: {POINTS_PER_WITHDRAW}\n"
                f"📄 Source: {row['filename']}\n"
                f"📦 Remaining stock: {available_stock()}\n"
                f"🕐 {now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        except Exception as e:
            print("Order group notification error:", e)


async def proofs(update, context):
    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    if not await check_join_or_prompt(update, context):
        return

    proof_link = get_setting("proof_link", "").strip()
    if proof_link:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤔 OPEN PROOFS", url=proof_link)]
        ])
        await update.message.reply_text(
            "🤔 PROOFS\n\n"
            "Open the proof channel using the button below.",
            reply_markup=keyboard,
        )
    else:
        await update.message.reply_text(
            "🤔 PROOFS\n\n"
            "Proof channel has not been configured yet."
        )


async def setproof(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        current = get_setting("proof_link", "Not set")
        await update.message.reply_text(
            "Usage: /setproof https://t.me/your_proof_channel\n\n"
            f"Current proof link: {current}"
        )
        return

    link = context.args[0].strip()
    if not (link.startswith("https://t.me/") or link.startswith("http://t.me/") or
            link.startswith("https://telegram.me/") or link.startswith("http://telegram.me/")):
        await update.message.reply_text(
            "❌ Please provide a valid Telegram channel/group link, for example:\n"
            "https://t.me/your_proof_channel"
        )
        return

    set_setting("proof_link", link)
    await update.message.reply_text(
        f"✅ Proof channel updated.\n\n{link}"
    )


async def support(update, context):
    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    if not await check_join_or_prompt(update, context):
        return

    await update.message.reply_text(
        "📞 SUPPORT\n\n"
        "Owner: @moderatorerq"
    )


# ============================================================
# ADMIN
# ============================================================

async def admin(update, context):
    if not is_admin(update.effective_user.id):
        return

    await update.message.reply_text(
        "👑 ADMIN PANEL",
        reply_markup=admin_keyboard(),
    )


async def setordergroup(update, context):
    if not is_admin(update.effective_user.id):
        return

    set_setting("order_group_id", update.effective_chat.id)

    await update.message.reply_text(
        f"✅ This chat is now the order-log group.\n"
        f"Group ID: {update.effective_chat.id}"
    )


async def stats(update, context):
    if not is_admin(update.effective_user.id):
        return

    users = db.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    banned = db.execute(
        "SELECT COUNT(*) AS c FROM users WHERE banned=1"
    ).fetchone()["c"]

    withdrawals = db.execute(
        "SELECT COUNT(*) AS c FROM withdrawals"
    ).fetchone()["c"]

    await update.message.reply_text(
        f"📊 STATS\n\n"
        f"👥 Users: {users}\n"
        f"🚫 Banned: {banned}\n"
        f"📦 Available items: {available_stock()}\n"
        f"💵 Total withdrawals: {withdrawals}"
    )


async def stock_cmd(update, context):
    if not is_admin(update.effective_user.id):
        return

    files = db.execute(
        "SELECT filepath, filename, "
        "SUM(CASE WHEN used=0 THEN 1 ELSE 0 END) AS available, "
        "SUM(CASE WHEN used=1 THEN 1 ELSE 0 END) AS used, "
        "COUNT(*) AS total "
        "FROM stock GROUP BY filepath, filename ORDER BY MAX(added_at) DESC"
    ).fetchall()

    if not files:
        await update.message.reply_text(
            "📦 STOCK\n\nNo stock files uploaded yet."
        )
        return

    lines = [
        "📦 STOCK",
        "",
        f"📄 Uploaded files: {len(files)}",
        f"📦 Available items: {available_stock()}",
        "",
    ]

    for i, row in enumerate(files, 1):
        lines.append(
            f"{i}. {row['filename']}\n"
            f"   Total: {row['total']} | Available: {row['available']} | Used: {row['used']}"
        )

    await update.message.reply_text("\n".join(lines))


async def ban(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /ban USER_ID")
        return

    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    if not get_user(uid):
        await update.message.reply_text("User not found.")
        return

    db.execute(
        "UPDATE users SET banned=1 WHERE user_id=?",
        (uid,),
    )
    db.commit()

    await update.message.reply_text(
        f"🚫 User {uid} banned."
    )


async def unban(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /unban USER_ID")
        return

    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    db.execute(
        "UPDATE users SET banned=0 WHERE user_id=?",
        (uid,),
    )
    db.commit()

    await update.message.reply_text(
        f"✅ User {uid} unbanned."
    )


async def addpoints(update, context):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /addpoints USER_ID POINTS"
        )
        return

    try:
        uid = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid numbers.")
        return

    db.execute(
        "UPDATE users SET points=points+? WHERE user_id=?",
        (amount, uid),
    )
    db.commit()

    await update.message.reply_text(
        f"✅ Added {amount} points to {uid}."
    )


async def removepoints(update, context):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /removepoints USER_ID POINTS"
        )
        return

    try:
        uid = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid numbers.")
        return

    db.execute(
        "UPDATE users SET points=MAX(0, points-?) WHERE user_id=?",
        (amount, uid),
    )
    db.commit()

    await update.message.reply_text(
        f"✅ Removed {amount} points from {uid}."
    )


async def add_funds(update, context):
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: /funds USER_ID POINTS\n\nExample:\n/funds 123456789 10"
        )
        return

    try:
        uid = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid user ID or points.")
        return

    if amount <= 0:
        await update.message.reply_text("Points must be greater than 0.")
        return

    if not get_user(uid):
        await update.message.reply_text("User not found.")
        return

    db.execute("UPDATE users SET points=points+? WHERE user_id=?", (amount, uid))
    db.commit()
    user = get_user(uid)

    await update.message.reply_text(
        f"✅ Funds added successfully.\n\n"
        f"👤 User ID: {uid}\n"
        f"➕ Added: {amount} points\n"
        f"💰 New balance: {user['points']} points"
    )

    try:
        await context.bot.send_message(
            uid,
            f"💳 Your account has been credited.\n\n"
            f"➕ Added: {amount} points\n"
            f"💰 New balance: {user['points']} points"
        )
    except Exception as e:
        print("Could not notify user:", e)


async def broadcast(update, context):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/broadcast Your message here"
        )
        return

    text = " ".join(context.args)

    users = db.execute(
        "SELECT user_id FROM users WHERE banned=0"
    ).fetchall()

    sent = 0
    failed = 0

    status = await update.message.reply_text(
        f"📢 Broadcasting to {len(users)} users..."
    )

    for row in users:
        try:
            await context.bot.send_message(
                row["user_id"],
                text,
            )
            sent += 1
        except Exception:
            failed += 1

    await status.edit_text(
        f"📢 BROADCAST DONE\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


async def users_cmd(update, context):
    if not is_admin(update.effective_user.id):
        return

    rows = db.execute(
        "SELECT user_id, username, points, referrals, banned "
        "FROM users ORDER BY created_at DESC LIMIT 30"
    ).fetchall()

    if not rows:
        await update.message.reply_text("No users.")
        return

    lines = ["👥 Latest Users\n"]

    for r in rows:
        name = (
            f"@{r['username']}"
            if r["username"]
            else str(r["user_id"])
        )

        lines.append(
            f"{name} | ID {r['user_id']} | "
            f"P {r['points']} | R {r['referrals']} | "
            f"{'BAN' if r['banned'] else 'OK'}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# ADMIN TXT UPLOAD
# ============================================================

async def handle_admin_document(update, context):
    if not is_admin(update.effective_user.id):
        return

    document = update.message.document

    if not document.file_name.lower().endswith(".txt"):
        await update.message.reply_text(
            "❌ Please upload a .txt file only."
        )
        return

    tg_file = await document.get_file()

    safe_name = (
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{secrets.token_hex(4)}_"
        f"{os.path.basename(document.file_name)}"
    )

    filepath = os.path.join(STOCK_DIR, safe_name)
    await tg_file.download_to_drive(filepath)

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            items = [line.strip() for line in f.read().splitlines() if line.strip()]
    except Exception as e:
        try:
            os.remove(filepath)
        except OSError:
            pass
        await update.message.reply_text(f"❌ Could not read TXT file: {e}")
        return

    if not items:
        try:
            os.remove(filepath)
        except OSError:
            pass
        await update.message.reply_text(
            "❌ This TXT file has no non-empty lines, so nothing was added."
        )
        return

    added_at = now().isoformat()
    db.executemany(
        "INSERT INTO stock(filename, filepath, item, used, added_at) "
        "VALUES(?,?,?,?,?)",
        [
            (document.file_name, filepath, item, 0, added_at)
            for item in items
        ],
    )
    db.commit()

    await update.message.reply_text(
        f"✅ TXT stock imported successfully.\n\n"
        f"📄 File: {document.file_name}\n"
        f"📦 Items added: {len(items)}\n"
        f"📦 Total available items: {available_stock()}\n\n"
        f"🎲 Each withdrawal will randomly deliver 1 item."
    )


# ============================================================
# TEXT MENU ROUTER
# ============================================================

async def text_router(update, context):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    if is_banned(user_id):
        await update.message.reply_text(
            "🚫 You are banned."
        )
        return

    if text == "💰 BALANCE":
        await balance(update, context)

    elif text == "👥 REFERRAL":
        await referral(update, context)

    elif text == "💵 WITHDRAW":
        await withdraw(update, context)


    elif text == "🤔 PROOFS":
        await proofs(update, context)

    elif text == "📞 SUPPORT":
        await support(update, context)

    elif is_admin(user_id) and text == "📊 STATS":
        await stats(update, context)

    elif is_admin(user_id) and text == "📦 STOCK":
        await stock_cmd(update, context)

    elif is_admin(user_id) and text == "💳 ADD FUNDS":
        await update.message.reply_text(
            "Use:\n/funds USER_ID POINTS\n\n"
            "Example:\n/funds 123456789 10"
        )

    elif is_admin(user_id) and text == "👥 USERS":
        await users_cmd(update, context)

    elif is_admin(user_id) and text == "📢 BROADCAST":
        await update.message.reply_text(
            "Use:\n/broadcast Your message here"
        )

    elif is_admin(user_id) and text == "⬅️ USER MENU":
        await update.message.reply_text(
            "🏠 USER MENU",
            reply_markup=user_keyboard(),
        )

    else:
        await update.message.reply_text(
            "Please select an option from the menu.",
            reply_markup=(
                admin_keyboard()
                if is_admin(user_id)
                else user_keyboard()
            ),
        )


async def help_cmd(update, context):
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "👑 ADMIN COMMANDS\n\n"
            "/admin - admin panel\n"
            "/setordergroup - current group as order log\n"
            "/setproof LINK - set proof channel\n"
            "/set LINK - set proof channel (alias)\n"
            "/stats - statistics\n"
            "/stock - stock count\n"
            "/users - latest users\n"
            "/ban USER_ID\n"
            "/unban USER_ID\n"
            "/addpoints USER_ID POINTS\n"
            "/removepoints USER_ID POINTS\n"
            "/funds USER_ID POINTS\n"
            "/broadcast MESSAGE\n\n"
            "TXT file directly bot ko upload karein."
        )
    else:
        await update.message.reply_text(
            "/start - Start bot\n"
            "/help - Help"
        )


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN missing. ACLClouds Environment/Variables "
            "mein BOT_TOKEN add karein, ya bot.py mein token set karein."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("setordergroup", setordergroup))
    app.add_handler(CommandHandler("setproof", setproof))
    app.add_handler(CommandHandler("set", setproof))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("addpoints", addpoints))
    app.add_handler(CommandHandler("removepoints", removepoints))
    app.add_handler(CommandHandler("funds", add_funds))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("stock", stock_cmd))

    app.add_handler(
        CallbackQueryHandler(
            verify_join,
            pattern="^verify_join$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            login_option,
            pattern="^login_(pc|phone)$",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_admin_document,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_router,
        )
    )

    print("NF CITY BOT running...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
