import asyncio
import json
import os
from telethon import TelegramClient, events, Button
from telethon.tl.types import Channel, Chat
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError
)

# -------------------------------------------------------------
# 1. Credentials Setup
# -------------------------------------------------------------
API_ID = 30849115
API_HASH = "1e56a12c7e496ef8e658d33c7418e60e"
BOT_TOKEN = "8599020367:AAExOJwJ3RzypQ8c8cYEMuoB5bCirOgVO4U"
OWNER_ID = 1190639187

DATA_FILE = "data.json"
user_states = {}
temp_chats_cache = {}

# Persistent sessions to prevent relogin
bot = TelegramClient("tele_bot_session", API_ID, API_HASH)
user = TelegramClient("tele_user_session", API_ID, API_HASH)

# -------------------------------------------------------------
# 2. Helper Functions
# -------------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "pairs": [],
        "status": "stopped",
        "forwarded_count": 0
    }

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def normalize_id(chat_id):
    """IDs ko standard format me clean kare"""
    if not chat_id:
        return ""
    c_str = str(chat_id).strip()
    if c_str.startswith("-100"):
        return c_str[4:]
    if c_str.startswith("-"):
        return c_str[1:]
    return c_str

def clean_chat_input(input_val):
    val = str(input_val).strip()
    if "t.me/" in val:
        val = val.split("t.me/")[-1].replace("+", "").replace("/", "")
    if val.startswith("-100") or val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
        return int(val)
    if not val.startswith("@") and not val.startswith("-"):
        return f"@{val}"
    return val

# -------------------------------------------------------------
# 3. Keyboards & Control Panel UI
# -------------------------------------------------------------
async def build_main_markup():
    data = load_data()
    status = data.get("status", "stopped")
    is_auth = await user.is_user_authorized()

    buttons = []
    if not is_auth:
        buttons.append([Button.inline("🔑 Login Telegram Account", data=b"btn_login")])
    else:
        if status == "running":
            buttons.append([Button.inline("⏹️ Stop Forwarding", data=b"btn_stop")])
        else:
            buttons.append([Button.inline("▶️ Start Forwarding", data=b"btn_start")])

        buttons.append([
            Button.inline("📋 Pick Joined Channel/Group", data=b"btn_pick_0"),
            Button.inline("✍️ Manual Add", data=b"btn_add_pair")
        ])
        buttons.append([
            Button.inline("🗑️ Remove Pair", data=b"btn_remove_pair"),
            Button.inline("🚪 Logout Account", data=b"btn_logout")
        ])

    buttons.append([Button.inline("🔄 Refresh Panel", data=b"btn_refresh")])
    return buttons

async def build_status_text():
    data = load_data()
    status_str = "🟢 Active" if data.get("status") == "running" else "🔴 Stopped"
    is_auth = await user.is_user_authorized()
    auth_str = "✅ Connected" if is_auth else "❌ Not Logged In"
    
    pairs = data.get("pairs", [])
    pairs_text = ""
    if not pairs:
        pairs_text = "❌ *कोई Pair सेट नहीं है*"
    else:
        for idx, p in enumerate(pairs, 1):
            name = p.get("source_title", p.get("source"))
            pairs_text += f"\n`{idx}.` **{name}** (`{p.get('source_id')}`) ➡️ `{p['target']}`"

    return (
        f"🤖 **Go Auto Forwarder Control Panel (Telethon Core)**\n\n"
        f"🔐 **Account Auth**: `{auth_str}`\n"
        f"⚡ **Forwarding Status**: `{status_str}`\n"
        f"📊 **Total Messages Forwarded**: `{data.get('forwarded_count', 0)}`\n\n"
        f"📋 **Active Pairs ({len(pairs)}):**\n{pairs_text}\n\n"
        f"👇 नीचे दिए गए बटनों से मैनेज करें:"
    )

# -------------------------------------------------------------
# 4. Background Auto-Forwarder (Catches ALL messages)
# -------------------------------------------------------------
@user.on(events.NewMessage)
async def global_auto_forward_listener(event):
    data_store = load_data()
    if data_store.get("status") != "running":
        return

    pairs = data_store.get("pairs", [])
    if not pairs or not event.chat_id:
        return

    incoming_chat_id = str(event.chat_id)
    incoming_norm = normalize_id(incoming_chat_id)
    chat_username = (getattr(event.chat, "username", None) or "").lower().replace("@", "")

    for pair in pairs:
        src_saved = str(pair.get("source_id", pair.get("source", ""))).strip().lower().replace("@", "")
        src_saved_norm = normalize_id(src_saved)
        tgt = clean_chat_input(pair.get("target"))

        # Match Source Chat
        is_match = False
        if incoming_norm and (incoming_norm == src_saved_norm):
            is_match = True
        elif chat_username and (chat_username == src_saved):
            is_match = True

        if is_match:
            sender_name = "Public/Admin/Bot"
            if event.sender:
                sender_name = getattr(event.sender, "first_name", None) or getattr(event.sender, "title", "Member")

            print(f"⚡ [Telethon Live] Catch Message from [{sender_name}] in Chat ID: {incoming_chat_id}")

            forwarded = False
            # 1. Direct Forward (Fast)
            try:
                await user.forward_messages(tgt, event.message)
                forwarded = True
                print(f"✅ FORWARDED to: {tgt}")
            except Exception as e:
                print(f"⚠️ Direct forward failed ({e}). Bypass running...")

            # 2. Content-Protection Bypass (Text + Media Re-upload)
            if not forwarded:
                file_path = None
                try:
                    caption = event.message.message or ""
                    if event.message.media:
                        file_path = await event.message.download_media()
                        await user.send_file(tgt, file_path, caption=caption)
                        forwarded = True
                    elif caption:
                        await user.send_message(tgt, caption)
                        forwarded = True
                    print(f"✅ BYPASS FORWARDED to: {tgt}")
                except Exception as err:
                    print(f"❌ Error sending to target ({tgt}): {err}")
                finally:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass

            if forwarded:
                data = load_data()
                data["forwarded_count"] = data.get("forwarded_count", 0) + 1
                save_data(data)

# -------------------------------------------------------------
# 5. Telegram Control Bot Commands & Callbacks
# -------------------------------------------------------------
@bot.on(events.NewMessage(from_users=OWNER_ID, pattern="/start"))
async def start_handler(event):
    user_states[event.chat_id] = None
    text = await build_status_text()
    buttons = await build_main_markup()
    await event.respond(text, buttons=buttons)

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != OWNER_ID:
        await event.answer("⛔ Access Denied!", alert=True)
        return

    chat_id = event.chat_id
    data_store = load_data()
    cb_data = event.data.decode("utf-8")

    if cb_data == "btn_refresh":
        text = await build_status_text()
        buttons = await build_main_markup()
        await event.edit(text, buttons=buttons)
        await event.answer("Refreshed!")

    elif cb_data == "btn_login":
        if await user.is_user_authorized():
            await event.answer("✅ Account already Logged In hai!", alert=True)
            return
        user_states[chat_id] = {"step": "AWAITING_PHONE"}
        await event.edit(
            "📱 **अपना Telegram Phone Number दर्ज करें:**\n\n(उदा: `+919876543210`)",
            buttons=[Button.inline("🔙 Cancel", data=b"btn_cancel")]
        )

    elif cb_data == "btn_logout":
        await user.log_out()
        data_store["status"] = "stopped"
        save_data(data_store)
        await event.answer("लॉगआउट हो गया!", alert=True)
        text = await build_status_text()
        buttons = await build_main_markup()
        await event.edit(text, buttons=buttons)

    elif cb_data.startswith("btn_pick_"):
        if not await user.is_user_authorized():
            await event.answer("⚠️ पहले अकाउंट लॉगिन करें!", alert=True)
            return

        page = int(cb_data.split("_")[-1])
        await event.answer("लोड हो रहा है...")

        dialogs = []
        async for dialog in user.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                dialogs.append(dialog)

        total_chats = len(dialogs)
        per_page = 6
        start_idx = page * per_page
        end_idx = start_idx + per_page
        current_chats = dialogs[start_idx:end_idx]

        temp_chats_cache[chat_id] = {str(d.id): d.name for d in current_chats}

        buttons = []
        for ch in current_chats:
            title = (ch.name or "Unknown")[:20]
            buttons.append([Button.inline(f"📢 {title}", data=f"sel_{ch.id}".encode("utf-8"))])

        nav_buttons = []
        if page > 0:
            nav_buttons.append(Button.inline("⬅️ Prev", data=f"btn_pick_{page-1}".encode("utf-8")))
        if end_idx < total_chats:
            nav_buttons.append(Button.inline("Next ➡️", data=f"btn_pick_{page+1}".encode("utf-8")))

        if nav_buttons:
            buttons.append(nav_buttons)
        buttons.append([Button.inline("🔙 Cancel", data=b"btn_cancel")])

        await event.edit(
            f"📋 **Source चुनें (Page {page+1}):**",
            buttons=buttons
        )

    elif cb_data.startswith("sel_"):
        src_id = cb_data.split("_")[1]
        cache = temp_chats_cache.get(chat_id, {})
        src_title = cache.get(src_id, src_id)

        user_states[chat_id] = {
            "step": "AWAITING_PAIR_TARGET",
            "source_raw": src_title,
            "source_id": src_id
        }
        await event.edit(
            f"✅ **Source चुना गया:** `{src_title}`\n`ID: {src_id}`\n\n"
            "📤 **Target दर्ज करें:**\n"
            "जहाँ मैसेज भेजना है उसका **Username (`@username`)** या **ID** भेजें:",
            buttons=[Button.inline("🔙 Cancel", data=b"btn_cancel")]
        )

    elif cb_data == "btn_add_pair":
        if not await user.is_user_authorized():
            await event.answer("⚠️ पहले अकाउंट लॉगिन करें!", alert=True)
            return
        user_states[chat_id] = {"step": "AWAITING_PAIR_SOURCE"}
        await event.edit(
            "📥 **Source Username, ID या Link भेजें:**",
            buttons=[Button.inline("🔙 Cancel", data=b"btn_cancel")]
        )

    elif cb_data == "btn_remove_pair":
        pairs = data_store.get("pairs", [])
        if not pairs:
            await event.answer("⚠️ कोई Pair नहीं है!", alert=True)
            return

        buttons = []
        for idx, p in enumerate(pairs, 1):
            name = (p.get("source_title", p.get("source")))[:15]
            buttons.append([Button.inline(f"❌ {name} -> {p['target']}", data=f"del_{idx-1}".encode("utf-8"))])
        buttons.append([Button.inline("🔙 Cancel", data=b"btn_cancel")])

        await event.edit("🗑️ **हटाने के लिए Pair चुनें:**", buttons=buttons)

    elif cb_data.startswith("del_"):
        pair_idx = int(cb_data.split("_")[1])
        pairs = data_store.get("pairs", [])
        if 0 <= pair_idx < len(pairs):
            pairs.pop(pair_idx)
            data_store["pairs"] = pairs
            save_data(data_store)
            await event.answer("✅ Pair हटाया गया!", alert=True)
        text = await build_status_text()
        buttons = await build_main_markup()
        await event.edit(text, buttons=buttons)

    elif cb_data == "btn_start":
        if not await user.is_user_authorized():
            await event.answer("⚠️ पहले लॉगिन करें!", alert=True)
            return
        if not data_store.get("pairs"):
            await event.answer("⚠️ पहले Pair जोड़ें!", alert=True)
            return
        data_store["status"] = "running"
        save_data(data_store)
        text = await build_status_text()
        buttons = await build_main_markup()
        await event.edit(text, buttons=buttons)
        await event.answer("🟢 Forwarding Started!", alert=True)

    elif cb_data == "btn_stop":
        data_store["status"] = "stopped"
        save_data(data_store)
        text = await build_status_text()
        buttons = await build_main_markup()
        await event.edit(text, buttons=buttons)
        await event.answer("🔴 Forwarding Stopped!", alert=True)

    elif cb_data == "btn_cancel":
        user_states[chat_id] = None
        text = await build_status_text()
        buttons = await build_main_markup()
        await event.edit(text, buttons=buttons)

# -------------------------------------------------------------
# 6. Inputs Handler (OTP, Phone, Pairs)
# -------------------------------------------------------------
@bot.on(events.NewMessage(from_users=OWNER_ID))
async def inputs_handler(event):
    text = event.raw_text.strip()
    if text.startswith("/"):
        return

    chat_id = event.chat_id
    state = user_states.get(chat_id)
    data_store = load_data()

    if not state:
        return

    step = state.get("step")

    if step == "AWAITING_PHONE":
        state["phone"] = text
        await event.respond("⏳ OTP भेजा जा रहा है...")
        try:
            res = await user.send_code_request(text)
            state["phone_code_hash"] = res.phone_code_hash
            state["step"] = "AWAITING_OTP"
            await event.respond("📩 **OTP दर्ज करें (उदा: `1 2 3 4 5`):**")
        except Exception as e:
            user_states[chat_id] = None
            await event.respond(f"❌ त्रुटि: `{e}`\n/start करें।")

    elif step == "AWAITING_OTP":
        otp = text.replace(" ", "").replace("-", "")
        try:
            await user.sign_in(
                phone=state["phone"],
                code=otp,
                phone_code_hash=state["phone_code_hash"]
            )
            user_states[chat_id] = None
            text_status = await build_status_text()
            buttons = await build_main_markup()
            await event.respond("🎉 **लॉगिन सफल हो गया!**", buttons=buttons)
        except SessionPasswordNeededError:
            state["step"] = "AWAITING_2FA"
            await event.respond("🔐 **2-Step Verification Password दर्ज करें:**")
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await event.respond("❌ गलत OTP! दोबारा चेक करके भेजें:")
        except Exception as e:
            user_states[chat_id] = None
            await event.respond(f"❌ लॉगिन त्रुटि: `{e}`")

    elif step == "AWAITING_2FA":
        try:
            await user.sign_in(password=text)
            user_states[chat_id] = None
            text_status = await build_status_text()
            buttons = await build_main_markup()
            await event.respond("🎉 **2FA वेरिफिकेशन सफल!**", buttons=buttons)
        except PasswordHashInvalidError:
            await event.respond("❌ गलत पासवर्ड! दोबारा भेजें:")
        except Exception as e:
            user_states[chat_id] = None
            await event.respond(f"❌ त्रुटि: `{e}`")

    elif step == "AWAITING_PAIR_SOURCE":
        cleaned_src = clean_chat_input(text)
        resolved_id = str(cleaned_src)
        title = text

        try:
            entity = await user.get_entity(cleaned_src)
            resolved_id = str(entity.id)
            title = getattr(entity, "title", None) or getattr(entity, "first_name", text)
        except Exception:
            pass

        user_states[chat_id] = {
            "step": "AWAITING_PAIR_TARGET",
            "source_raw": text,
            "source_title": title,
            "source_id": resolved_id
        }
        await event.respond(
            f"✅ **Source Chat:** `{title}` (ID: `{resolved_id}`)\n\n"
            "📤 **Target दर्ज करें (Username `@channel` या ID):**"
        )

    elif step == "AWAITING_PAIR_TARGET":
        source_raw = state.get("source_raw")
        source_title = state.get("source_title", source_raw)
        source_id = state.get("source_id")
        target_chat = clean_chat_input(text)

        pairs = data_store.get("pairs", [])
        pairs.append({
            "source": source_raw,
            "source_title": source_title,
            "source_id": source_id,
            "target": str(target_chat)
        })
        data_store["pairs"] = pairs
        save_data(data_store)

        user_states[chat_id] = None
        text_status = await build_status_text()
        buttons = await build_main_markup()
        await event.respond(
            f"🎉 **Pair जुड़ गया!**\n\n`{source_title}` ➡️ `{target_chat}`",
            buttons=buttons
        )

# -------------------------------------------------------------
# 7. Main Entry Point
# -------------------------------------------------------------
async def main():
    print("🚀 Auto-Forwarder Bot शुरू हो रहा है...")
    await user.connect()
    await bot.start(bot_token=BOT_TOKEN)
    print("🔥 TELETHON CORE READY: All member messages will be forwarded 100%!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())