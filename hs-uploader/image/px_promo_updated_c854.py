import os
import re
import sqlite3
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8636106021:AAF_4VuAe4a561i-_KFHEOOsR4p7GT5_Fm0")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "7439860263").split(",")
    if x.strip().isdigit()
}

DB_FILE = "px_members.db"

# Order posts are sent here.
ORDER_CHANNEL_ID = int(os.getenv("ORDER_CHANNEL_ID", "-1003928742180"))

# Initial required channels. Admin can add/remove channels from
# the Admin Panel.
DEFAULT_REQUIRED_CHANNELS = [
    (-1002324665576, "𝑪𝒉𝒂𝒏𝒏𝒆𝒍 𝟏", "https://t.me/+thrD4TkRtmU3YzQ0"),
    (-1003928742180, "𝑪𝒉𝒂𝒏𝒏𝒆𝒍 𝟐", "https://t.me/+VMdKQdAvP5VkNWM0"),
    (-1003883206919, "𝑪𝒉𝒂𝒏𝒏𝒆𝒍 𝟑", "https://t.me/+e2XhBhtHzxQ1ZmRk"),
]


# ============================================================
# DATABASE
# ============================================================
db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    members INTEGER NOT NULL,
    link TEXT NOT NULL,
    created_at TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    members INTEGER NOT NULL DEFAULT 0,
    button_text TEXT,
    button_url TEXT,
    created_at TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS promo_redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    redeemed_at TEXT,
    UNIQUE(user_id, code)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS user_rewards (
    user_id INTEGER PRIMARY KEY,
    promo_members INTEGER NOT NULL DEFAULT 0
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS required_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    link TEXT NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")

# Seed admins from ADMIN_IDS.
for admin_id in ADMIN_IDS:
    db.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (admin_id,))

# Seed required channels only when the table is empty.
if db.execute("SELECT COUNT(*) c FROM required_channels").fetchone()["c"] == 0:
    for chat_id, name, link in DEFAULT_REQUIRED_CHANNELS:
        db.execute(
            "INSERT OR IGNORE INTO required_channels(chat_id,name,link) VALUES (?,?,?)",
            (chat_id, name, link),
        )

db.commit()


# ============================================================
# HELPERS
# ============================================================
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    return db.execute(
        "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
    ).fetchone() is not None


def save_user(tg_user):
    db.execute("""
        INSERT INTO users(user_id, username, first_name, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
    """, (
        tg_user.id,
        tg_user.username or "",
        tg_user.first_name or "",
        now(),
    ))
    db.commit()


def get_user(user_id):
    return db.execute(
        "SELECT * FROM users WHERE user_id=?", (user_id,)
    ).fetchone()


def get_required_channels():
    return db.execute(
        "SELECT * FROM required_channels ORDER BY id"
    ).fetchall()


def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 𝑴𝒀 𝑨𝑪𝑪𝑶𝑼𝑵𝑻", callback_data="account"),
            InlineKeyboardButton("🛒 𝑪𝑹𝑬𝑨𝑻𝑬 𝑶𝑹𝑫𝑬𝑹", callback_data="create_order"),
        ],
        [
            InlineKeyboardButton("📢 𝑪𝑯𝑨𝑵𝑵𝑬𝑳𝑺", callback_data="channels"),
            InlineKeyboardButton("📊 𝑺𝑻𝑨𝑻𝑰𝑺𝑻𝑰𝑪𝑺", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("🎁 𝑰𝑵𝑽𝑰𝑻𝑬 𝑷𝑹𝑶𝑴𝑶 𝑪𝑶𝑫𝑬", callback_data="promo_code"),
        ],
    ])


def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ 𝑩𝑨𝑪𝑲", callback_data="back")]
    ])


def join_keyboard():
    rows = []
    for row in get_required_channels():
        rows.append([
            InlineKeyboardButton(f"📢 {row['name']}", url=row["link"])
        ])
    rows.append([
        InlineKeyboardButton("✅ 𝑽𝑬𝑹𝑰𝑭𝒀 𝑱𝑶𝑰𝑵", callback_data="verify_join")
    ])
    return InlineKeyboardMarkup(rows)


async def check_all_channels(user_id, context):
    missing = []
    for row in get_required_channels():
        try:
            member = await context.bot.get_chat_member(row["chat_id"], user_id)
            if member.status in ("left", "kicked"):
                missing.append((row["name"], row["link"]))
        except Exception:
            missing.append((row["name"], row["link"]))
    return missing


# ============================================================
# CHANNEL LINK VALIDATION
# Only public Telegram CHANNEL links are accepted for orders.
# Groups/supergroups are rejected.
# ============================================================
def extract_public_username(link: str):
    link = link.strip()

    m = re.fullmatch(
        r"https?://t\.me/([A-Za-z0-9_]{5,32})/?",
        link,
        flags=re.IGNORECASE,
    )
    if not m:
        return None

    username = m.group(1)
    if username.startswith("+"):
        return None
    return username


async def validate_channel_link(link: str, context):
    username = extract_public_username(link)
    if not username:
        return False, "❌ Please send a valid *public Telegram channel link*.\n\nExample:\n`https://t.me/YourChannel`"

    try:
        chat = await context.bot.get_chat(f"@{username}")
    except Exception:
        return False, (
            "❌ *Channel not found or inaccessible.*\n\n"
            "Please send the public link of a Telegram channel where the bot can access the channel."
        )

    if chat.type != "channel":
        return False, "❌ *Groups are not accepted.*\n\nPlease send a *Telegram CHANNEL* link only."

    return True, ""


def masked_order_link(link: str):
    # Example output style: https://xxxxxxTkR
    clean = link.rstrip("/")
    tail = clean.split("/")[-1]
    if len(tail) >= 3:
        return "https://" + ("x" * 6) + tail[-3:]
    return "https://xxxxxx"


# ============================================================
# START
# ============================================================
WELCOME = """✨ *𝑾𝑬𝑳𝑪𝑶𝑴𝑬 𝑻𝑶 PX 𝑴𝑬𝑴𝑩𝑬𝑹𝑺 𝑯𝑼𝑩* ✨

🚀 *Free & Fast Members Service*
📦 Create your Telegram channel members order easily.
⚡ Fast processing
💯 Simple & easy system

📢 First join all required channels, then press VERIFY.
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    missing = await check_all_channels(user.id, context)
    if missing:
        await update.message.reply_text(
            WELCOME + "\n👇 *Join all required channels, then press VERIFY.*",
            parse_mode="Markdown",
            reply_markup=join_keyboard(),
        )
        return

    await update.message.reply_text(
        WELCOME + "\n✅ *Access Verified!*",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


# ============================================================
# CALLBACKS
# ============================================================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    save_user(q.from_user)

    if q.data == "verify_join":
        missing = await check_all_channels(user_id, context)
        if missing:
            await q.edit_message_text(
                "❌ *Not Verified Yet*\n\n"
                "📢 Please join all required channels.",
                parse_mode="Markdown",
                reply_markup=join_keyboard(),
            )
            return

        await q.edit_message_text(
            "✅ *JOIN VERIFIED!*\n\n🎉 You can now use the bot.",
            parse_mode="Markdown",
        )
        await context.bot.send_message(
            user_id,
            "✨ *MAIN MENU* ✨",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
        return

    if q.data == "account":
        row = get_user(user_id)
        orders = db.execute(
            "SELECT COUNT(*) c FROM orders WHERE user_id=?", (user_id,)
        ).fetchone()["c"]

        reward_row = db.execute(
            "SELECT promo_members FROM user_rewards WHERE user_id=?",
            (user_id,)
        ).fetchone()
        promo_members = reward_row["promo_members"] if reward_row else 0

        await q.edit_message_text(
            f"👤 *MY ACCOUNT*\n\n"
            f"🆔 Member ID: `{user_id}`\n"
            f"👤 Name: {q.from_user.first_name}\n"
            f"🛒 Total Orders: *{orders}*\n"
            f"🎁 Promo Members: *{promo_members}*",
            parse_mode="Markdown",
            reply_markup=back_menu(),
        )
        return

    if q.data == "channels":
        channels = get_required_channels()
        if not channels:
            text = "📢 *REQUIRED CHANNELS*\n\nNo channels configured."
        else:
            text = "📢 *REQUIRED CHANNELS*\n\n"
            text += "\n".join(
                f"• {r['name']}" for r in channels
            )

        await q.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=join_keyboard(),
        )
        return

    if q.data == "stats":
        total_users = db.execute(
            "SELECT COUNT(*) c FROM users"
        ).fetchone()["c"]
        total_orders = db.execute(
            "SELECT COUNT(*) c FROM orders"
        ).fetchone()["c"]

        await q.edit_message_text(
            f"📊 *BOT STATISTICS*\n\n"
            f"👥 Total Members: *{total_users}*\n"
            f"🛒 Total Orders: *{total_orders}*",
            parse_mode="Markdown",
            reply_markup=back_menu(),
        )
        return

    if q.data == "promo_code":
        context.user_data["awaiting_promo_code"] = True
        await q.edit_message_text(
            "🎁 *INVITE PROMO CODE*\n\n"
            "Enter the promo code shared by the admin.\n\n"
            "Example: `WELCOME100`",
            parse_mode="Markdown",
            reply_markup=back_menu(),
        )
        return

    if q.data == "create_order":
        missing = await check_all_channels(user_id, context)
        if missing:
            await q.edit_message_text(
                "❌ *Please join all required channels first.*",
                parse_mode="Markdown",
                reply_markup=join_keyboard(),
            )
            return

        context.user_data["awaiting_order_link"] = True
        context.user_data.pop("awaiting_order_members", None)

        await q.edit_message_text(
            "🛒 *NEW MEMBER ORDER*\n\n"
            "🔗 Send your *Telegram CHANNEL link* only.\n\n"
            "Example:\n"
            "`https://t.me/YourChannel`\n\n"
            "❌ Groups are not accepted.",
            parse_mode="Markdown",
            reply_markup=back_menu(),
        )
        return

    if q.data == "back":
        context.user_data.clear()
        await q.edit_message_text(
            "✨ *MAIN MENU* ✨",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
        return

    # ---------------- ADMIN PANEL ----------------
    if q.data == "admin_panel":
        if not is_admin(user_id):
            await q.answer("Admin only.", show_alert=True)
            return
        await show_admin_panel(q)
        return

    if q.data == "admin_members":
        if not is_admin(user_id):
            return
        total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        orders = db.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        await q.edit_message_text(
            f"👥 *MEMBERS*\n\n"
            f"Total Members: *{total}*\n"
            f"Total Orders: *{orders}*",
            parse_mode="Markdown",
            reply_markup=admin_back_menu(),
        )
        return

    if q.data == "admin_broadcast":
        if not is_admin(user_id):
            return
        context.user_data["admin_broadcast"] = True
        await q.edit_message_text(
            "📢 *BROADCAST MESSAGE*\n\n"
            "Send the message you want to broadcast to all bot members.",
            parse_mode="Markdown",
            reply_markup=admin_back_menu(),
        )
        return

    if q.data == "admin_channels":
        if not is_admin(user_id):
            return
        await show_channel_management(q)
        return

    if q.data == "admin_add_channel":
        if not is_admin(user_id):
            return
        context.user_data["admin_add_channel"] = True
        await q.edit_message_text(
            "➕ *ADD CHANNEL*\n\n"
            "Send in this format:\n"
            "`-1001234567890 | Channel Name | https://t.me/ChannelUsername`\n\n"
            "⚠️ The chat must be a Telegram *channel*, not a group.",
            parse_mode="Markdown",
            reply_markup=admin_back_menu(),
        )
        return

    if q.data.startswith("admin_del_channel:"):
        if not is_admin(user_id):
            return
        try:
            db_id = int(q.data.split(":", 1)[1])
            db.execute("DELETE FROM required_channels WHERE id=?", (db_id,))
            db.commit()
        except Exception:
            pass
        await show_channel_management(q)
        return

    if q.data == "admin_promos":
        if not is_admin(user_id):
            return
        await show_promo_management(q)
        return

    if q.data == "admin_add_promo":
        if not is_admin(user_id):
            return
        context.user_data["admin_add_promo"] = True
        await q.edit_message_text(
            "🎁 *CREATE PROMO CODE*\n\n"
            "Send in this format:\n"
            "`CODE | MEMBERS | BUTTON TEXT | BUTTON URL`\n\n"
            "Example:\n"
            "`WELCOME100 | 100 | JOIN CHANNEL | https://t.me/YourChannel`\n\n"
            "• CODE = promo code\n"
            "• MEMBERS = members reward\n"
            "• BUTTON TEXT = custom button name\n"
            "• BUTTON URL = button link\n\n"
            "Without a custom button:\n"
            "`CODE | MEMBERS`",
            parse_mode="Markdown",
            reply_markup=admin_back_menu(),
        )
        return

    if q.data.startswith("admin_del_promo:"):
        if not is_admin(user_id):
            return
        code = q.data.split(":", 1)[1]
        db.execute("DELETE FROM promo_codes WHERE code=?", (code,))
        db.execute("DELETE FROM promo_redemptions WHERE code=?", (code,))
        db.commit()
        await show_promo_management(q)
        return

    if q.data == "admin_management":
        if not is_admin(user_id):
            return
        await show_admin_management(q)
        return

    if q.data == "admin_add_admin":
        if not is_admin(user_id):
            return
        context.user_data["admin_add_admin"] = True
        await q.edit_message_text(
            "➕ *ADD ADMIN*\n\nSend the Telegram numeric User ID.",
            parse_mode="Markdown",
            reply_markup=admin_back_menu(),
        )
        return

    if q.data.startswith("admin_del_admin:"):
        if not is_admin(user_id):
            return
        try:
            admin_id = int(q.data.split(":", 1)[1])
            if admin_id != user_id:
                db.execute("DELETE FROM admins WHERE user_id=?", (admin_id,))
                db.commit()
        except Exception:
            pass
        await show_admin_management(q)
        return

    if q.data == "admin_add_reward" or q.data == "admin_remove_reward":
        # Rewards/coins have been removed from this version.
        await q.answer("Rewards system is removed.", show_alert=True)
        return

    if q.data == "admin_back":
        if not is_admin(user_id):
            return
        await show_admin_panel(q)
        return


# ============================================================
# ADMIN UI
# ============================================================
def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 𝑩𝑹𝑶𝑨𝑫𝑪𝑨𝑺𝑻", callback_data="admin_broadcast"),
            InlineKeyboardButton("👥 𝑴𝑬𝑴𝑩𝑬𝑹𝑺", callback_data="admin_members"),
        ],
        [
            InlineKeyboardButton("📢 𝑪𝑯𝑨𝑵𝑵𝑬𝑳 𝑴𝑨𝑵𝑨𝑮𝑬𝑴𝑬𝑵𝑻", callback_data="admin_channels"),
        ],
        [
            InlineKeyboardButton("🎁 𝑷𝑹𝑶𝑴𝑶 𝑪𝑶𝑫𝑬𝑺", callback_data="admin_promos"),
        ],
        [
            InlineKeyboardButton("👑 𝑨𝑫𝑴𝑰𝑵 𝑴𝑨𝑵𝑨𝑮𝑬𝑴𝑬𝑵𝑻", callback_data="admin_management"),
        ],
        [
            InlineKeyboardButton("➕ 𝑨𝑫𝑫 𝑹𝑬𝑾𝑨𝑹𝑫𝑺", callback_data="admin_add_reward"),
            InlineKeyboardButton("➖ 𝑹𝑬𝑴𝑶𝑽𝑬 𝑹𝑬𝑾𝑨𝑹𝑫𝑺", callback_data="admin_remove_reward"),
        ],
    ])


def admin_back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ 𝑨𝑫𝑴𝑰𝑵 𝑷𝑨𝑵𝑬𝑳", callback_data="admin_back")]
    ])


async def show_admin_panel(q):
    total_users = db.execute(
        "SELECT COUNT(*) c FROM users"
    ).fetchone()["c"]
    total_orders = db.execute(
        "SELECT COUNT(*) c FROM orders"
    ).fetchone()["c"]
    total_channels = db.execute(
        "SELECT COUNT(*) c FROM required_channels"
    ).fetchone()["c"]
    total_admins = db.execute(
        "SELECT COUNT(*) c FROM admins"
    ).fetchone()["c"]

    await q.edit_message_text(
        f"👑 *PX ADMIN PANEL*\n\n"
        f"👥 Members: *{total_users}*\n"
        f"🛒 Orders: *{total_orders}*\n"
        f"📢 Required Channels: *{total_channels}*\n"
        f"👑 Admins: *{total_admins}*\n\n"
        f"💳 Pakistani bank/payment system: *REMOVED*\n"
        f"🎁 Rewards system: *REMOVED*",
        parse_mode="Markdown",
        reply_markup=admin_menu(),
    )


async def show_promo_management(q):
    rows = db.execute(
        "SELECT * FROM promo_codes ORDER BY created_at DESC"
    ).fetchall()

    text = "🎁 *PROMO CODE MANAGEMENT*\n\n"
    if not rows:
        text += "No promo codes created yet."
    else:
        for row in rows:
            text += f"• `{row['code']}` → *{row['members']} members*"
            if row["button_text"]:
                text += f"\n  🔘 {row['button_text']}"
            text += "\n\n"

    buttons = []
    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"❌ Remove {row['code']}",
                callback_data=f"admin_del_promo:{row['code']}"
            )
        ])
    buttons.append([
        InlineKeyboardButton("➕ Create Promo Code", callback_data="admin_add_promo")
    ])
    buttons.append([
        InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")
    ])

    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_channel_management(q):
    rows = get_required_channels()
    text = "📢 *CHANNEL MANAGEMENT*\n\n"

    if not rows:
        text += "No required channels."
    else:
        for i, row in enumerate(rows, 1):
            text += f"{i}. *{row['name']}*\n`{row['chat_id']}`\n{row['link']}\n\n"

    buttons = []
    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"❌ Remove {row['name']}",
                callback_data=f"admin_del_channel:{row['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")
    ])
    buttons.append([
        InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")
    ])

    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_admin_management(q):
    rows = db.execute(
        "SELECT user_id FROM admins ORDER BY user_id"
    ).fetchall()

    text = "👑 *ADMIN MANAGEMENT*\n\n"
    if not rows:
        text += "No admins configured."
    else:
        for row in rows:
            text += f"• `{row['user_id']}`\n"

    buttons = []
    for row in rows:
        buttons.append([
            InlineKeyboardButton(
                f"❌ Remove {row['user_id']}",
                callback_data=f"admin_del_admin:{row['user_id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("➕ Add Admin", callback_data="admin_add_admin")
    ])
    buttons.append([
        InlineKeyboardButton("⬅️ Admin Panel", callback_data="admin_back")
    ])

    await q.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# ADMIN COMMAND
# ============================================================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only.")
        return

    await update.message.reply_text(
        "👑 *PX ADMIN PANEL*",
        parse_mode="Markdown",
        reply_markup=admin_menu(),
    )


# ============================================================
# TEXT INPUT
# ============================================================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    save_user(user)

    # ---------------- USER PROMO CODE ----------------
    if context.user_data.get("awaiting_promo_code"):
        code = text.upper()
        row = db.execute(
            "SELECT * FROM promo_codes WHERE code=?", (code,)
        ).fetchone()

        if not row:
            await update.message.reply_text(
                "❌ Invalid promo code. Please try again.",
                reply_markup=back_menu(),
            )
            return

        already = db.execute(
            "SELECT 1 FROM promo_redemptions WHERE user_id=? AND code=?",
            (user.id, code),
        ).fetchone()
        if already:
            context.user_data.pop("awaiting_promo_code", None)
            await update.message.reply_text(
                "⚠️ You have already used this promo code.",
                reply_markup=main_menu(),
            )
            return

        db.execute(
            "INSERT INTO user_rewards(user_id,promo_members) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "promo_members = promo_members + excluded.promo_members",
            (user.id, row["members"]),
        )
        db.execute(
            "INSERT INTO promo_redemptions(user_id,code,redeemed_at) VALUES (?,?,?)",
            (user.id, code, now()),
        )
        db.commit()
        context.user_data.pop("awaiting_promo_code", None)

        buttons = []
        if row["button_text"] and row["button_url"]:
            buttons.append([
                InlineKeyboardButton(row["button_text"], url=row["button_url"])
            ])
        buttons.append([
            InlineKeyboardButton("⬅️ Main Menu", callback_data="back")
        ])

        await update.message.reply_text(
            f"🎉 *PROMO CODE APPLIED!*\n\n"
            f"🎁 Code: `{code}`\n"
            f"👥 Members Reward: *{row['members']}*\n\n"
            f"✅ Reward has been added to your account.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # ---------------- ADMIN BROADCAST ----------------
    if context.user_data.get("admin_broadcast"):
        if not is_admin(user.id):
            context.user_data.pop("admin_broadcast", None)
            return

        context.user_data.pop("admin_broadcast", None)
        users = db.execute("SELECT user_id FROM users").fetchall()

        sent = 0
        failed = 0
        for row in users:
            try:
                await context.bot.send_message(
                    row["user_id"],
                    text,
                )
                sent += 1
            except Exception:
                failed += 1

        await update.message.reply_text(
            f"✅ *BROADCAST COMPLETE*\n\n"
            f"📨 Sent: *{sent}*\n"
            f"❌ Failed: *{failed}*",
            parse_mode="Markdown",
            reply_markup=admin_menu(),
        )
        return

    # ---------------- ADMIN ADD CHANNEL ----------------
    if context.user_data.get("admin_add_channel"):
        if not is_admin(user.id):
            context.user_data.pop("admin_add_channel", None)
            return

        parts = [x.strip() for x in text.split("|")]
        if len(parts) != 3:
            await update.message.reply_text(
                "⚠️ Format:\n"
                "`-1001234567890 | Channel Name | https://t.me/ChannelUsername`",
                parse_mode="Markdown",
            )
            return

        try:
            chat_id = int(parts[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid numeric channel ID.")
            return

        name, link = parts[1][:100], parts[2][:200]

        if not extract_public_username(link):
            await update.message.reply_text(
                "❌ Only a public Telegram CHANNEL link is allowed.\n"
                "Example: `https://t.me/YourChannel`",
                parse_mode="Markdown",
            )
            return

        try:
            chat = await context.bot.get_chat(chat_id)
            if chat.type != "channel":
                await update.message.reply_text(
                    "❌ This chat ID is not a Telegram channel."
                )
                return
        except Exception:
            await update.message.reply_text(
                "❌ Bot cannot access this channel. Add the bot to the channel and try again."
            )
            return

        try:
            db.execute(
                "INSERT INTO required_channels(chat_id,name,link) VALUES (?,?,?)",
                (chat_id, name, link),
            )
            db.commit()
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ This channel is already added.")
            return

        context.user_data.pop("admin_add_channel", None)
        await update.message.reply_text(
            "✅ *CHANNEL ADDED*",
            parse_mode="Markdown",
            reply_markup=admin_menu(),
        )
        return

    # ---------------- ADMIN ADD ADMIN ----------------
    if context.user_data.get("admin_add_admin"):
        if not is_admin(user.id):
            context.user_data.pop("admin_add_admin", None)
            return

        try:
            new_admin = int(text)
        except ValueError:
            await update.message.reply_text("❌ Send a numeric Telegram User ID.")
            return

        db.execute(
            "INSERT OR IGNORE INTO admins(user_id) VALUES (?)",
            (new_admin,),
        )
        db.commit()
        context.user_data.pop("admin_add_admin", None)

        await update.message.reply_text(
            f"✅ *ADMIN ADDED*\n\n🆔 `{new_admin}`",
            parse_mode="Markdown",
            reply_markup=admin_menu(),
        )
        return

    # ---------------- ORDER: LINK ----------------
    if context.user_data.get("awaiting_order_link"):
        ok, error = await validate_channel_link(text, context)
        if not ok:
            await update.message.reply_text(
                error,
                parse_mode="Markdown",
            )
            return

        context.user_data.pop("awaiting_order_link", None)
        context.user_data["awaiting_order_members"] = text

        await update.message.reply_text(
            "✅ *CHANNEL LINK ACCEPTED*\n\n"
            "👥 Now tell me *how many members* you want to order.\n\n"
            "Example: `1000`",
            parse_mode="Markdown",
            reply_markup=back_menu(),
        )
        return

    # ---------------- ORDER: MEMBER COUNT ----------------
    if context.user_data.get("awaiting_order_members"):
        link = context.user_data["awaiting_order_members"]

        try:
            members = int(text.replace(",", "").strip())
            if members < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Please send a valid positive member number.\nExample: `1000`",
                parse_mode="Markdown",
            )
            return

        db.execute(
            "INSERT INTO orders(user_id,members,link,created_at) VALUES (?,?,?,?)",
            (user.id, members, link, now()),
        )

        # Reset member coins/balance to ZERO after a successful order.
        # This keeps the existing users table compatible with old databases.
        try:
            db.execute(
                "UPDATE users SET coins=0 WHERE user_id=?",
                (user.id,),
            )
        except sqlite3.OperationalError:
            # New version normally has no coins column; ignore it safely.
            pass

        db.commit()

        order_id = db.execute(
            "SELECT last_insert_rowid() x"
        ).fetchone()["x"]

        context.user_data.pop("awaiting_order_members", None)

        # Only the masked link is shown publicly in the order channel.
        order_text = (
            "🛒 *NEW MEMBER ORDER*\n\n"
            f"👤 *Member Name:* {user.first_name}\n"
            f"🆔 *Member ID:* `{user.id}`\n"
            f"📦 *Order ID:* `{order_id}`\n"
            f"👥 *Members:* `{members}`\n"
            f"🔗 *Link:* `{masked_order_link(link)}`"
        )

        try:
            await context.bot.send_message(
                ORDER_CHANNEL_ID,
                order_text,
                parse_mode="Markdown",
            )
        except Exception:
            await update.message.reply_text(
                "⚠️ Order saved, but I could not post it to the order channel.\n"
                "Make sure the bot is an admin there.",
                reply_markup=main_menu(),
            )
            return

        await update.message.reply_text(
            f"✅ *ORDER SUBMITTED*\n\n"
            f"📦 Order ID: `{order_id}`\n"
            f"👥 Members: *{members}*\n\n"
            f"⚡ Your order has been sent for processing.",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )
        return

    await update.message.reply_text(
        "✨ *MAIN MENU* ✨",
        parse_mode="Markdown",
        reply_markup=main_menu(),
    )


# ============================================================
# RUN
# ============================================================
def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is not configured. Set BOT_TOKEN as an environment variable."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))

    print("PX Members Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
