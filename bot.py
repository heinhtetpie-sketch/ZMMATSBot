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
    FloodWaitError,
    SlowModeWaitError
)

# ================= သင်၏ API နှင့် ADMIN အချက်အလက်များ =================
API_ID = 30424297                  
API_HASH = '815196d0e53b30fd4b33a98210442c98'    
BOT_TOKEN = '9111222051:AAEIKxU0tSG7pk0AyRdAaBYpEaygoqLOee8'
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
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"_vip_keys": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

users_db = load_data()
if "_vip_keys" not in users_db:
    users_db["_vip_keys"] = {}

running_tasks = {}
active_clients = {}   # လက်ရှိ ပွင့်နေသော Client များကို သိမ်းထားရန် (Force Stop အတွက်)
active_task_ids = {}  # Task နှစ်ခု ထပ်မပွားစေရန် စစ်ဆေးသည့် ID
login_states = {}
input_states = {}

bot = TelegramClient(StringSession(), API_ID, API_HASH)

def get_user(uid):
    uid = str(uid)
    if uid not in users_db:
        users_db[uid] = {
            "session": None,
            "post_link": None,
            "groups": [],
            "interval": 60,
            "vip_until": 0,
            "is_sharing": False
        }
        save_data(users_db)
    else:
        users_db[uid].setdefault("vip_until", 0)
        users_db[uid].setdefault("is_sharing", False)
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
                return updates.chats[0], None
            except UserAlreadyParticipantError:
                ent = await client.get_entity(link)
                return ent, None
        else:
            username = link.split('/')[-1].split('?')[0].replace('@', '')
            try:
                await client(JoinChannelRequest(username))
            except Exception:
                pass
            ent = await client.get_entity(username)
            return ent, None
    except Exception as e:
        return None, str(e)

async def force_stop_user_share(uid):
    """နောက်ကွယ်တွင် ကျန်နေသော Task နှင့် Client အားလုံးကို အတင်းအကျပ် ရပ်တန့်ပေးသည့်စနစ်"""
    uid = str(uid)
    user = get_user(uid)
    user["is_sharing"] = False
    save_data(users_db)
    
    # Task ID ကို ပြောင်းလိုက်သဖြင့် နောက်ကွယ်က Loop အဟောင်းများ အလိုအလျောက် ရပ်သွားမည်
    active_task_ids[uid] = None

    task = running_tasks.pop(uid, None)
    if task and not task.done():
        task.cancel()

    client = active_clients.pop(uid, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

# ================= Auto-Share အလုပ်လုပ်သည့် အဓိက စနစ် =================
async def user_share_worker(uid, my_task_id):
    uid = str(uid)
    user = get_user(uid)
    
    # အရင်ပွင့်နေသော Client အဟောင်းရှိပါက အရင်ပိတ်မည်
    old_client = active_clients.pop(uid, None)
    if old_client:
        try:
            await old_client.disconnect()
        except Exception:
            pass

    client = TelegramClient(StringSession(user["session"]), API_ID, API_HASH)
    active_clients[uid] = client
    
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await bot.send_message(int(uid), "❌ အကောင့် Session သက်တမ်းကုန်သွားပါပြီ။ `1️⃣ အကောင့်ချိတ်ရန်` ဖြင့် အသစ်ပြန်ချိတ်ပါ။")
            await force_stop_user_share(uid)
            return
    except Exception as e:
        await bot.send_message(int(uid), f"❌ အကောင့်ချိတ်ဆက်မှု အမှား: {e}")
        await force_stop_user_share(uid)
        return

    source_chat, msg_id = parse_post_link(user["post_link"])
    if not source_chat:
        await bot.send_message(int(uid), "❌ Post Link ပုံစံ မှားနေပါသည်။ ပြန်ထည့်ပါ။")
        await force_stop_user_share(uid)
        return

    try:
        await client.get_dialogs(limit=100)
    except Exception:
        pass

    try:
        test_msg = await client.get_messages(source_chat, ids=msg_id)
        if not test_msg:
            await bot.send_message(
                int(uid),
                "❌ **Share ရမည့် မူရင်း Post ကို ရှာမတွေ့ပါ!**\n"
                "သင်ချိတ်ထားသော အကောင့်သည် မူရင်း Post တင်ထားသည့် Channel/Group ထဲသို့ Join ထားရန် လိုအပ်ပါသည်။"
            )
            await force_stop_user_share(uid)
            return
    except Exception as e:
        await bot.send_message(int(uid), f"❌ **မူရင်း Post ကို ယူ၍မရပါ:** `{e}`")
        await force_stop_user_share(uid)
        return

    await bot.send_message(int(uid), f"🔄 Group ({len(user['groups'])}) ခုကို စစ်ဆေးပြီး ချိတ်ဆက်နေပါသည်...")

    target_chats = []
    failed_groups = []
    for link in list(user["groups"]):
        if not user.get("is_sharing") or active_task_ids.get(uid) != my_task_id:
            return
        entity, err = await resolve_and_join_group(client, link)
        if entity:
            target_chats.append((link, entity))
        else:
            failed_groups.append(f"• {link} ({err})")
        await asyncio.sleep(1.5)

    if not target_chats:
        err_text = "\n".join(failed_groups)
        await bot.send_message(int(uid), f"⚠️ **မည်သည့် Group ကိုမှ ချိတ်ဆက်မရပါ:**\n{err_text}")
        await force_stop_user_share(uid)
        return

    status_notice = (
        f"✅ Group ({len(target_chats)}) ခု အသင့်ဖြစ်ပါပြီ။\n"
        f"🚀 Group အားလုံးပို့ပြီးတိုင်း စက္ကန့် ({user['interval']}) အတိအကျခြား၍ Share ပေးပါမည်!"
    )
    if failed_groups:
        status_notice += f"\n\n⚠️ ချိတ်မရသော Group များ:\n" + "\n".join(failed_groups)
    await bot.send_message(int(uid), status_notice)

    first_round = True
    try:
        # Task ID တူညီပြီး is_sharing ပွင့်နေသမျှ ကာလပတ်လုံးသာ အလုပ်လုပ်မည် (နှစ်ခုထပ်မပွားနိုင်ပါ)
        while user.get("is_sharing", False) and active_task_ids.get(uid) == my_task_id:
            if not is_vip(uid):
                await bot.send_message(int(uid), "⚠️ VIP သက်တမ်း ကုန်ဆုံးသဖြင့် ရပ်တန့်လိုက်ပါပြီ။")
                break

            if not client.is_connected():
                try:
                    await client.connect()
                except Exception:
                    await asyncio.sleep(5)
                    continue

            try:
                original_msg = await client.get_messages(source_chat, ids=msg_id)
            except Exception:
                await asyncio.sleep(10)
                continue

            if not original_msg:
                await bot.send_message(int(uid), "❌ မူရင်း Post ပျက်သွားသဖြင့် Auto-Share ရပ်တန့်သွားပါပြီ။")
                break

            round_errors = []
            success_count = 0

            for link, chat_entity in target_chats:
                # ပို့နေစဉ်အတွင်း User က ရပ်လိုက်ပါက ချက်ချင်း ထွက်မည်
                if not user.get("is_sharing", False) or active_task_ids.get(uid) != my_task_id:
                    break

                try:
                    await client.forward_messages(chat_entity, original_msg)
                    success_count += 1
                    # Group တစ်ခုနှင့်တစ်ခုကြား 3 စက္ကန့်ခြားမည်
                    await asyncio.sleep(3)
                except SlowModeWaitError as e:
                    round_errors.append(f"• {link}: SlowMode ({e.seconds}s စောင့်ရမည်)")
                    continue
                except FloodWaitError as e:
                    round_errors.append(f"• {link}: FloodWait ({e.seconds}s)")
                    # FloodWait အချိန်အရမ်းများနေလျှင် အခြား Group များပါ ရပ်မသွားစေရန် ကျော်သွားမည်
                    if e.seconds <= 30:
                        await asyncio.sleep(e.seconds)
                    else:
                        continue
                except Exception as e:
                    round_errors.append(f"• {link}: `{type(e).__name__}` - {e}")

            if first_round and success_count == 0 and round_errors:
                await bot.send_message(
                    int(uid),
                    "⚠️ **Group များသို့ ပို့ရာတွင် အခက်အခဲရှိနေပါသည်:**\n" + "\n".join(round_errors)
                )
            first_round = False

            # သတ်မှတ်ထားသော စက္ကန့်အတိုင်း အတိအကျ စောင့်မည် (စောင့်နေစဉ် ရပ်လိုက်ပါကလည်း ချက်ချင်းရပ်မည်)
            wait_seconds = int(user.get("interval", 60))
            for _ in range(wait_seconds):
                if not user.get("is_sharing", False) or active_task_ids.get(uid) != my_task_id:
                    break
                await asyncio.sleep(1)

    except asyncio.CancelledError:
        pass
    finally:
        # မိမိ Task ID ဖြစ်နေသေးလျှင်သာ Cleanup လုပ်မည်
        if active_task_ids.get(uid) == my_task_id:
            user["is_sharing"] = False
            save_data(users_db)
            running_tasks.pop(uid, None)
            active_clients.pop(uid, None)
        try:
            await client.disconnect()
        except Exception:
            pass

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
        await force_stop_user_share(uid)
        login_states[uid] = {"step": "phone"}
        await event.edit("📱 ချိတ်ဆက်လိုသော Telegram ဖုန်းနံပါတ်ကို Country Code ဖြင့် ပို့ပေးပါ။\n(ဥပမာ - +95912345678)", buttons=back_btn)
    elif data == "m_post":
        input_states[uid] = "post"
        await event.edit("📌 Share လုပ်လိုသော Post Link ကို ပေးပို့ပါ။", buttons=back_btn)
    elif data == "m_group":
        input_states[uid] = "group"
        await event.edit(
            "📌 **Share ထည့်လိုသော Group Link များကို ပေးပို့ပါ။**\n\n"
            "• တစ်ခုချင်းစီ ဆက်တိုက် ပို့နိုင်သလို၊\n"
            "• Link အများကြီးကို စာတစ်စောင်တည်း စုပြီးလည်း ပို့နိုင်ပါသည်။\n"
            "• ထည့်ပြီးပါက **🔙 နောက်သို့ပြန်သွားမည်** ကို နှိပ်ပါ။",
            buttons=back_btn
        )
    elif data == "m_clear":
        user = get_user(uid)
        user["groups"] = []
        save_data(users_db)
        await event.edit("🗑️ Group စာရင်းအားလုံးကို ရှင်းလင်းပြီးပါပြီ။", buttons=back_btn)
    elif data == "m_time":
        input_states[uid] = "time"
        await event.edit("⏱️ Share မည့် အချိန်ခြားနားချက်ကို စက္ကန့်ဖြင့် ပေးပို့ပါ။\n(ဥပမာ - 60)", buttons=back_btn)
    elif data == "m_status":
        user = get_user(uid)
        is_logged = "✅ ချိတ်ဆက်ပြီး" if user["session"] else "❌ မချိတ်ရသေးပါ"
        task = running_tasks.get(uid)
        is_running = "🟢 Run နေသည်" if (user.get("is_sharing") and task and not task.done()) else "🔴 ရပ်ထားသည်"
        groups_list = "\n".join([f"  {i+1}. {g}" for i, g in enumerate(user["groups"])]) if user["groups"] else "  မရှိသေးပါ"
        msg = (
            f"📊 **သင်၏ လက်ရှိ အချက်အလက်များ**\n\n"
            f"• **အကောင့်:** {is_logged}\n"
            f"• **Post Link:** {user['post_link'] or 'မရှိသေးပါ'}\n"
            f"• **အချိန်:** {user['interval']} စက္ကန့်\n"
            f"• **အခြေအနေ:** {is_running}\n"
            f"• **Group စုစုပေါင်း ({len(user['groups'])}) ခု:**\n{groups_list}"
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
        
        # အရင် Run နေသော Task အဟောင်းရှိပါက အပြီးအပိုင် အရင်သတ်မည် (နှစ်ခု မထပ်စေရန်)
        await force_stop_user_share(uid)
        await asyncio.sleep(0.5)

        user["is_sharing"] = True
        save_data(users_db)
        new_task_id = secrets.token_hex(8)
        active_task_ids[uid] = new_task_id

        task = asyncio.create_task(user_share_worker(uid, new_task_id))
        running_tasks[uid] = task
        await event.edit("🚀 Auto-Share စတင်နေပါပြီ...", buttons=back_btn)
    elif data == "m_stop":
        # Status တွင် ရပ်ထားသည်ဟု ပြနေသော်လည်း နောက်ကွယ်မှ Task အားလုံးကို အတင်းအကျပ် ပိတ်ချမည်
        await force_stop_user_share(uid)
        await event.edit("🛑 Auto-Share လုပ်ငန်းစဉ် အားလုံးကို လုံးဝ ရပ်တန့်လိုက်ပါပြီ။", buttons=back_btn)

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
            links = extract_group_links(text)
            if not links:
                await event.respond("❌ Group Link ပုံစံ မှားနေပါသည်။ (`https://t.me/...` သို့မဟုတ် `@username` ပို့ပေးပါ)", buttons=back_btn)
                return
            added_count = 0
            for link in links:
                if link not in user["groups"]:
                    user["groups"].append(link)
                    added_count += 1
            save_data(users_db)
            await event.respond(
                f"✅ Group ({added_count}) ခု ထပ်ထည့်ပြီးပါပြီ။ (လက်ရှိ စုစုပေါင်း {len(user['groups'])} ခု)\n\n"
                "📌 နောက်ထပ် Group Link များ ဆက်ပို့နိုင်ပါသည်။\n"
                "ထည့်ပြီးပါက **🔙 နောက်သို့ပြန်သွားမည်** ကို နှိပ်ပါ။",
                buttons=back_btn
            )
            
        elif action == "time":
            if not text.isdigit():
                await event.respond("❌ ဂဏန်းသာ ရိုက်ထည့်ပါ။ (ဥပမာ - 60)", buttons=back_btn)
                return
            sec = int(text)
            if sec < 15:
                await event.respond("⚠️ အနည်းဆုံး 15 စက္ကန့် သတ်မှတ်ပါ။ ပြန်ထည့်ပါ။", buttons=back_btn)
                return
            user["interval"] = sec
            save_data(users_db)
            input_states.pop(uid, None)
            await event.respond(f"⏱️ အချိန် ({sec}) စက္ကန့် သတ်မှတ်ပြီးပါပြီ။\n(လက်ရှိ Share နေပါက အချိန်အသစ်သက်ရောက်စေရန် ရပ်ပြီး ပြန်စပေးပါ)", buttons=back_btn)
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
    await force_stop_user_share(target_id)
    await event.respond(f"🗑️ User ID `{target_id}` ၏ VIP ကို ဖျက်သိမ်းလိုက်ပါပြီ။")

@bot.on(events.NewMessage(pattern='/vips'))
async def vips_cmd(event):
    uid = str(event.sender_id)
    if not is_admin(uid): return
    active_vips = [f"• `{k}` - {format_expiry(k)}" for k, v in users_db.items() if k != "_vip_keys" and v.get("vip_until", 0) > time.time()]
    unused_keys = [f"• `{k}` ({d} ရက်)" for k, d in users_db.get("_vip_keys", {}).items()]
    await event.respond(f"👑 **VIP ရှိသူများ:**\n" + ("\n".join(active_vips) if active_vips else "မရှိပါ") + f"\n\n🔑 **လက်ကျန် Key များ:**\n" + ("\n".join(unused_keys) if unused_keys else "မရှိပါ"))

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 VIP Auto-Share Bot Started Successfully...")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    Thread(target=run_server, daemon=True).start()
    asyncio.run(main())
