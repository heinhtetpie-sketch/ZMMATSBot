import asyncio
import json
import os
import re
import time
import secrets
from datetime import datetime
from flask import Flask
from threading import Thread
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import (
    SessionPasswordNeededError,
    UserAlreadyParticipantError,
    FloodWaitError
)

# ================= သင်၏ API နှင့် ADMIN အချက်အလက်များ =================
API_ID = 30424297                  
API_HASH = '815196d0e53b30fd4b33a98210442c98'    
BOT_TOKEN = '9111222051:AAGea2BYZ4xuc51wn6ORkCpqxOzNhsxmIoE'
ADMIN_IDS = [6449880399]
# ====================================================================

app = Flask(__name__)
@app.route('/')
def home():
    return "Auto-Share Bot is Running 24/7!"

def run_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host="0.0.0.0", port=port)

DATA_FILE = 'users_data.json'

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"_vip_keys": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

users_db = load_data()
if "_vip_keys" not in users_db:
    users_db["_vip_keys"] = {}

running_tasks = {}
login_states = {}
input_states = {}

bot = TelegramClient('controller_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def get_user(uid):
    uid = str(uid)
    if uid not in users_db:
        users_db[uid] = {"session": None, "post_link": None, "groups": [], "interval": 30, "vip_until": 0}
        save_data(users_db)
    else:
        users_db[uid].setdefault("vip_until", 0)
    return users_db[uid]

def is_admin(uid):
    return int(uid) in ADMIN_IDS

def is_vip(uid):
    if is_admin(uid):
        return True
    user = get_user(uid)
    return user.get("vip_until", 0) > time.time()

def format_expiry(uid):
    if is_admin(uid):
        return "♾️ Lifetime (Admin)"
    user = get_user(uid)
    exp = user.get("vip_until", 0)
    if exp <= time.time():
        return "❌ သက်တမ်းမရှိပါ"
    dt = datetime.fromtimestamp(exp).strftime('%Y-%m-%d %I:%M %p')
    return f"✅ {dt} အထိ"

def parse_post_link(link):
    pattern = r"https?://t\.me/(?:c/)?([^/\s]+)/(\d+)"
    match = re.search(pattern, link)
    if not match:
        return None, None
    chat, msg_id = match.group(1), int(match.group(2))
    if chat.isdigit():
        chat = int("-100" + chat)
    return chat, msg_id

def extract_group_links(text):
    pattern = r"(?:https?://)?t\.me/(?:\+[a-zA-Z0-9_-]+|joinchat/[a-zA-Z0-9_-]+|[a-zA-Z0-9_]+)|@[a-zA-Z0-9_]+"
    matches = re.findall(pattern, text)
    cleaned = []
    for m in matches:
        if m.startswith("t.me/"):
            m = "https://" + m
        cleaned.append(m)
    return cleaned

async def resolve_and_join_group(client, link):
    try:
        if '+' in link or 'joinchat/' in link:
            invite_hash = link.split('/')[-1].replace('+', '').split('?')[0]
            try:
                updates = await client(ImportChatInviteRequest(invite_hash))
                return updates.chats[0]
            except UserAlreadyParticipantError:
                return await client.get_entity(link)
        else:
            username = link.split('/')[-1].split('?')[0].replace('@', '')
            try:
                await client(JoinChannelRequest(username))
            except Exception:
                pass
            return await client.get_entity(username)
    except Exception:
        return None

# Worker (တိုက်ရိုက် Forward လုပ်မည့် စနစ်အသစ်)
async def user_share_worker(uid):
    user = get_user(uid)
    client = TelegramClient(StringSession(user["session"]), API_ID, API_HASH)
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await bot.send_message(int(uid), "❌ Session သက်တမ်းကုန်သွားပါပြီ။ Login ပြန်ဝင်ပါ။")
            running_tasks.pop(uid, None)
            return
    except Exception as e:
        await bot.send_message(int(uid), f"❌ ချိတ်ဆက်မှု အမှား: {e}")
        running_tasks.pop(uid, None)
        return

    source_chat, msg_id = parse_post_link(user["post_link"])
    if not source_chat:
        await bot.send_message(int(uid), "❌ Post Link ပုံစံ မှားနေပါသည်။")
        running_tasks.pop(uid, None)
        await client.disconnect()
        return

    await bot.send_message(int(uid), f"🔄 Group ({len(user['groups'])}) ခုသို့ ချိတ်ဆက်နေပါသည်...")

    target_chats = []
    for link in user["groups"]:
        entity = await resolve_and_join_group(client, link)
        if entity:
            target_chats.append(entity)
        await asyncio.sleep(1)

    if not target_chats:
        await bot.send_message(int(uid), "⚠️ မည်သည့် Group ကိုမှ ချိတ်ဆက်မရပါ။")
        running_tasks.pop(uid, None)
        await client.disconnect()
        return

    await bot.send_message(
        int(uid),
        f"✅ အားလုံး အသင့်ဖြစ်ပါပြီ။\n"
        f"🚀 စက္ကန့် ({user['interval']}) တိုင်း စတင် Share နေပါပြီ!"
    )

    try:
        while True:
            if not is_vip(uid):
                break

            try:
                # မူရင်း Post ကို ယူမည်
                original_msg = await client.get_messages(source_chat, ids=msg_id)
                if original_msg:
                    for chat in target_chats:
                        try:
                            # Forward လုပ်မည်
                            await client.forward_messages(chat, original_msg)
                            await asyncio.sleep(2)
                        except FloodWaitError as e:
                            await asyncio.sleep(e.seconds)
                        except Exception:
                            pass
            except Exception:
                pass

            await asyncio.sleep(user["interval"])

    except asyncio.CancelledError:
        pass
    finally:
        running_tasks.pop(uid, None)
        try:
            await client.disconnect()
        except Exception:
            pass

# ================= UI အပိုင်း =================
def get_main_menu_text(uid):
    return (
        "👋 **Auto-Share Bot မှ ကြိုဆိုပါသည်**\n\n"
        f"👤 **သင့် ID:** `{uid}`\n"
        f"💎 **VIP သက်တမ်း:** {format_expiry(uid)}\n\n"
        "👇 **အောက်ပါခလုတ်များကိုနှိပ်၍ အသုံးပြုနိုင်ပါသည်**"
    )

def get_main_menu_buttons():
    return [
        [Button.inline("1️⃣ အကောင့်ချိတ်ရန်", b"m_login"), Button.inline("2️⃣ Post Link ထည့်ရန်", b"m_post")],
        [Button.inline("3️⃣ Group ထည့်ရန်", b"m_group"), Button.inline("4️⃣ Group ရှင်းရန်", b"m_clear")],
        [Button.inline("5️⃣ အချိန်သတ်မှတ်ရန်", b"m_time"), Button.inline("6️⃣ Status စစ်ရန်", b"m_status")],
        [Button.inline("7️⃣ Share စတင်ရန်", b"m_start"), Button.inline("8️⃣ Share ရပ်တန့်ရန်", b"m_stop")]
    ]

def get_reply_keyboard(uid):
    if is_admin(uid):
        return [
            [Button.text("🏠 Main Menu", resize=True), Button.text("👑 Admin Commands", resize=True)],
            [Button.text("🆘 Help", resize=True)]
        ]
    return [
        [Button.text("🏠 Main Menu", resize=True)],
        [Button.text("🆘 Help", resize=True)]
    ]

back_btn = [[Button.inline("🔙 နောက်သို့ပြန်သွားမည်", b"m_back")]]

@bot.on(events.NewMessage(pattern='/start|/menu'))
async def start_cmd(event):
    uid = str(event.sender_id)
    get_user(uid)
    input_states.pop(uid, None)
    login_states.pop(uid, None)
    await event.respond("အောက်ခြေရှိ Menu များကို ရွေးချယ်နိုင်ပါသည် 👇", buttons=get_reply_keyboard(uid))
    await event.respond(get_main_menu_text(uid), buttons=get_main_menu_buttons())

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    uid = str(event.sender_id)
    data = event.data.decode('utf-8')
    
    if data == "m_back":
        input_states.pop(uid, None)
        login_states.pop(uid, None)
        await event.edit(get_main_menu_text(uid), buttons=get_main_menu_buttons())
        return

    if not is_vip(uid):
        await event.answer("🔒 VIP သက်တမ်း မရှိသေးပါ။", alert=True)
        return

    if data == "m_login":
        login_states[uid] = {"step": "phone"}
        await event.edit("📱 Telegram ဖုန်းနံပါတ်ကို ပို့ပေးပါ။ (ဥပမာ - +95912345678)", buttons=back_btn)
    elif data == "m_post":
        input_states[uid] = "post"
        await event.edit("📌 Post Link ကို ပေးပို့ပါ။", buttons=back_btn)
    elif data == "m_group":
        input_states[uid] = "group"
        await event.edit("📌 Group Link များကို ဆက်တိုက် ပို့နိုင်ပါသည်။ ပြီးလျှင် နောက်သို့ပြန်သွားမည် ကိုနှိပ်ပါ။", buttons=back_btn)
    elif data == "m_clear":
        user = get_user(uid)
        user["groups"] = []
        save_data(users_db)
        await event.edit("🗑️ Group စာရင်း ရှင်းပြီးပါပြီ။", buttons=back_btn)
    elif data == "m_time":
        input_states[uid] = "time"
        await event.edit("⏱️ အချိန်ခြားနားချက်ကို စက္ကန့်ဖြင့် ပို့ပါ။ (ဥပမာ - 30)", buttons=back_btn)
    elif data == "m_status":
        user = get_user(uid)
        is_logged = "✅ ချိတ်ဆက်ပြီး" if user["session"] else "❌ မချိတ်ရသေးပါ"
        task = running_tasks.get(uid)
        is_running = "🟢 Run နေသည်" if (task and not task.done()) else "🔴 ရပ်ထားသည်"
        groups_list = "\n".join([f"  {i+1}. {g}" for i, g in enumerate(user["groups"])]) if user["groups"] else "  မရှိသေးပါ"
        msg = (
            f"📊 **Status**\n\n"
            f"• **အကောင့်:** {is_logged}\n"
            f"• **Post Link:** {user['post_link'] or 'မရှိသေးပါ'}\n"
            f"• **အချိန်:** {user['interval']} စက္ကန့်\n"
            f"• **အခြေအနေ:** {is_running}\n"
            f"• **Group ({len(user['groups'])}):**\n{groups_list}"
        )
        await event.edit(msg, buttons=back_btn)
    elif data == "m_start":
        user = get_user(uid)
        if not user["session"] or not user["post_link"] or not user["groups"]:
            await event.answer("⚠️ အချက်အလက် မပြည့်စုံပါ!", alert=True)
            return
        if uid in running_tasks and not running_tasks[uid].done():
            running_tasks[uid].cancel()
        
        task = asyncio.create_task(user_share_worker(uid))
        running_tasks[uid] = task
        await event.edit("🚀 Auto-Share စတင်နေပါပြီ...", buttons=back_btn)
    elif data == "m_stop":
        if uid in running_tasks:
            running_tasks[uid].cancel()
            running_tasks.pop(uid, None)
            await event.edit("🛑 ရပ်တန့်လိုက်ပါပြီ။", buttons=back_btn)
        else:
            await event.answer("ℹ️ Run နေသည် မရှိပါ။", alert=True)

@bot.on(events.NewMessage)
async def handle_general_inputs(event):
    uid = str(event.sender_id)
    text = event.raw_text.strip()
    
    if text.startswith('/'):
        input_states.pop(uid, None)
        return

    if text == "🏠 Main Menu":
        input_states.pop(uid, None)
        login_states.pop(uid, None)
        await event.respond(get_main_menu_text(uid), buttons=get_main_menu_buttons())
        return
    if text == "👑 Admin Commands" and is_admin(uid):
        await event.respond("👑 Admin Commands: `/genkey`, `/addvip`, `/removevip`, `/vips`")
        return
    if text == "🆘 Help":
        await event.respond("👨‍💻 Admin သို့ ဆက်သွယ်ရန် 👉 @momogaming8")
        return

    if uid in login_states:
        state = login_states[uid]
        if state["step"] == "phone":
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            sent = await client.send_code_request(text)
            state["client"], state["phone"], state["hash"], state["step"] = client, text, sent.phone_code_hash, "otp"
            await event.respond("📩 OTP ကုဒ်ကို ပို့ပါ။ (ဥပမာ - 1 2 3 4 5)", buttons=back_btn)
        elif state["step"] == "otp":
            client = state["client"]
            await client.sign_in(state["phone"], text.replace(" ", ""), phone_code_hash=state["hash"])
            user = get_user(uid)
            user["session"] = client.session.save()
            save_data(users_db)
            await client.disconnect()
            login_states.pop(uid, None)
            await event.respond("✅ အကောင့် ချိတ်ဆက်ပြီး!", buttons=back_btn)
        return

    if uid in input_states:
        action = input_states[uid]
        user = get_user(uid)
        if action == "post":
            user["post_link"] = text
            save_data(users_db)
            input_states.pop(uid, None)
            await event.respond("✅ Post Link ထည့်ပြီးပါပြီ။", buttons=back_btn)
        elif action == "group":
            links = extract_group_links(text)
            for l in links:
                if l not in user["groups"]:
                    user["groups"].append(l)
            save_data(users_db)
            await event.respond(f"✅ Group ပေါင်း ({len(user['groups'])}) ခု ရှိပါပြီ။ ဆက်ထည့်နိုင်ပါသည်။", buttons=back_btn)
        elif action == "time":
            if text.isdigit():
                user["interval"] = int(text)
                save_data(users_db)
                input_states.pop(uid, None)
                await event.respond(f"⏱️ {text} စက္ကန့် သတ်မှတ်ပြီးပါပြီ။", buttons=back_btn)
        return

@bot.on(events.NewMessage(pattern=r'/redeem (.+)'))
async def redeem_cmd(event):
    uid, key = str(event.sender_id), event.pattern_match.group(1).strip()
    keys_pool = users_db.get("_vip_keys", {})
    if key not in keys_pool:
        await event.respond("❌ Key မှားနေပါသည်။")
        return
    days = keys_pool.pop(key)
    user = get_user(uid)
    user["vip_until"] = max(time.time(), user.get("vip_until", 0)) + (days * 86400)
    save_data(users_db)
    await event.respond(f"🎉 VIP သက်တမ်း တိုးပြီးပါပြီ ({days} ရက်)!")

@bot.on(events.NewMessage(pattern=r'/genkey (\d+)(?: (\d+))?'))
async def genkey_cmd(event):
    if not is_admin(event.sender_id): return
    days, count = int(event.pattern_match.group(1)), int(event.pattern_match.group(2) or 1)
    gen = [f"`/redeem VIP-{secrets.token_hex(4).upper()}`" for _ in range(min(count, 20))]
    for g in gen:
        k = g.split('`')[1].replace('/redeem ', '')
        users_db["_vip_keys"][k] = days
    save_data(users_db)
    await event.respond(f"🔑 Key များ:\n\n" + "\n".join(gen))

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    bot.run_until_disconnected()
