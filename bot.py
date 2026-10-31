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
ADMIN_IDS = [6449880399] # Admin ID အမှန်
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
    pattern = r"https?://t\.me/(?:c/)?([^/]+)/(\d+)"
    match = re.search(pattern, link)
    if not match:
        return None, None
    chat, msg_id = match.group(1), int(match.group(2))
    if chat.isdigit():
        chat = int("-100" + chat)
    return chat, msg_id

async def user_share_worker(uid):
    user = get_user(uid)
    client = TelegramClient(StringSession(user["session"]), API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        await bot.send_message(int(uid), "❌ Session သက်တမ်းကုန်သွားပါပြီ။ Login ပြန်ဝင်ပါ။")
        running_tasks.pop(uid, None)
        return

    source_chat, msg_id = parse_post_link(user["post_link"])
    if not source_chat:
        await bot.send_message(int(uid), "❌ Post Link ပုံစံ မှားနေပါသည်။ ပြန်ထည့်ပါ။")
        running_tasks.pop(uid, None)
        await client.disconnect()
        return

    await bot.send_message(int(uid), "🔄 Group များကို ချိတ်ဆက်နေပါသည်...")
    
    target_chats = []
    for link in user["groups"]:
        try:
            entity = await client.get_entity(link)
            target_chats.append(entity)
        except Exception:
            pass
        await asyncio.sleep(2)

    if not target_chats:
        await bot.send_message(int(uid), "⚠️ မည်သည့် Group ကိုမှ ချိတ်ဆက်မရပါ။ Link များ ပြန်စစ်ပါ။")
        running_tasks.pop(uid, None)
        await client.disconnect()
        return

    await bot.send_message(
        int(uid),
        f"✅ Group ({len(target_chats)}) ခု အသင့်ဖြစ်ပါပြီ။\n"
        f"🚀 စက္ကန့် ({user['interval']}) တိုင်း (Premium Emoji Forward) ဖြင့် စတင်နေပါပြီ!"
    )

    try:
        while True:
            if not is_vip(uid):
                await bot.send_message(int(uid), "⚠️ VIP သက်တမ်း ကုန်ဆုံးသဖြင့် ရပ်တန့်လိုက်ပါပြီ။")
                running_tasks.pop(uid, None)
                await client.disconnect()
                return
            try:
                original_msg = await client.get_messages(source_chat, ids=msg_id)
            except Exception:
                await asyncio.sleep(user["interval"])
                continue

            if original_msg:
                for chat in target_chats:
                    try:
                        await client.forward_messages(chat, original_msg)
                        await asyncio.sleep(2)
                    except FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception:
                        pass
            await asyncio.sleep(user["interval"])
    except asyncio.CancelledError:
        await client.disconnect()

# ================= UI အပိုင်း (Buttons & Menus) =================
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
    # Keyboard အသေ (အောက်ခြေ Menu)
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
    await event.respond("အောက်ခြေရှိ Menu များကို ရွေးချယ်နိုင်ပါသည် 👇", buttons=get_reply_keyboard(uid))
    await event.respond(get_main_menu_text(uid), buttons=get_main_menu_buttons())

async def check_vip_access_cb(event):
    uid = str(event.sender_id)
    if not is_vip(uid):
        await event.answer("🔒 VIP သက်တမ်း မရှိသေးပါ။", alert=True)
        return False
    return True

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    uid = str(event.sender_id)
    data = event.data.decode('utf-8')
    
    if data == "m_back":
        input_states.pop(uid, None)
        login_states.pop(uid, None)
        await event.edit(get_main_menu_text(uid), buttons=get_main_menu_buttons())
        return

    if not await check_vip_access_cb(event):
        return

    if data == "m_login":
        login_states[uid] = {"step": "phone"}
        await event.edit("📱 ချိတ်ဆက်လိုသော Telegram ဖုန်းနံပါတ်ကို Country Code ဖြင့် ပို့ပေးပါ။\n(ဥပမာ - +95912345678)", buttons=back_btn)
    elif data == "m_post":
        input_states[uid] = "post"
        await event.edit("📌 Share လုပ်လိုသော Post Link ကို ပေးပို့ပါ။", buttons=back_btn)
    elif data == "m_group":
        input_states[uid] = "group"
        await event.edit("📌 Share ထည့်လိုသော Group Link ကို ပေးပို့ပါ။\n(ဥပမာ - https://t.me/mygroup)", buttons=back_btn)
    elif data == "m_clear":
        user = get_user(uid)
        user["groups"] = []
        save_data(users_db)
        await event.edit("🗑️ Group စာရင်းအားလုံးကို ရှင်းလင်းပြီးပါပြီ။", buttons=back_btn)
    elif data == "m_time":
        input_states[uid] = "time"
        await event.edit("⏱️ Share မည့် အချိန်ခြားနားချက်ကို စက္ကန့်ဖြင့် ပေးပို့ပါ။\n(ဥပမာ - 30)", buttons=back_btn)
    elif data == "m_status":
        user = get_user(uid)
        is_logged = "✅ ချိတ်ဆက်ပြီး" if user["session"] else "❌ မချိတ်ရသေးပါ"
        is_running = "🟢 Run နေသည်" if uid in running_tasks else "🔴 ရပ်ထားသည်"
        msg = (
            f"📊 **သင်၏ လက်ရှိ အချက်အလက်များ**\n\n"
            f"• **အကောင့်:** {is_logged}\n"
            f"• **Post Link:** {user['post_link'] or 'မရှိသေးပါ'}\n"
            f"• **Group:** {len(user['groups'])} ခု\n"
            f"• **အချိန်:** {user['interval']} စက္ကန့်\n"
            f"• **အခြေအနေ:** {is_running}"
        )
        await event.edit(msg, buttons=back_btn)
    elif data == "m_start":
        user = get_user(uid)
        if not user["session"]:
            await event.answer("⚠️ အကောင့် မချိတ်ရသေးပါ။ Login အရင်ဝင်ပါ။", alert=True)
            return
        if not user["post_link"] or not user["groups"]:
            await event.answer("⚠️ Post Link (သို့) Group မထည့်ရသေးပါ။", alert=True)
            return
        if uid in running_tasks:
            await event.answer("⚠️ Auto-Share လုပ်နေပြီးသားဖြစ်ပါသည်။", alert=True)
            return
        
        task = asyncio.create_task(user_share_worker(uid))
        running_tasks[uid] = task
        await event.edit("🚀 Auto-Share စတင်နေပါပြီ...", buttons=back_btn)
    elif data == "m_stop":
        if uid in running_tasks:
            running_tasks[uid].cancel()
            running_tasks.pop(uid, None)
            await event.edit("🛑 Auto-Share ကို ရပ်တန့်လိုက်ပါပြီ။", buttons=back_btn)
        else:
            await event.answer("ℹ️ Run နေသော Auto-Share မရှိပါ။", alert=True)

@bot.on(events.NewMessage)
async def handle_general_inputs(event):
    uid = str(event.sender_id)
    text = event.raw_text.strip()
    
    if text.startswith('/'):
        input_states.pop(uid, None)
        return

    # ================= Reply Keyboard (Menu အသေ) Handlers =================
    if text == "🏠 Main Menu":
        input_states.pop(uid, None)
        login_states.pop(uid, None)
        await event.respond(get_main_menu_text(uid), buttons=get_main_menu_buttons())
        return
        
    if text == "👑 Admin Commands" and is_admin(uid):
        msg = (
            "👑 **Admin Commands (စာရိုက်၍ သုံးပါ)**\n\n"
            "🔑 `/genkey <ရက်> <အရေအတွက်>` - Key ထုတ်ရန်\n"
            "➕ `/addvip <user_id> <ရက်>` - VIP ပေးရန်\n"
            "➖ `/removevip <user_id>` - VIP ဖြုတ်ရန်\n"
            "📋 `/vips` - VIP စာရင်းကြည့်ရန်\n"
            "🎁 `/redeem <KEY>` - မိမိကိုယ်တိုင် VIP သွင်းရန်"
        )
        await event.respond(msg)
        return

    if text == "🆘 Help":
        await event.respond("👨‍💻 **အကူအညီလိုအပ်ပါက Admin သို့ ဆက်သွယ်ပါ**\n👉 @momogaming8")
        return
    # ====================================================================

    # Login Logic
    if uid in login_states:
        state = login_states[uid]
        if state["step"] == "phone":
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            try:
                sent = await client.send_code_request(text)
                state["client"], state["phone"], state["hash"], state["step"] = client, text, sent.phone_code_hash, "otp"
                await event.respond("📩 Telegram သို့ ရောက်လာသော OTP ကုဒ်ကို Space ခြား၍ ပို့ပါ။ (ဥပမာ - 1 2 3 4 5)", buttons=back_btn)
            except Exception as e:
                await client.disconnect()
                login_states.pop(uid, None)
                await event.respond(f"❌ အမှားရှိပါသည်: {e}", buttons=back_btn)
        elif state["step"] == "otp":
            otp_code = text.replace(" ", "")
            client = state["client"]
            try:
                await client.sign_in(state["phone"], otp_code, phone_code_hash=state["hash"])
                user = get_user(uid)
                user["session"] = client.session.save()
                save_data(users_db)
                await client.disconnect()
                login_states.pop(uid, None)
                await event.respond("✅ အကောင့် ချိတ်ဆက်မှု အောင်မြင်ပါပြီ!", buttons=back_btn)
            except SessionPasswordNeededError:
                state["step"] = "2fa"
                await event.respond("🔐 Two-Step (2FA) Password ရိုက်ပို့ပေးပါ-", buttons=back_btn)
            except Exception as e:
                await client.disconnect()
                login_states.pop(uid, None)
                await event.respond(f"❌ OTP မှားယွင်းနေပါသည်: {e}", buttons=back_btn)
        elif state["step"] == "2fa":
            client = state["client"]
            try:
                await client.sign_in(password=text)
                user = get_user(uid)
                user["session"] = client.session.save()
                save_data(users_db)
                await client.disconnect()
                login_states.pop(uid, None)
                await event.respond("✅ 2FA အတည်ပြုပြီး အကောင့်ချိတ်ဆက်မှု အောင်မြင်ပါပြီ!", buttons=back_btn)
            except Exception as e:
                await client.disconnect()
                login_states.pop(uid, None)
                await event.respond(f"❌ Password မှားယွင်းနေပါသည်: {e}", buttons=back_btn)
        return

    # Settings Input Logic
    if uid in input_states:
        action = input_states[uid]
        user = get_user(uid)
        
        if action == "post":
            chat, msg_id = parse_post_link(text)
            if not chat:
                await event.respond("❌ Post Link ပုံစံ မှားနေပါသည်။ ပြန်ပို့ပါ။", buttons=back_btn)
                return
            user["post_link"] = text
            save_data(users_db)
            input_states.pop(uid, None)
            await event.respond("✅ Post Link သတ်မှတ်ပြီးပါပြီ။", buttons=back_btn)
            
        elif action == "group":
            if 't.me/' not in text:
                await event.respond("❌ Group Link ပုံစံ မှားနေပါသည်။", buttons=back_btn)
                return
            if text not in user["groups"]:
                user["groups"].append(text)
                save_data(users_db)
            input_states.pop(uid, None)
            await event.respond(f"✅ Group Link ထည့်ပြီးပါပြီ။ (စုစုပေါင်း {len(user['groups'])} ခု)", buttons=back_btn)
            
        elif action == "time":
            if not text.isdigit():
                await event.respond("❌ ဂဏန်းသာ ရိုက်ထည့်ပါ။ (ဥပမာ - 30)", buttons=back_btn)
                return
            sec = int(text)
            if sec < 15:
                await event.respond("⚠️ အနည်းဆုံး 15 စက္ကန့် သတ်မှတ်ပါ။ ပြန်ထည့်ပါ။", buttons=back_btn)
                return
            user["interval"] = sec
            save_data(users_db)
            input_states.pop(uid, None)
            await event.respond(f"⏱️ အချိန် ({sec}) စက္ကန့် သတ်မှတ်ပြီးပါပြီ။", buttons=back_btn)
        return

@bot.on(events.NewMessage(pattern=r'/redeem (.+)'))
async def redeem_cmd(event):
    uid, key = str(event.sender_id), event.pattern_match.group(1).strip()
    keys_pool = users_db.get("_vip_keys", {})
    if key not in keys_pool:
        await event.respond("❌ VIP Key မှားယွင်းနေပါသည်။")
        return
    days = keys_pool.pop(key)
    user = get_user(uid)
    user["vip_until"] = max(time.time(), user.get("vip_until", 0)) + (days * 86400)
    save_data(users_db)
    await event.respond(f"🎉 **VIP အောင်မြင်ပါသည်!**\n➕ ထပ်တိုးရက်: {days} ရက်")

@bot.on(events.NewMessage(pattern=r'/genkey (\d+)(?: (\d+))?'))
async def genkey_cmd(event):
    uid = str(event.sender_id)
    if not is_admin(uid): return
    days, count = int(event.pattern_match.group(1)), int(event.pattern_match.group(2) or 1)
    count = min(count, 20)
    generated = []
    for _ in range(count):
        k = f"VIP-{secrets.token_hex(4).upper()}"
        users_db["_vip_keys"][k] = days
        generated.append(f"`/redeem {k}`")
    save_data(users_db)
    await event.respond(f"🔑 **({days}) ရက်စာ VIP Key ({count}) ခု:**\n\n" + "\n".join(generated))

@bot.on(events.NewMessage(pattern=r'/addvip (\d+) (\d+)'))
async def addvip_cmd(event):
    uid = str(event.sender_id)
    if not is_admin(uid): return
    target_id, days = event.pattern_match.group(1), int(event.pattern_match.group(2))
    user = get_user(target_id)
    user["vip_until"] = max(time.time(), user.get("vip_until", 0)) + (days * 86400)
    save_data(users_db)
    await event.respond(f"✅ User ID `{target_id}` ကို ({days}) ရက် VIP ပေးပြီးပါပြီ။")

@bot.on(events.NewMessage(pattern=r'/removevip (\d+)'))
async def removevip_cmd(event):
    uid = str(event.sender_id)
    if not is_admin(uid): return
    target_id = event.pattern_match.group(1)
    user = get_user(target_id)
    user["vip_until"] = 0
    save_data(users_db)
    if target_id in running_tasks:
        running_tasks[target_id].cancel()
        running_tasks.pop(target_id, None)
    await event.respond(f"🗑️ User ID `{target_id}` ၏ VIP ကို ဖျက်သိမ်းလိုက်ပါပြီ။")

@bot.on(events.NewMessage(pattern='/vips'))
async def vips_cmd(event):
    uid = str(event.sender_id)
    if not is_admin(uid): return
    active_vips = [f"• `{k}` - {format_expiry(k)}" for k, v in users_db.items() if k != "_vip_keys" and v.get("vip_until", 0) > time.time()]
    unused_keys = [f"• `{k}` ({d} ရက်)" for k, d in users_db.get("_vip_keys", {}).items()]
    await event.respond(f"👑 **VIP ရှိသူများ:**\n" + ("\n".join(active_vips) if active_vips else "မရှိပါ") + f"\n\n🔑 **လက်ကျန် Key များ:**\n" + ("\n".join(unused_keys) if unused_keys else "မရှိပါ"))

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    print("🤖 VIP Auto-Share Bot Started...")
    bot.run_until_disconnected()
