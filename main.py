import os
import asyncio
import logging
import sqlite3
import requests
from aiohttp import web
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# ================= INITIAL DEFAULT CONFIG =================
BOT_TOKEN = "9201048894:AAH4gNA1m_UphXVvKD6HmXcm5YMDbr2HOOo"
ADMIN_IDS = [8419760844]  # সুপার অ্যাডমিন আইডি

DEFAULT_CONFIG = {
    "api_url": "https://1xpanel.com/api/v2",
    "api_key": "e19e34d1e991b739cc18a9917e6d9144",
    "service_id": "1936",            # Reaction + Free View Service ID
    "reaction_rate": "3000",         # 1000 রিঅ্যাকশনের কয়েন রেট
    "view_rate": "2000",             # 1000 ভিউয়ের শো-রেট
    "auto_order_qty": "100",         # গ্রুপে নতুন পোস্ট করলে অটো অর্ডার পরিমাণ
    "binance_uid": "1122595327",     # বাইনান্স পে ইউআইডি
    "required_channel": "@GX_SMM_PREMIUM_PANEL",
    "order_log_group": "-1003908562163",
    "welcome_bonus": "500"           # নতুন ইউজারের ফ্রি বোনাস কয়েন
}
# ==========================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# ================= DATABASE SETUP (SQLite) =================
def init_db():
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    coins INTEGER DEFAULT 0,
                    is_banned INTEGER DEFAULT 0
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS user_groups (
                    chat_id INTEGER PRIMARY KEY,
                    owner_id INTEGER,
                    title TEXT,
                    auto_qty INTEGER DEFAULT 100,
                    is_active INTEGER DEFAULT 1
                )""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_order_id TEXT,
                    user_id INTEGER,
                    chat_id INTEGER,
                    link TEXT,
                    quantity INTEGER,
                    cost INTEGER,
                    status TEXT DEFAULT 'Pending'
                )""")
    for k, v in DEFAULT_CONFIG.items():
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()


def get_config(key):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_CONFIG.get(key)


def set_config(key, value):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_user(user_id, username=""):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username, coins, is_banned FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        bonus = int(get_config("welcome_bonus"))
        c.execute("INSERT INTO users (user_id, username, coins, is_banned) VALUES (?, ?, ?, 0)", (user_id, username, bonus))
        conn.commit()
        user = (user_id, username, bonus, 0)
    conn.close()
    return user


def update_coins(user_id, amount):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


# ================= SMM API ENGINE =================
class SMMAPI:
    def order(self, link, quantity):
        post = {
            'key': get_config("api_key"),
            'action': 'add',
            'service': get_config("service_id"),
            'link': link,
            'quantity': quantity
        }
        return self._connect(post)

    def status(self, order_id):
        return self._connect({
            'key': get_config("api_key"),
            'action': 'status',
            'order': order_id
        })

    def balance(self):
        return self._connect({
            'key': get_config("api_key"),
            'action': 'balance'
        })

    def _connect(self, post):
        try:
            response = requests.post(
                get_config("api_url"),
                data=post,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=15
            )
            return response.json()
        except Exception as e:
            logging.error(f"SMM API Error: {e}")
            return {"error": str(e)}

smm = SMMAPI()


# ================= FSM STATES =================
class UserStates(StatesGroup):
    waiting_for_link = State()
    waiting_for_quantity = State()
    waiting_for_deposit_proof = State()
    waiting_for_group_link_setup = State()


class AdminStates(StatesGroup):
    waiting_for_setting_value = State()
    waiting_for_user_id_coins = State()
    waiting_for_coins_amount = State()
    waiting_for_ban_user_id = State()
    waiting_for_broadcast = State()


# ================= HELPER FUNCTIONS =================
async def check_subscription(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        log_group = int(get_config("order_log_group"))
        member = await bot.get_chat_member(chat_id=log_group, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False


def format_clean_tg_link(raw_text: str) -> str:
    link = raw_text.strip().split()[0]
    if link.startswith("t.me/"):
        link = "https://" + link
    elif link.startswith("@"):
        link = "https://t.me/" + link[1:]
    if "?" in link:
        link = link.split("?")[0]
    return link


def get_super_admin_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🆔 সার্ভিস ID সেট", callback_data="adm_set_service_id"),
        types.InlineKeyboardButton("💎 কয়েন রেট (1K)", callback_data="adm_set_reaction_rate"),
        types.InlineKeyboardButton("⚡️ অটো অর্ডার Qty", callback_data="adm_set_auto_order_qty"),
        types.InlineKeyboardButton("💳 Binance UID সেট", callback_data="adm_set_binance_uid"),
        types.InlineKeyboardButton("🔑 API Key পরিবর্তন", callback_data="adm_set_api_key"),
        types.InlineKeyboardButton("🌐 API URL পরিবর্তন", callback_data="adm_set_api_url"),
        types.InlineKeyboardButton("📢 অর্ডার গ্রুপ ID", callback_data="adm_set_order_log_group"),
        types.InlineKeyboardButton("🎁 ওয়েলকাম বোনাস", callback_data="adm_set_welcome_bonus"),
        types.InlineKeyboardButton("➕ ইউজার কয়েন অ্যাড/কাট", callback_data="adm_manage_coins"),
        types.InlineKeyboardButton("🚫 ইউজার ব্যান / আনব্যান", callback_data="adm_ban_unban"),
        types.InlineKeyboardButton("📣 অল ইউজার ব্রডকাস্ট", callback_data="adm_broadcast"),
        types.InlineKeyboardButton("📊 লাইভ স্ট্যাটাস ও ব্যালেন্স", callback_data="adm_stats")
    )
    return kb


def get_user_main_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🚀 রিঅ্যাকশন + ফ্রি ভিউ অর্ডার", callback_data="usr_new_order"),
        types.InlineKeyboardButton("👀 ফ্রি ভিউ ইনফো", callback_data="usr_free_views"),
        types.InlineKeyboardButton("🤖 অটো গ্রুপ সেটআপ", callback_data="usr_auto_group"),
        types.InlineKeyboardButton("👤 আমার একাউন্ট", callback_data="usr_profile"),
        types.InlineKeyboardButton("💳 বাইনান্স ডিপোজিট", callback_data="usr_deposit"),
        types.InlineKeyboardButton("ℹ️ নিয়মাবলী ও সাপোর্ট", callback_data="usr_help")
    )
    return kb


# ================= START COMMAND (ROLE SEPARATION) =================
@dp.message_handler(commands=['start'], state="*")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    user_id = message.from_user.id

    if user_id in ADMIN_IDS:
        panel_bal = smm.balance()
        text = (
            "👑 <b>SUPER ADMIN CONTROL CENTER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>সার্ভিস ID:</b> <code>{get_config('service_id')}</code> (Reaction + Free View)\n"
            f"💎 <b>রেট (1K):</b> <code>{get_config('reaction_rate')} Coins</code>\n"
            f"⚡️ <b>অটো গ্রুপ পোস্ট Qty:</b> <code>{get_config('auto_order_qty')}</code>\n"
            f"💳 <b>Binance UID:</b> <code>{get_config('binance_uid')}</code>\n"
            f"💰 <b>প্যানেল ব্যালেন্স:</b> <code>{panel_bal.get('balance', 'N/A')} {panel_bal.get('currency', 'USD')}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "নিচের বাটনগুলো থেকে বটের সম্পূর্ণ সিস্টেম কন্ট্রোল ও পরিবর্তন করুন:"
        )
        await message.answer(text, reply_markup=get_super_admin_keyboard())
        return

    user = get_user(user_id, message.from_user.username or "")
    if user[3] == 1:
        await message.answer("🚫 <b>আপনাকে এই বট থেকে ব্যান করা হয়েছে!</b>")
        return

    is_joined = await check_subscription(user_id)
    if not is_joined:
        join_kb = types.InlineKeyboardMarkup(row_width=1)
        join_kb.add(
            types.InlineKeyboardButton("📢 অফিশিয়াল গ্রুপে জয়েন করুন", url="https://t.me/GX_SMM_PREMIUM_PANEL"),
            types.InlineKeyboardButton("✅ জয়েন করেছি (Verify)", callback_data="usr_verify_join")
        )
        await message.answer(
            "⚠️ <b>বট ব্যবহার করতে হলে অবশ্যই আমাদের অফিশিয়াল গ্রুপে জয়েন করতে হবে!</b>\n\n"
            "নিচের বাটনে ক্লিক করে গ্রুপে জয়েন করুন এবং Verify বাটনে চাপ দিন:",
            reply_markup=join_kb
        )
        return

    welcome_text = (
        f"🔥 <b>স্বাগতম {message.from_user.first_name}!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 আমাদের অটোমেটেড SMM প্যানেল বটে আপনি রিঅ্যাকশন অর্ডার করলে সাথে <b>ফ্রি ভিউ</b> পাবেন।\n"
        "🤖 আপনার গ্রুপে বটকে অ্যাডমিন দিলে প্রতিটি নতুন পোস্টে অটোমেটিক অর্ডার চলে যাবে!\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(welcome_text, reply_markup=get_user_main_keyboard())


@dp.callback_query_handler(text="usr_verify_join")
async def verify_join_cb(call: types.CallbackQuery):
    if await check_subscription(call.from_user.id):
        await call.answer("✅ ভেরিফিকেশন সফল!", show_alert=True)
        await call.message.edit_text(
            "🔥 <b>GX SMM PREMIUM PANEL</b>\n\nনিচের প্রিমিয়াম মেনু থেকে আপনার প্রয়োজনীয় অপশন সিলেক্ট করুন:",
            reply_markup=get_user_main_keyboard()
        )
    else:
        await call.answer("❌ আপনি এখনো গ্রুপে জয়েন করেননি!", show_alert=True)


# ================= SUPER ADMIN CALLBACK HANDLERS =================
@dp.callback_query_handler(lambda c: c.data.startswith("adm_"))
async def admin_callbacks(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ শুধুমাত্র অ্যাডমিনের জন্য!", show_alert=True)

    action = call.data
    await call.answer()

    if action == "adm_back":
        panel_bal = smm.balance()
        text = (
            "👑 <b>SUPER ADMIN CONTROL CENTER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>সার্ভিস ID:</b> <code>{get_config('service_id')}</code>\n"
            f"💎 <b>রেট (1K):</b> <code>{get_config('reaction_rate')} Coins</code>\n"
            f"⚡️ <b>অটো গ্রুপ পোস্ট Qty:</b> <code>{get_config('auto_order_qty')}</code>\n"
            f"💳 <b>Binance UID:</b> <code>{get_config('binance_uid')}</code>\n"
            f"💰 <b>প্যানেল ব্যালেন্স:</b> <code>{panel_bal.get('balance', 'N/A')} {panel_bal.get('currency', 'USD')}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        await call.message.edit_text(text, reply_markup=get_super_admin_keyboard())
        return

    setting_map = {
        "adm_set_service_id": ("service_id", "নতুন SMM Service ID লিখুন (যেমন: 1936):"),
        "adm_set_reaction_rate": ("reaction_rate", "১০০০ রিঅ্যাকশনের জন্য নতুন কয়েন রেট লিখুন (যেমন: 3000):"),
        "adm_set_auto_order_qty": ("auto_order_qty", "গ্রুপে নতুন পোস্ট হলে অটো কত পরিমাণ অর্ডার যাবে লিখুন (যেমন: 100):"),
        "adm_set_binance_uid": ("binance_uid", "নতুন Binance Pay UID লিখুন:"),
        "adm_set_api_key": ("api_key", "নতুন SMM Panel API Key লিখুন:"),
        "adm_set_api_url": ("api_url", "নতুন SMM Panel API URL লিখুন (যেমন: https://1xpanel.com/api/v2):"),
        "adm_set_order_log_group": ("order_log_group", "অর্ডার নোটিফিকেশন গ্রুপের Chat ID লিখুন (যেমন: -1003908562163):"),
        "adm_set_welcome_bonus": ("welcome_bonus", "নতুন ইউজার জয়েন করলে কত কয়েন ফ্রি পাবে লিখুন:")
    }

    if action in setting_map:
        key, prompt_text = setting_map[action]
        await state.update_data(config_key=key)
        await call.message.edit_text(f"✏️ <b>{prompt_text}</b>\n\nবর্তমান মান: <code>{get_config(key)}</code>")
        await AdminStates.waiting_for_setting_value.set()

    elif action == "adm_manage_coins":
        await call.message.edit_text("👤 যে ইউজারের কয়েন অ্যাড বা কাটতে চান তার <b>Telegram User ID</b> দিন:")
        await AdminStates.waiting_for_user_id_coins.set()

    elif action == "adm_ban_unban":
        await call.message.edit_text("🚫 ব্যান বা আনব্যান করার জন্য ইউজারের <b>Telegram User ID</b> লিখুন:")
        await AdminStates.waiting_for_ban_user_id.set()

    elif action == "adm_broadcast":
        await call.message.edit_text("📣 সকল ইউজারকে পাঠানোর জন্য আপনার মেসেজটি লিখুন:")
        await AdminStates.waiting_for_broadcast.set()

    elif action == "adm_stats":
        conn = sqlite3.connect("bot_database.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM orders")
        total_orders = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM user_groups WHERE is_active = 1")
        total_groups = c.fetchone()[0]
        conn.close()
        panel_bal = smm.balance()

        stat_text = (
            "📊 <b>LIVE SYSTEM STATISTICS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 মোট ইউজার: <b>{total_users} জন</b>\n"
            f"📦 মোট অর্ডার: <b>{total_orders} টি</b>\n"
            f"🤖 অ্যাক্টিভ অটো গ্রুপ: <b>{total_groups} টি</b>\n"
            f"💰 ওয়েবসাইট ব্যালেন্স: <b>{panel_bal.get('balance', 'N/A')} {panel_bal.get('currency', 'USD')}</b>"
        )
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 অ্যাডমিন প্যানেল", callback_data="adm_back"))
        await call.message.edit_text(stat_text, reply_markup=kb)


@dp.message_handler(state=AdminStates.waiting_for_setting_value)
async def admin_save_setting(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    key = data.get("config_key")
    new_val = message.text.strip()
    set_config(key, new_val)
    await state.finish()
    await message.answer(
        f"✅ <b>সফলভাবে আপডেট হয়েছে!</b>\n📌 <code>{key}</code> = <code>{new_val}</code>",
        reply_markup=get_super_admin_keyboard()
    )


@dp.message_handler(state=AdminStates.waiting_for_user_id_coins)
async def admin_get_uid_coins(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ সঠিক সংখ্যায় User ID দিন:")
    target_uid = int(message.text)
    get_user(target_uid)
    await state.update_data(target_uid=target_uid)
    await message.answer("💎 কত কয়েন যোগ বা বিয়োগ করতে চান লিখুন:\n<i>(যোগ করতে: 5000 | কাটতে: -2000)</i>")
    await AdminStates.waiting_for_coins_amount.set()


@dp.message_handler(state=AdminStates.waiting_for_coins_amount)
async def admin_apply_coins(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ সংখ্যায় পরিমাণ লিখুন:")

    data = await state.get_data()
    target_uid = data.get("target_uid")
    update_coins(target_uid, amount)
    await state.finish()

    try:
        await bot.send_message(target_uid, f"💎 অ্যাডমিন আপনার একাউন্টে <b>{amount} Coins</b> আপডেট করেছেন!")
    except Exception:
        pass

    await message.answer(
        f"✅ ইউজার <code>{target_uid}</code> এর ব্যালেন্সে <b>{amount} Coins</b> আপডেট করা হয়েছে!",
        reply_markup=get_super_admin_keyboard()
    )


@dp.message_handler(state=AdminStates.waiting_for_ban_user_id)
async def admin_toggle_ban(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ সঠিক User ID দিন:")
    target_uid = int(message.text)
    user = get_user(target_uid)
    new_status = 0 if user[3] == 1 else 1
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (new_status, target_uid))
    conn.commit()
    conn.close()
    await state.finish()
    status_str = "ব্যান (Banned) 🚫" if new_status == 1 else "আনব্যান (Unbanned) ✅"
    await message.answer(f"ইউজার <code>{target_uid}</code> এখন <b>{status_str}</b>!", reply_markup=get_super_admin_keyboard())


@dp.message_handler(state=AdminStates.waiting_for_broadcast)
async def admin_send_broadcast(message: types.Message, state: FSMContext):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = c.fetchall()
    conn.close()

    sent = 0
    for (uid,) in users:
        if uid not in ADMIN_IDS:
            try:
                await bot.send_message(uid, f"📢 <b>অ্যাডমিন নোটিশ:</b>\n\n{message.text}")
                sent += 1
            except Exception:
                pass
    await state.finish()
    await message.answer(f"✅ মোট <b>{sent} জন</b> ইউজারের কাছে মেসেজ পাঠানো হয়েছে!", reply_markup=get_super_admin_keyboard())


# ================= USER CALLBACK HANDLERS =================
@dp.callback_query_handler(lambda c: c.data.startswith("usr_"))
async def user_callbacks(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if user_id in ADMIN_IDS:
        return await call.answer("আপনি অ্যাডমিন! /start দিয়ে অ্যাডমিন প্যানেল ব্যবহার করুন।", show_alert=True)

    user = get_user(user_id, call.from_user.username or "")
    if user[3] == 1:
        return await call.answer("🚫 আপনি ব্যানড!", show_alert=True)

    action = call.data
    await call.answer()

    if action == "usr_main":
        await call.message.edit_text("🔥 <b>GX SMM PREMIUM PANEL</b>\n\nনিচের মেনু থেকে সিলেক্ট করুন:", reply_markup=get_user_main_keyboard())

    elif action == "usr_profile":
        text = (
            "👤 <b>আপনার প্রোফাইল তথ্য</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            f"💎 বর্তমান কয়েন: <b>{user[2]} Coins</b>\n"
            f"⚡️ সার্ভিস রেট: <b>{get_config('reaction_rate')} Coins / 1K</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 প্রধান মেনু", callback_data="usr_main"))
        await call.message.edit_text(text, reply_markup=kb)

    elif action == "usr_new_order":
        await call.message.edit_text(
            "🔗 <b>আপনার টেলিগ্রাম পোস্টের লিংক দিন:</b>\n\n"
            "<i>উদাহরণ: https://t.me/channel_name/123</i>\n"
            "✨ এই অর্ডারের সাথে রিঅ্যাকশন + ফ্রি ভিউ একসাথে পাবেন!"
        )
        await UserStates.waiting_for_link.set()

    elif action == "usr_free_views":
        text = (
            "👀 <b>ফ্রি টেলিগ্রাম ভিউ সিস্টেম</b>\n\n"
            f"💎 রেট: {get_config('view_rate')} Coins / 1K (Combo Free)\n"
            "✅ আলাদাভাবে ভিউ অর্ডার করার প্রয়োজন নেই! আপনি রিঅ্যাকশন অর্ডার করলেই প্রতিটি পোস্টে অটোমেটিক ফ্রি ভিউ চলে যাবে।"
        )
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 প্রধান মেনু", callback_data="usr_main"))
        await call.message.edit_text(text, reply_markup=kb)

    elif action == "usr_auto_group":
        bot_me = await bot.get_me()
        text = (
            "🤖 <b>অটোমেটিক গ্রুপ পোস্ট সেটআপ</b>\n\n"
            f"১. প্রথমে বটকে (<code>@{bot_me.username}</code>) আপনার গ্রুপে <b>Admin</b> বানান।\n"
            "২. অ্যাডমিন দিলেই বট অটোমেটিক আপনার গ্রুপ কানেক্ট করে নেবে!\n"
            "৩. এরপর গ্রুপে নতুন কোনো পোস্ট করলেই আপনার একাউন্ট থেকে কয়েন কেটে অটোমেটিক ওয়েবসাইটে অর্ডার চলে যাবে।\n\n"
            "অথবা নিচে আপনার গ্রুপের লিংক/ইউজারনেম পাঠিয়ে ম্যানুয়ালিও সেটআপ করতে পারেন:"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("🔗 গ্রুপ লিংক দিয়ে সেটআপ করুন", callback_data="usr_manual_group_link"),
            types.InlineKeyboardButton("🔙 প্রধান মেনু", callback_data="usr_main")
        )
        await call.message.edit_text(text, reply_markup=kb)

    elif action == "usr_manual_group_link":
        await call.message.edit_text("🔗 আপনার পাবলিক গ্রুপের লিংক বা <code>@username</code> পাঠান (বট অবশ্যই ওই গ্রুপের অ্যাডমিন হতে হবে):")
        await UserStates.waiting_for_group_link_setup.set()

    elif action == "usr_deposit":
        uid = get_config("binance_uid")
        text = (
            "💳 <b>বাইনান্স অটো ডিপোজিট (Binance Pay)</b>\n\n"
            f"📌 <b>Binance Pay UID:</b> <code>{uid}</code>\n\n"
            "উপরের UID-তে ডলার পাঠিয়ে নিচের বাটনে ক্লিক করে আপনার <b>Order ID / TrxID</b> জমা দিন:"
        )
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("📤 পেমেন্ট প্রুফ সাবমিট করুন", callback_data="usr_submit_deposit"),
            types.InlineKeyboardButton("🔙 প্রধান মেনু", callback_data="usr_main")
        )
        await call.message.edit_text(text, reply_markup=kb)

    elif action == "usr_submit_deposit":
        await call.message.edit_text("📝 আপনার বাইনান্স পেমেন্টের <b>Order ID / Transaction Details</b> লিখে পাঠান:")
        await UserStates.waiting_for_deposit_proof.set()

    elif action == "usr_help":
        text = (
            "ℹ️ <b>সাহায্য ও নিয়মাবলী</b>\n\n"
            "• অর্ডার দেওয়ার সময় সঠিক পাবলিক পোস্ট লিংক দিন।\n"
            "• গ্রুপে বটকে অ্যাডমিন করলে প্রতিটি নতুন পোস্টে অটো অর্ডার হবে।\n"
            "• যেকোনো সমস্যায় অ্যাডমিনের সাথে যোগাযোগ করুন।"
        )
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 প্রধান মেনু", callback_data="usr_main"))
        await call.message.edit_text(text, reply_markup=kb)


# ================= USER ORDER & SETUP HANDLERS =================
@dp.message_handler(state=UserStates.waiting_for_link)
async def user_receive_link(message: types.Message, state: FSMContext):
    link = format_clean_tg_link(message.text)
    if not link.startswith("https://t.me/"):
        return await message.answer("❌ ভুল লিংক! দয়া করে সঠিক টেলিগ্রাম পোস্ট লিংক দিন (যেমন: https://t.me/channel/123):")
    await state.update_data(link=link)
    rate = get_config("reaction_rate")
    await message.answer(f"🔢 কত পরিমাণ রিঅ্যাকশন (+ ফ্রি ভিউ) নিতে চান সংখ্যায় লিখুন:\n<i>(রেট: ১০০০ পিস = {rate} কয়েন)</i>")
    await UserStates.waiting_for_quantity.set()


@dp.message_handler(state=UserStates.waiting_for_quantity)
async def user_receive_qty(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ দয়া করে সঠিক সংখ্যা লিখুন:")
    qty = int(message.text)
    data = await state.get_data()
    link = data.get("link")
    user_id = message.from_user.id

    rate = int(get_config("reaction_rate"))
    cost = max(1, int((qty / 1000) * rate))
    user = get_user(user_id)

    if user[2] < cost:
        await state.finish()
        return await message.answer(
            f"❌ <b>পর্যাপ্ত কয়েন নেই!</b>\nপ্রয়োজন: <b>{cost} Coins</b>\nআপনার আছে: <b>{user[2]} Coins</b>",
            reply_markup=get_user_main_keyboard()
        )

    res = smm.order(link, qty)
    if "order" in res:
        api_oid = str(res["order"])
        update_coins(user_id, -cost)

        conn = sqlite3.connect("bot_database.db")
        c = conn.cursor()
        c.execute("INSERT INTO orders (api_order_id, user_id, chat_id, link, quantity, cost, status) VALUES (?, ?, ?, ?, ?, ?, 'Pending')",
                  (api_oid, user_id, user_id, link, qty, cost))
        conn.commit()
        conn.close()

        await message.answer(
            "✅ <b>অর্ডার সফলভাবে ওয়েবসাইটে সাবমিট হয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Order ID: <code>{api_oid}</code>\n"
            f"🔗 Post Link: {link}\n"
            f"📦 Quantity: <b>{qty} (Reaction + Free View)</b>\n"
            f"💎 কয়েন কাটা হয়েছে: <b>{cost} Coins</b>\n"
            "📌 Status: <b>Pending ⏳</b>",
            reply_markup=get_user_main_keyboard()
        )

        try:
            log_group = int(get_config("order_log_group"))
            await bot.send_message(
                log_group,
                f"🚀 <b>নতুন অর্ডার প্লেস হয়েছে!</b>\n"
                f"👤 User: <a href='tg://user?id={user_id}'>{message.from_user.first_name}</a> (<code>{user_id}</code>)\n"
                f"🆔 Order ID: <code>{api_oid}</code>\n"
                f"🔗 Post: {link}\n"
                f"📦 Qty: <b>{qty}</b> | Status: <b>Pending ⏳</b>"
            )
        except Exception:
            pass
    else:
        await message.answer(f"❌ ওয়েবসাইট API এরর: <code>{res}</code>", reply_markup=get_user_main_keyboard())

    await state.finish()


@dp.message_handler(state=UserStates.waiting_for_group_link_setup)
async def user_manual_group_setup(message: types.Message, state: FSMContext):
    clean_link = format_clean_tg_link(message.text)
    username = "@" + clean_link.replace("https://t.me/", "").split("/")[0]
    try:
        chat = await bot.get_chat(username)
        bot_member = await bot.get_chat_member(chat.id, (await bot.get_me()).id)
        if bot_member.status not in ['administrator', 'creator']:
            return await message.answer("❌ বট এখনো ওই গ্রুপের অ্যাডমিন নয়! আগে বটকে গ্রুপে Admin বানান।")

        conn = sqlite3.connect("bot_database.db")
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO user_groups (chat_id, owner_id, title, auto_qty, is_active) VALUES (?, ?, ?, ?, 1)",
                  (chat.id, message.from_user.id, chat.title, int(get_config("auto_order_qty"))))
        conn.commit()
        conn.close()

        await state.finish()
        await message.answer(
            f"✅ <b>গ্রুপ সফলভাবে সেটআপ হয়েছে!</b>\n📌 গ্রুপ: <b>{chat.title}</b>\n🔗 লিংক: {clean_link}\n"
            "এখন ওই গ্রুপে নতুন পোস্ট করলেই অটো অর্ডার হবে!",
            reply_markup=get_user_main_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ গ্রুপ খুঁজে পাওয়া যায়নি বা বট অ্যাডমিন নেই। সঠিক পাবলিক লিংক দিন্ন।\nError: {e}")


@dp.message_handler(state=UserStates.waiting_for_deposit_proof)
async def user_deposit_proof_submit(message: types.Message, state: FSMContext):
    user = message.from_user
    for adm in ADMIN_IDS:
        try:
            await bot.send_message(
                adm,
                f"💰 <b>নতুন বাইনান্স ডিপোজিট রিকোয়েস্ট!</b>\n\n"
                f"👤 ইউজার: {user.full_name} (<code>{user.id}</code>)\n"
                f"📝 প্রুফ/TrxID: <code>{message.text}</code>"
            )
        except Exception:
            pass
    await state.finish()
    await message.answer("✅ আপনার ডিপোজিট প্রুফ অ্যাডমিনের কাছে পাঠানো হয়েছে! যাচাই করে কয়েন যোগ করে দেওয়া হবে।", reply_markup=get_user_main_keyboard())


# ================= AUTO ADMIN DETECTION IN GROUPS =================
@dp.my_chat_member_handler()
async def on_bot_added_as_admin(update: types.ChatMemberUpdated):
    new_status = update.new_chat_member.status
    chat = update.chat
    adder = update.from_user

    if new_status in ['administrator', 'creator'] and chat.type in ['group', 'supergroup', 'channel']:
        conn = sqlite3.connect("bot_database.db")
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE user_id = ?", (adder.id,))
        exists = c.fetchone()
        if exists:
            auto_qty = int(get_config("auto_order_qty"))
            c.execute("INSERT OR REPLACE INTO user_groups (chat_id, owner_id, title, auto_qty, is_active) VALUES (?, ?, ?, ?, 1)",
                      (chat.id, adder.id, chat.title, auto_qty))
            conn.commit()
            try:
                await bot.send_message(
                    adder.id,
                    f"🤖 <b>অটো গ্রুপ সেটআপ সম্পন্ন!</b>\n\n"
                    f"আপনি <b>{chat.title}</b> গ্রুপে বটকে অ্যাডমিন দিয়েছেন। এখন থেকে এই গ্রুপে নতুন পোস্ট করলেই অটোমেটিক <b>{auto_qty}</b> রিঅ্যাকশন + ফ্রি ভিউ অর্ডার হয়ে যাবে!"
                )
            except Exception:
                pass
        conn.close()


# ================= AUTO POST LINK ORDER ENGINE =================
@dp.channel_post_handler(content_types=types.ContentType.ANY)
@dp.message_handler(content_types=types.ContentType.ANY)
async def auto_group_new_post_trigger(message: types.Message):
    if message.chat.type not in ['group', 'supergroup', 'channel']:
        return

    chat_id = message.chat.id
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT owner_id, auto_qty, is_active FROM user_groups WHERE chat_id = ?", (chat_id,))
    grp = c.fetchone()
    conn.close()

    if not grp or grp[2] != 1:
        return

    owner_id, qty, _ = grp
    user = get_user(owner_id)
    if user[3] == 1:
        return

    if message.chat.username:
        post_link = f"https://t.me/{message.chat.username}/{message.message_id}"
    else:
        clean_chat_id = str(chat_id).replace("-100", "")
        post_link = f"https://t.me/c/{clean_chat_id}/{message.message_id}"

    rate = int(get_config("reaction_rate"))
    cost = max(1, int((qty / 1000) * rate))

    if user[2] < cost:
        try:
            await bot.send_message(owner_id, f"⚠️ আপনার গ্রুপ <b>{message.chat.title}</b>-এ নতুন পোস্ট হয়েছে, কিন্তু কয়েন কম থাকায় অটো অর্ডার হয়নি!")
        except Exception:
            pass
        return

    res = smm.order(post_link, qty)
    if "order" in res:
        api_oid = str(res["order"])
        update_coins(owner_id, -cost)

        conn = sqlite3.connect("bot_database.db")
        c = conn.cursor()
        c.execute("INSERT INTO orders (api_order_id, user_id, chat_id, link, quantity, cost, status) VALUES (?, ?, ?, ?, ?, ?, 'Pending')",
                  (api_oid, owner_id, chat_id, post_link, qty, cost))
        conn.commit()
        conn.close()

        try:
            log_group = int(get_config("order_log_group"))
            await bot.send_message(
                log_group,
                f"⚡️ <b>অটো পোস্ট অর্ডার প্লেস হয়েছে!</b>\n"
                f"👤 Owner ID: <code>{owner_id}</code>\n"
                f"🆔 Order ID: <code>{api_oid}</code>\n"
                f"🔗 Post Link: {post_link}\n"
                f"📦 Qty: <b>{qty}</b> | Status: <b>Pending ⏳</b>"
            )
        except Exception:
            pass


# ================= RAILWAY DUMMY WEB SERVER =================
async def handle(request):
    return web.Response(text="Bot is running successfully on Railway!")

async def web_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")


# ================= BACKGROUND STARTUP =================
async def on_startup(dispatcher):
    init_db()
    asyncio.create_task(web_server())
    logging.info("Telegram Bot and Web Server started successfully on Railway!")


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
