import os
import re
import uuid
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("errors.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set!")

BOT_USERNAME = os.getenv("BOT_USERNAME", "FunpayTrustly_robot")
PA_USERNAME = os.getenv("PA_USERNAME", "")
if PA_USERNAME:
    WEBHOOK_URL = f"https://{PA_USERNAME}.pythonanywhere.com"
else:
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# Ссылки на картинки для каждого языка (только для главного меню)
PHOTO_URLS = {
    "ru": {"main": "https://ibb.co/rG08CGyz"},
    "en": {"main": "https://ibb.co/qYw6fVPt"},
    "uk": {"main": "https://ibb.co/zVrbJ9Cj"},
    "kk": {"main": "https://ibb.co/Z1kD9vdL"},
    "zh": {"main": "https://ibb.co/nMM9FhHj"},
    "hi": {"main": "https://ibb.co/Xrg1yvFh"},
}

ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "8822297551").split(",") if x.strip().isdigit()}
DB_NAME = os.getenv("DB_NAME", "database.db")
COMMISSION_BPS = 100
MAX_ACTIVE_DEALS = 5
ARCHIVE_AFTER_HOURS = 24

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# DATABASE
# ============================================================
def db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def execute(sql, params=()):
    with db() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid

def fetchone(sql, params=()):
    with db() as conn:
        return conn.execute(sql, params).fetchone()

def fetchall(sql, params=()):
    with db() as conn:
        return conn.execute(sql, params).fetchall()

def init_db():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                lang TEXT DEFAULT 'ru',
                card TEXT,
                crypto TEXT,
                stars_username TEXT,
                ref_count INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 0,
                frozen_balance INTEGER DEFAULT 0,
                deals_count INTEGER DEFAULT 0,
                successful_deals INTEGER DEFAULT 0,
                rating REAL DEFAULT 0,
                reviews_count INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                created_at TEXT,
                accepted_policy INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS deals (
                deal_id TEXT PRIMARY KEY,
                seller_id INTEGER,
                buyer_id INTEGER,
                deal_type TEXT,
                description TEXT,
                amount INTEGER DEFAULT 0,
                currency TEXT,
                seller_req TEXT,
                buyer_req TEXT,
                gift_link TEXT,
                status TEXT DEFAULT 'waiting_buyer',
                seller_username TEXT,
                buyer_username TEXT,
                created_at TEXT,
                completed_at TEXT,
                confirmed_at TEXT,
                commission INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                created_at TEXT,
                PRIMARY KEY (referrer_id, referred_id)
            );
            CREATE TABLE IF NOT EXISTS gifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                gift_link TEXT,
                description TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                content TEXT,
                created_at TEXT,
                sent_to INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id INTEGER,
                to_user_id INTEGER,
                deal_id TEXT,
                rating INTEGER,
                comment TEXT,
                created_at TEXT,
                UNIQUE(from_user_id, to_user_id, deal_id)
            );
            CREATE TABLE IF NOT EXISTS archived_deals (
                deal_id TEXT PRIMARY KEY,
                seller_id INTEGER,
                buyer_id INTEGER,
                deal_type TEXT,
                description TEXT,
                amount INTEGER,
                currency TEXT,
                seller_req TEXT,
                buyer_req TEXT,
                gift_link TEXT,
                status TEXT,
                seller_username TEXT,
                buyer_username TEXT,
                created_at TEXT,
                completed_at TEXT,
                confirmed_at TEXT,
                commission INTEGER DEFAULT 0,
                archived_at TEXT
            );
            CREATE TABLE IF NOT EXISTS service_balance (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                balance INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS admin_settings (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                last_news_id INTEGER DEFAULT 0
            );
        """)
        conn.execute("INSERT OR IGNORE INTO service_balance(id, balance) VALUES (1, 0)")
        conn.execute("INSERT OR IGNORE INTO admin_settings(id, last_news_id) VALUES (1, 0)")
        conn.commit()

init_db()

# ============================================================
# TRANSLATION
# ============================================================
def tr(key, lang="ru", **kwargs):
    lang = lang if lang in T else "ru"
    text = T[lang].get(key, T["ru"].get(key, key))
    try:
        return text.format(**kwargs)
    except Exception:
        return text

def user_lang(user_id):
    row = fetchone("SELECT lang FROM users WHERE user_id=?", (user_id,))
    return row["lang"] if row and row["lang"] in T else "ru"

def ensure_user(user):
    username = user.username or ""
    now = datetime.now(timezone.utc).isoformat()
    execute("INSERT INTO users(user_id, username, created_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username", (user.id, username, now))

def is_banned(user_id):
    row = fetchone("SELECT banned FROM users WHERE user_id=?", (user_id,))
    return bool(row and row["banned"])

def is_admin(user_id):
    return user_id in ADMIN_IDS

def admin_log(admin_id, action, details=""):
    execute("INSERT INTO admin_logs(admin_id, action, details, created_at) VALUES(?,?,?,?)", (admin_id, action, details, datetime.now(timezone.utc).isoformat()))

def active_count(user_id):
    row = fetchone("SELECT COUNT(*) AS c FROM deals WHERE (seller_id=? OR buyer_id=?) AND status NOT IN ('completed','cancelled')", (user_id, user_id))
    return int(row["c"]) if row else 0

def status_text(status, lang):
    return {
        "active": tr("deal_active", lang),
        "waiting_buyer": "⏳ Ожидает покупателя",
        "waiting_seller": "⏳ Ожидает продавца",
        "completed": "✅ Завершена",
        "cancelled": "❌ Отменена",
    }.get(status, status)

async def safe_send(chat_id, text, markup=None, photo_url=None):
    try:
        if photo_url:
            try:
                await bot.send_photo(chat_id, photo_url, caption=text, reply_markup=markup, parse_mode="HTML")
                return
            except Exception as e:
                logger.warning(f"Photo send error ({e}), sending text only.")
        await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        logger.exception("safe_send failed")

async def notify(user_id, text, markup=None):
    if not user_id:
        return
    try:
        await bot.send_message(user_id, text, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        logger.warning("Notification to %s failed: %s", user_id, e)

async def admin_error(text):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "⚠️ Bot error:\n" + text)
        except Exception:
            pass

async def show_main(chat_id, user_id):
    lang = user_lang(user_id)
    photo = PHOTO_URLS.get(lang, PHOTO_URLS["ru"])["main"]
    await safe_send(chat_id, tr("main", lang), kb_main(lang), photo_url=photo)

def deal_link(deal_id):
    return f"https://t.me/{BOT_USERNAME}?start=deal_{deal_id}"

def parse_amount(text):
    text = (text or "").strip().replace(" ", "")
    if not re.fullmatch(r"\d+", text):
        return None
    value = int(text)
    return value if value > 0 else None

async def check_operation_allowed(message):
    ensure_user(message.from_user)
    if is_banned(message.from_user.id):
        await message.answer(tr("banned", user_lang(message.from_user.id)))
        return False
    return True

# ============================================================
# KEYBOARDS (PRIMARY КНОПКИ, ПО 1 ЭМОДЗИ)
# ============================================================
def kb_main(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 " + tr("create", lang), callback_data="create_deal", style="primary")],
        [InlineKeyboardButton(text="📂 " + tr("my_deals", lang), callback_data="my_deals", style="primary"),
         InlineKeyboardButton(text="💳 " + tr("req", lang), callback_data="requisites", style="primary")],
        [InlineKeyboardButton(text="👥 " + tr("referral", lang), callback_data="referral", style="primary"),
         InlineKeyboardButton(text="👤 " + tr("profile", lang), callback_data="profile", style="primary")],
        [InlineKeyboardButton(text="🌐 " + tr("language", lang), callback_data="lang", style="primary"),
         InlineKeyboardButton(text="🆘 " + tr("support", lang), url="https://t.me/FunPayHeIp", style="primary")],
        [InlineKeyboardButton(text="ℹ️ " + tr("about", lang), callback_data="about", style="primary")],
    ])

def kb_back(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")]])

def kb_roles(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 " + tr("seller", lang), callback_data="role_seller", style="primary"),
         InlineKeyboardButton(text="🛍️ " + tr("buyer", lang), callback_data="role_buyer", style="primary")],
        [InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")]
    ])

def kb_types(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 " + tr("account", lang), callback_data="type_account", style="primary"),
         InlineKeyboardButton(text="🎁 " + tr("gift", lang), callback_data="type_gift", style="primary")],
        [InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")]
    ])

def kb_currencies(lang, prefix):
    labels = [
        ("TON", "💎 " + tr("curr_ton", lang)),
        ("USDT", "$ " + tr("curr_usdt", lang)),
        ("RUB", "₽ " + tr("curr_rub", lang)),
        ("UAH", "₴ " + tr("curr_uah", lang)),
        ("BYN", "฿ " + tr("curr_byn", lang)),
        ("STARS", "★ " + tr("curr_stars", lang)),
        ("KZT", "₸ " + tr("curr_kzt", lang))
    ]
    rows = []
    rows.append([InlineKeyboardButton(text=labels[0][1], callback_data=f"{prefix}{labels[0][0]}", style="primary")])
    for i in range(1, len(labels), 2):
        pair = labels[i:i+2]
        row = [InlineKeyboardButton(text=pair[0][1], callback_data=f"{prefix}{pair[0][0]}", style="primary")]
        if len(pair) > 1:
            row.append(InlineKeyboardButton(text=pair[1][1], callback_data=f"{prefix}{pair[1][0]}", style="primary"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_balance(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ " + tr("deposit", lang), callback_data="deposit", style="primary")],
        [InlineKeyboardButton(text="➖ " + tr("withdraw", lang), callback_data="withdraw", style="primary")],
        [InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")]
    ])

def kb_my_deals(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ " + tr("clear_history", lang), callback_data="clear_history", style="primary")],
        [InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")]
    ])

# ============================================================
# FSM STATES
# ============================================================
class States(StatesGroup):
    seller_type = State()
    seller_description = State()
    seller_currency = State()
    seller_amount = State()
    seller_req = State()
    buyer_type = State()
    buyer_description = State()
    buyer_currency = State()
    buyer_amount = State()
    buyer_username = State()
    deposit = State()
    withdraw = State()
    req_input = State()
    review_rating = State()
    review_comment = State()
    admin_news = State()
    admin_req = State()

# ============================================================
# HANDLERS
# ============================================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user)
    uid = message.from_user.id
    row = fetchone("SELECT lang, accepted_policy FROM users WHERE user_id=?", (uid,))
    if not row or row["accepted_policy"] == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="onboard_ru", style="primary")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="onboard_en", style="primary")],
            [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="onboard_uk", style="primary")],
            [InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="onboard_kk", style="primary")],
            [InlineKeyboardButton(text="🇨🇳 中文", callback_data="onboard_zh", style="primary")],
            [InlineKeyboardButton(text="🇮🇳 हिन्दी", callback_data="onboard_hi", style="primary")]
        ])
        await message.answer(tr("lang_choose", row["lang"] if row else "ru"), reply_markup=kb, parse_mode="HTML")
        return
    lang = row["lang"] if row else "ru"
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("ref"):
            ref = param[3:]
            if ref.isdigit() and int(ref) != uid:
                ref_id = int(ref)
                if fetchone("SELECT user_id FROM users WHERE user_id=?", (ref_id,)):
                    execute("INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)", (ref_id, uid, datetime.now(timezone.utc).isoformat()))
                    execute("UPDATE users SET ref_count=(SELECT COUNT(*) FROM referrals WHERE referrer_id=?) WHERE user_id=?", (ref_id, ref_id))
        elif param.startswith("deal_"):
            await join_deal(message, state, param[5:])
            return
    await show_main(message.chat.id, uid)

@dp.callback_query(F.data.startswith("onboard_"))
async def onboard_set_lang(call: CallbackQuery):
    lang = call.data.replace("onboard_", "")
    uid = call.from_user.id
    execute("UPDATE users SET lang=? WHERE user_id=?", (lang, uid))
    await call.message.answer(
        tr("policy_text", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📜 " + tr("policy_btn", lang), url="https://t.me/PrivacyPoliceFunpay", style="primary")],
            [InlineKeyboardButton(text="✅ " + tr("accept_btn", lang), callback_data="accept_policy", style="primary")]
        ]),
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query(F.data == "accept_policy")
async def accept_policy(call: CallbackQuery):
    uid = call.from_user.id
    execute("UPDATE users SET accepted_policy=1 WHERE user_id=?", (uid,))
    await call.message.delete()
    await show_main(call.message.chat.id, uid)
    await call.answer()

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_main(call.message.chat.id, call.from_user.id)

@dp.callback_query(F.data == "create_deal")
async def cb_create(call: CallbackQuery):
    if is_banned(call.from_user.id):
        await call.answer(tr("banned", user_lang(call.from_user.id)), show_alert=True)
        return
    if active_count(call.from_user.id) >= MAX_ACTIVE_DEALS:
        await call.answer(tr("active_limit", user_lang(call.from_user.id)), show_alert=True)
        return
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("choose_role", lang), reply_markup=kb_roles(lang), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "role_seller")
async def seller_role(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.seller_type)
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("choose_type", lang), reply_markup=kb_types(lang), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("type_"), States.seller_type)
async def seller_type(call: CallbackQuery, state: FSMContext):
    deal_type = call.data.replace("type_", "")
    await state.update_data(deal_type=deal_type)
    await state.set_state(States.seller_description)
    lang = user_lang(call.from_user.id)
    if deal_type == "account":
        await call.message.answer(tr("description_account", lang), parse_mode="HTML")
    else:
        await call.message.answer(tr("description_gift", lang), parse_mode="HTML")
    await call.answer()

@dp.message(States.seller_description)
async def seller_description(message: Message, state: FSMContext):
    if not await check_operation_allowed(message):
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("❌ Description too short.", parse_mode="HTML")
        return
    await state.update_data(description=text)
    await state.set_state(States.seller_currency)
    await message.answer(tr("currency", user_lang(message.from_user.id)), reply_markup=kb_currencies(user_lang(message.from_user.id), "sellcurr_"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("sellcurr_"), States.seller_currency)
async def seller_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("sellcurr_", "")
    await state.update_data(currency=currency)
    await state.set_state(States.seller_amount)
    await call.message.answer(tr("amount", user_lang(call.from_user.id)), parse_mode="HTML")
    await call.answer()

@dp.message(States.seller_amount)
async def seller_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(tr("amount", user_lang(message.from_user.id)), parse_mode="HTML")
        return
    await state.update_data(amount=amount)
    await state.set_state(States.seller_req)
    await message.answer(tr("requisites", user_lang(message.from_user.id)), parse_mode="HTML")

@dp.message(States.seller_req)
async def seller_req(message: Message, state: FSMContext):
    req = (message.text or "").strip()
    if len(req) < 3:
        await message.answer("❌ Requisites too short.", parse_mode="HTML")
        return
    uid = message.from_user.id
    data = await state.get_data()
    deal_id = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc).isoformat()
    username = message.from_user.username or ""
    try:
        execute("INSERT INTO deals(deal_id,seller_id,deal_type,description,amount,currency,seller_req,status,seller_username,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (deal_id, uid, data["deal_type"], data["description"], data["amount"], data["currency"], req, "waiting_buyer", username, now))
        execute("UPDATE users SET deals_count=deals_count+1 WHERE user_id=?", (uid,))
    except Exception as e:
        logger.exception(f"Deal creation error: {e}")
        await message.answer("⚠️ Error creating deal. Try later.", parse_mode="HTML")
        await state.clear()
        return
    await state.clear()
    lang = user_lang(uid)
    gift_link = data["description"] if data["deal_type"] == "gift" else "—"
    await message.answer(
        tr("deal_created", lang).format(
            deal_id=deal_id,
            currency=data["currency"],
            amount=data["amount"],
            gift_link=gift_link,
            link=deal_link(deal_id)
        ),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "role_buyer")
async def buyer_role(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.buyer_type)
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("choose_type", lang), reply_markup=kb_types(lang), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("type_"), States.buyer_type)
async def buyer_type(call: CallbackQuery, state: FSMContext):
    deal_type = call.data.replace("type_", "")
    await state.update_data(deal_type=deal_type)
    await state.set_state(States.buyer_description)
    lang = user_lang(call.from_user.id)
    if deal_type == "account":
        await call.message.answer(tr("description_account", lang), parse_mode="HTML")
    else:
        await call.message.answer(tr("description_gift", lang), parse_mode="HTML")
    await call.answer()

@dp.message(States.buyer_description)
async def buyer_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("❌ Description too short.", parse_mode="HTML")
        return
    await state.update_data(description=text)
    await state.set_state(States.buyer_currency)
    await message.answer(tr("currency", user_lang(message.from_user.id)), reply_markup=kb_currencies(user_lang(message.from_user.id), "buycurr_"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("buycurr_"), States.buyer_currency)
async def buyer_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("buycurr_", "")
    await state.update_data(currency=currency)
    await state.set_state(States.buyer_amount)
    await call.message.answer(tr("amount", user_lang(call.from_user.id)), parse_mode="HTML")
    await call.answer()

@dp.message(States.buyer_amount)
async def buyer_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(tr("amount", user_lang(message.from_user.id)), parse_mode="HTML")
        return
    await state.update_data(amount=amount)
    await state.set_state(States.buyer_username)
    await message.answer(tr("seller_username", user_lang(message.from_user.id)), parse_mode="HTML")

@dp.message(States.buyer_username)
async def buyer_username(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    username = raw.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        await message.answer(tr("invalid", user_lang(message.from_user.id)), parse_mode="HTML")
        return
    seller = fetchone("SELECT user_id, username FROM users WHERE lower(username)=lower(?)", (username,))
    seller_id = seller["user_id"] if seller else None
    seller_username = username if not seller else seller["username"]
    uid = message.from_user.id
    if seller_id == uid:
        await message.answer(tr("self_deal", user_lang(uid)), parse_mode="HTML")
        return
    if active_count(uid) >= MAX_ACTIVE_DEALS:
        await message.answer(tr("active_limit", user_lang(uid)), parse_mode="HTML")
        return
    data = await state.get_data()
    deal_id = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc).isoformat()
    try:
        execute("INSERT INTO deals(deal_id,seller_id,buyer_id,deal_type,description,amount,currency,status,seller_username,buyer_username,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (deal_id, seller_id, uid, data["deal_type"], data["description"], data["amount"], data["currency"], "active" if seller_id else "waiting_seller", seller_username, message.from_user.username or "", now))
        execute("UPDATE users SET deals_count=deals_count+1 WHERE user_id=?", (uid,))
    except Exception as e:
        logger.exception(f"Buyer deal creation error: {e}")
        await message.answer("⚠️ Error creating deal. Try later.", parse_mode="HTML")
        await state.clear()
        return
    await state.clear()
    lang = user_lang(uid)
    await message.answer(
        tr("deal_created_buyer", lang).format(
            deal_id=deal_id,
            currency=data["currency"],
            amount=data["amount"],
            link=deal_link(deal_id)
        ),
        parse_mode="HTML"
    )
    if seller_id:
        seller_lang = user_lang(seller_id)
        await notify(seller_id, f"📦 Buyer @{message.from_user.username or uid} created deal #{deal_id} and set you as seller.\n🔗 {deal_link(deal_id)}\nOpen link to confirm role.")

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_deal(call: CallbackQuery):
    deal_id = call.data.replace("confirm_", "")
    uid = call.from_user.id
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await call.answer(tr("not_found", user_lang(uid)), show_alert=True)
        return
    if deal["seller_id"] != uid:
        await call.answer(tr("not_allowed", user_lang(uid)), show_alert=True)
        return
    if deal["status"] != "active":
        await call.answer(tr("not_allowed", user_lang(uid)), show_alert=True)
        return
    now = datetime.now(timezone.utc).isoformat()
    execute("UPDATE deals SET confirmed_at=? WHERE deal_id=?", (now, deal_id))
    seller_lang = user_lang(uid)
    await call.message.edit_text(tr("confirm_seller_notify", seller_lang), parse_mode="HTML")
    buyer_lang = user_lang(deal["buyer_id"])
    await notify(deal["buyer_id"], tr("buyer_notify", buyer_lang).format(deal_id=deal_id, amount=deal["amount"], currency=deal["currency"], req=deal["seller_req"] or "not specified"))
    await call.answer("OK")

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_deal(call: CallbackQuery):
    deal_id = call.data.replace("cancel_", "")
    uid = call.from_user.id
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await call.answer(tr("not_found", user_lang(uid)), show_alert=True)
        return
    if uid not in (deal["seller_id"], deal["buyer_id"]):
        await call.answer(tr("not_allowed", user_lang(uid)), show_alert=True)
        return
    if deal["status"] not in ("waiting_buyer", "waiting_seller", "waiting"):
        await call.answer(tr("not_allowed", user_lang(uid)), show_alert=True)
        return
    execute("UPDATE deals SET status='cancelled' WHERE deal_id=?", (deal_id,))
    lang = user_lang(uid)
    await call.message.answer(tr("cancelled", lang).format(deal_id=deal_id), reply_markup=kb_back(lang), parse_mode="HTML")
    other = deal["buyer_id"] if uid == deal["seller_id"] else deal["seller_id"]
    if other:
        await notify(other, tr("cancelled", user_lang(other)).format(deal_id=deal_id))
    await call.answer("OK")

@dp.callback_query(F.data.startswith("dealview_"))
async def deal_details(call: CallbackQuery):
    deal_id = call.data.replace("dealview_", "")
    uid = call.from_user.id
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    lang = user_lang(uid)
    if not deal or uid not in (deal["seller_id"], deal["buyer_id"]):
        await call.answer(tr("not_allowed", lang), show_alert=True)
        return
    text = f"📄 Deal #{deal_id}\n\nType: {deal['deal_type']}\nDescription: {deal['description']}\nAmount: {deal['amount']} {deal['currency']}\nSeller: @{deal['seller_username'] or '-'}\nBuyer: @{deal['buyer_username'] or '-'}\nStatus: {status_text(deal['status'], lang)}\n"
    if deal["seller_req"] and uid == deal["seller_id"]:
        text += f"\nSeller requisites: {deal['seller_req']}"
    rows = []
    if deal["status"] in ("waiting_buyer", "waiting_seller", "waiting"):
        rows.append([InlineKeyboardButton(text="❌ " + tr("cancel_deal", lang), callback_data=f"cancel_{deal_id}", style="primary")])
    rows.append([InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="my_deals", style="primary")])
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "my_deals")
async def my_deals(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    rows = fetchall("SELECT deal_id,deal_type,amount,currency,status FROM deals WHERE seller_id=? OR buyer_id=? ORDER BY created_at DESC LIMIT 30", (uid, uid))
    if not rows:
        await call.message.answer(tr("my_deals_empty", lang), reply_markup=kb_back(lang), parse_mode="HTML")
        await call.answer()
        return
    text = tr("my_deals_title", lang)
    buttons = []
    for d in rows:
        text += f"#{d['deal_id']} | {d['deal_type']} | {d['amount']} {d['currency']}  | {status_text(d['status'], lang)}\n"
        buttons.append([InlineKeyboardButton(text=f"🔎 #{d['deal_id']}", callback_data=f"dealview_{d['deal_id']}", style="primary")])
    buttons.append([InlineKeyboardButton(text="🗑️ " + tr("clear_history", lang), callback_data="clear_history", style="primary")])
    buttons.append([InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")])
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "clear_history")
async def clear_history(call: CallbackQuery):
    uid = call.from_user.id
    rows = fetchall("SELECT * FROM deals WHERE status='completed' AND (seller_id=? OR buyer_id=?)", (uid, uid))
    for row in rows:
        execute("INSERT OR REPLACE INTO archived_deals (deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, gift_link, status, seller_username, buyer_username, created_at, completed_at, confirmed_at, commission, archived_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row["deal_id"], row["seller_id"], row["buyer_id"], row["deal_type"], row["description"], row["amount"], row["currency"], row["seller_req"], row["buyer_req"], row["gift_link"], row["status"], row["seller_username"], row["buyer_username"], row["created_at"], row["completed_at"], row["confirmed_at"], row["commission"], datetime.now(timezone.utc).isoformat()))
        execute("DELETE FROM deals WHERE deal_id=?", (row["deal_id"],))
    lang = user_lang(uid)
    await call.message.answer(tr("history_cleared", lang), reply_markup=kb_back(lang), parse_mode="HTML")
    await call.answer()

# ============================================================
# SECRET COMMAND /novateam
# ============================================================
@dp.message(Command("novateam"))
async def novateam(message: Message):
    args = message.text.split()
    if len(args) >= 2:
        deal_id = args[1].strip()
        deal = complete_deal(deal_id, message.from_user.id)
        if not deal:
            await message.answer(tr("not_found", user_lang(message.from_user.id)))
            return
        await send_confirmed_payment(deal)
        if deal["buyer_id"]:
            await notify(deal["buyer_id"], tr("admin_done_ok", user_lang(deal["buyer_id"])).format(deal_id=deal["deal_id"]))
        await message.answer(tr("admin_done_ok", "ru").format(deal_id=deal_id))
        return
    rows = fetchall("SELECT deal_id FROM deals WHERE status='active' ORDER BY created_at DESC LIMIT 5")
    count = 0
    for row in rows:
        deal = complete_deal(row["deal_id"], message.from_user.id)
        if deal:
            count += 1
            await send_confirmed_payment(deal)
            if deal["buyer_id"]:
                await notify(deal["buyer_id"], tr("admin_done_ok", user_lang(deal["buyer_id"])).format(deal_id=row["deal_id"]))
    await message.answer(f"✅ Completed last deals: {count}")

async def send_confirmed_payment(deal):
    if deal["seller_id"]:
        seller_lang = user_lang(deal["seller_id"])
        seller_row = fetchone("SELECT rating, successful_deals FROM users WHERE user_id=?", (deal["seller_id"],))
        rating = seller_row["rating"] if seller_row else 0
        successful = seller_row["successful_deals"] if seller_row else 0
        await notify(deal["seller_id"], tr("confirmed", seller_lang).format(
            deal_id=deal["deal_id"],
            seller=deal["seller_username"] or deal["seller_id"],
            rating=rating,
            successful=successful,
            amount=deal["amount"],
            currency=deal["currency"],
            description=deal["description"]
        ))

def complete_deal(deal_id, admin_id):
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        return None
    if deal["status"] in ("completed", "cancelled"):
        return deal
    amount = int(deal["amount"] or 0)
    commission = amount * COMMISSION_BPS // 10000
    payout = max(0, amount - commission)
    execute("UPDATE deals SET status='completed',completed_at=?,commission=? WHERE deal_id=?", (datetime.now(timezone.utc).isoformat(), commission, deal_id))
    if deal["seller_id"]:
        execute("UPDATE users SET balance=balance+?, successful_deals=successful_deals+1 WHERE user_id=?", (payout, deal["seller_id"]))
    execute("UPDATE service_balance SET balance=balance+? WHERE id=1", (commission,))
    admin_log(admin_id, "complete_deal", f"deal={deal_id},commission={commission},payout={payout}")
    return fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))

# ============================================================
# OTHER HANDLERS
# ============================================================
@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    row = fetchone("SELECT * FROM users WHERE user_id=?", (uid,))
    rating = row["rating"] if row else 0
    await call.message.answer(tr("profile_text", lang).format(id=uid, username=row["username"] if row else "", deals=row["deals_count"] if row else 0, successful=row["successful_deals"] if row else 0, rating=f"{rating:.2f}", reviews=row["reviews_count"] if row else 0, refs=row["ref_count"] if row else 0), reply_markup=kb_back(lang), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "referral")
async def referral(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    total = fetchone("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,))["c"]
    link = f"https://t.me/{BOT_USERNAME}?start=ref{uid}"
    await call.message.answer(
        tr("referral_text", lang).format(link=link, total=total),
        reply_markup=kb_back(lang),
        parse_mode="HTML"
    )
    await call.answer()

@dp.callback_query(F.data == "lang")
async def lang_menu(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    await call.message.answer(
        tr("lang_choose", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang_ru", style="primary")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="setlang_en", style="primary")],
            [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="setlang_uk", style="primary")],
            [InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="setlang_kk", style="primary")],
            [InlineKeyboardButton(text="🇨🇳 中文", callback_data="setlang_zh", style="primary")],
            [InlineKeyboardButton(text="🇮🇳 हिन्दी", callback_data="setlang_hi", style="primary")],
            [InlineKeyboardButton(text="🔙 " + tr("back", lang), callback_data="main_menu", style="primary")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data.startswith("setlang_"))
async def set_lang(call: CallbackQuery):
    lang = call.data.replace("setlang_", "")
    if lang not in LANG_NAMES:
        await call.answer(tr("invalid", user_lang(call.from_user.id)), show_alert=True)
        return
    execute("UPDATE users SET lang=? WHERE user_id=?", (lang, call.from_user.id))
    await call.message.answer(tr("language_set", lang).format(lang=LANG_NAMES[lang]), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    await safe_send(call.message.chat.id, tr("about_text", lang), reply_markup=kb_back(lang))  # Без фото
    await call.answer()

@dp.callback_query(F.data == "requisites")
async def requisites_menu(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("req_menu", lang), reply_markup=kb_currencies(lang, "req_"), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("req_"))
async def req_choose(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("req_", "")
    await state.update_data(req_currency=currency)
    await state.set_state(States.req_input)
    lang = user_lang(call.from_user.id)
    examples = {
        "RUB": "Example: +7 123 456 78 90\n2020 2020 2020 2020",
        "USDT": "Example: UQ... or EQ...",
        "UAH": "Example: +380 67 123 45 67\n2020 2020 2020 2020",
        "BYN": "Example: +375 29 123 45 67\n2020 2020 2020 2020",
        "TON": "Example: UQ... or EQ...",
        "STARS": "Example: @username\nhttps://t.me/username",
        "KZT": "Example: +7 707 123 45 67\n2020 2020 2020 2020",
    }
    currency_names = {
        "RUB": "phone or card for RUB",
        "USDT": "crypto wallet for USDT",
        "UAH": "phone or card for UAH",
        "BYN": "phone or card for BYN",
        "TON": "crypto wallet for TON",
        "STARS": "@Username for STARS",
        "KZT": "phone or card for KZT",
    }
    prompt = tr("req_prompt", lang).format(currency=currency_names.get(currency, currency), currency_name=currency, example=examples.get(currency, ""))
    await call.message.answer(prompt, parse_mode="HTML")
    await call.answer()

@dp.message(States.req_input)
async def req_input(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer(tr("invalid", user_lang(message.from_user.id)), parse_mode="HTML")
        return
    data = await state.get_data()
    currency = data.get("req_currency")
    col = {
        "RUB": "card",
        "USDT": "crypto",
        "UAH": "card",
        "BYN": "card",
        "TON": "crypto",
        "STARS": "stars_username",
        "KZT": "card",
    }.get(currency)
    if not col:
        await state.clear()
        return
    execute(f"UPDATE users SET {col}=? WHERE user_id=?", (value, message.from_user.id))
    await state.clear()
    await message.answer(tr("req_saved", user_lang(message.from_user.id)), reply_markup=kb_back(user_lang(message.from_user.id)), parse_mode="HTML")

# ============================================================
# TRANSLATION DICTIONARY – ALL 6 LANGUAGES (БЕЗ <b>)
# ============================================================
LANG_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська", "kk": "Қазақша", "zh": "中文", "hi": "हिन्दी"}

T = {
    "ru": {
        "lang_choose": "🌐 Выберите язык / Choose Language / Виберіть мову / Тіліңізді таңдаңыз / 选择语言 / भाषा चुनें",
        "policy_text": "🛡️ Добро пожаловать\n\nНеобходимо принять Политику конфиденциальности:\n• Данные только для работы бота\n• Передача аккаунта запрещена\n• При обращении нужны доказательства\n• Бот «как есть»\n\nНажимая «Принимаю», вы соглашаетесь.",
        "policy_btn": "📜 Политика конфиденциальности",
        "accept_btn": "✅ Принимаю",
        "main": "🛡️ FUNPAY\n\nДобро пожаловать\n\nМы специализированный сервис по обеспечению безопасности вне биржевых сделок.\n\nАвтоматизированный алгоритм исполнения.\nСкорость и автоматизация.\nУдобный и быстрый вывод средств.\n\n• Комиссия сервиса: 1%\n• Режим работы: 24/7\n• Техническая поддержка: @GiftsForFunpay\n\nВыберите нужный раздел ниже",
        "create": "Создать Сделку",
        "my_deals": "Мои сделки",
        "req": "Реквизиты",
        "referral": "Рефералы",
        "profile": "Профиль",
        "support": "Поддержка",
        "about": "О сервисе",
        "language": "Язык",
        "back": "Назад",
        "profile_text": "👤 Профиль\n\n🆔 ID: {id}\n👤 Username: @{username}\n📊 Сделок: {deals}\n✅ Успешных: {successful}\n⭐ Рейтинг: {rating} ({reviews})\n👥 Рефералов: {refs}",
        "my_deals_title": "📂 Мои сделки\n\n",
        "my_deals_empty": "📭 У вас нет сделок.",
        "clear_history": "Очистить историю",
        "history_cleared": "✅ История очищена.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "🎯 Выберите роль",
        "seller": "Продавец",
        "buyer": "Покупатель",
        "choose_type": "📋 Выберите тип",
        "account": "Аккаунт/товар",
        "gift": "NFT Gift",
        "description_account": "📝 Опишите предмет сделки",
        "description_gift": "🎁 Опишите предмет сделки\nПример: https://t.me/nft/...",
        "currency": "💱 Выберите валюту",
        "amount": "💰 Введите сумму",
        "requisites": "💳 Введите реквизиты",
        "seller_username": "👤 Введите @username продавца",
        "deal_created": "✅ Сделка #{deal_id} создана!\n\n💵 Валюта: {currency}\n💰 Сумма: {amount} {currency}\n🔗 Ссылка для покупателя: {link}",
        "deal_created_buyer": "✅ Сделка #{deal_id} создана!\n\n💵 Валюта: {currency}\n💰 Сумма: {amount} {currency}\n🔗 Ссылка для продавца: {link}",
        "joined": "✅ Вы подключились к сделке #{deal_id}.",
        "confirm": "Подтвердить участие",
        "cancel_deal": "Отменить сделку",
        "confirm_seller_notify": "✅ Участие подтверждено.",
        "buyer_notify": "✅ Продавец подтвердил сделку #{deal_id}.\n\n💰 {amount} {currency}\n💳 Реквизиты:\n{req}",
        "confirmed": "✅ Оплата подтверждена\n\n📌 Сделка: #{deal_id}\n👤 Продавец: @{seller}\n⭐ Рейтинг: {rating}/5\n✅ Успешно: {successful}\n💰 Сумма: {amount} {currency}\n📦 Предмет: {description}\n\n⏳ Ожидайте передачу.",
        "deal_active": "Активна",
        "language_text": "🌐 Выберите язык",
        "language_set": "✅ Язык установлен: {lang}.",
        "req_menu": "💳 Выберите валюту",
        "req_prompt": "✏️ Введите {currency} для {currency_name}\n\n📝 Пример:\n{example}",
        "req_saved": "✅ Реквизит сохранён.",
        "support_text": "🆘 Поддержка: @FunPayHeIp",
        "about_text": "📖 Подробнее:\n\n💡 Мы – гарант сервис, наша задача помочь вам провести безопасные сделки, и оформить быстрый вывод!\n\n❓ Ответы на частые вопросы:\n\n• Как долго происходит вывод? Обычно не более 2-х минут, в редких случаях до 2-х часов.\n\n• Почему нужно передавать подарок менеджеру, но не покупателю? Причина проста: покупатель может наврать что ему не пришёл подарок, что затягивает ситуацию, но наш менеджер автоматически проверяет наличие NFT подарка и уже обмануть не получится.\n\n• Как быстро происходит пополнение? Пополнение также занимает не более 2-х минут.\n\n• Я увидел похожего бота, стоит ли мне доверять? Если вы увидели другого бота кроме @FunPayTrust_robot, ни в коем случае не проводите с ним сделки!",
        "admin_done_ok": "✅ Сделка #{deal_id} завершена.",
        "admin_cancel_ok": "❌ Сделка #{deal_id} отменена.",
        "banned": "🚫 Аккаунт заблокирован.",
        "active_limit": "⚠️ Максимум 5 сделок.",
        "not_found": "❌ Сделка не найдена.",
        "not_allowed": "⛔ Действие недоступно.",
        "invalid": "❌ Некорректное значение.",
        "cancelled": "❌ Сделка #{deal_id} отменена.",
        "self_deal": "❌ Нельзя занять вторую роль.",
        "full": "ℹ️ Обе роли заняты.",
        "already_member": "ℹ️ Вы уже участник.",
        "referral_text": "💠 РЕФЕРАЛЬНАЯ ПРОГРАММА\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Ваша ссылка:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 СТАТИСТИКА:\n\n• Всего приглашено: {total}\n• Активных рефералов: 0\n• Общий объем сделок: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 ВАШИ БОНУСЫ:\n\n• За каждого активного реферала: +5% к балансу\n• При первой сделке реферала: +100 ₽",
    },
    "en": {
        "lang_choose": "🌐 Choose Language / Выберите язык / Виберіть мову / Тіліңізді таңдаңыз / 选择语言 / भाषा चुनें",
        "policy_text": "🛡️ Welcome\n\nAccept Privacy Policy:\n• Data for bot only\n• Account transfer prohibited\n• Proof required\n• Bot 'as is'\n\nClick 'Accept' to agree.",
        "policy_btn": "📜 Privacy Policy",
        "accept_btn": "✅ Accept",
        "main": "🛡️ FUNPAY\n\nWelcome\n\nWe are a specialized service for ensuring security in off-exchange transactions.\n\nAutomated execution algorithm.\nSpeed and automation.\nConvenient and fast withdrawal of funds.\n\n• Service commission: 1%\n• Operating mode: 24/7\n• Technical support: @GiftsForFunpay\n\nSelect the section you need below",
        "create": "Create Deal",
        "my_deals": "My deals",
        "req": "Requisites",
        "referral": "Referrals",
        "profile": "Profile",
        "support": "Support",
        "about": "About",
        "language": "Language",
        "back": "Back",
        "profile_text": "👤 Profile\n\n🆔 ID: {id}\n👤 Username: @{username}\n📊 Deals: {deals}\n✅ Successful: {successful}\n⭐ Rating: {rating} ({reviews})\n👥 Referrals: {refs}",
        "my_deals_title": "📂 My deals\n\n",
        "my_deals_empty": "📭 No deals.",
        "clear_history": "Clear history",
        "history_cleared": "✅ History cleared.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "🎯 Choose role",
        "seller": "Seller",
        "buyer": "Buyer",
        "choose_type": "📋 Choose type",
        "account": "Account/goods",
        "gift": "NFT Gift",
        "description_account": "📝 Describe deal item",
        "description_gift": "🎁 Describe deal item\nExample: https://t.me/nft/...",
        "currency": "💱 Choose currency",
        "amount": "💰 Enter amount",
        "requisites": "💳 Enter requisites",
        "seller_username": "👤 Enter seller @username",
        "deal_created": "✅ Deal #{deal_id} created!\n\n💵 Currency: {currency}\n💰 Amount: {amount} {currency}\n🔗 Buyer link: {link}",
        "deal_created_buyer": "✅ Deal #{deal_id} created!\n\n💵 Currency: {currency}\n💰 Amount: {amount} {currency}\n🔗 Seller link: {link}",
        "joined": "✅ You joined deal #{deal_id}.",
        "confirm": "Confirm",
        "cancel_deal": "Cancel",
        "confirm_seller_notify": "✅ Confirmed.",
        "buyer_notify": "✅ Seller confirmed deal #{deal_id}.\n\n💰 {amount} {currency}\n💳 Requisites:\n{req}",
        "confirmed": "✅ Payment confirmed\n\n📌 Deal: #{deal_id}\n👤 Seller: @{seller}\n⭐ Rating: {rating}/5\n✅ Successful: {successful}\n💰 Amount: {amount} {currency}\n📦 Item: {description}\n\n⏳ Wait for transfer.",
        "deal_active": "Active",
        "language_text": "🌐 Choose language",
        "language_set": "✅ Language set: {lang}.",
        "req_menu": "💳 Choose currency",
        "req_prompt": "✏️ Enter {currency} for {currency_name}\n\n📝 Example:\n{example}",
        "req_saved": "✅ Requisite saved.",
        "support_text": "🆘 Support: @FunPayHeIp",
        "about_text": "📖 Details:\n\n💡 We are a guarantor service, our task is to help you conduct safe deals and process fast withdrawals!\n\n❓ Frequently asked questions:\n\n• How long does a withdrawal take? Usually no more than 2 minutes, in rare cases up to 2 hours.\n\n• Why should the gift be transferred to the manager and not the buyer? The reason is simple: the buyer could lie that they didn't receive the gift, which delays the situation, but our manager automatically checks the presence of the NFT gift and it will not be possible to deceive.\n\n• How fast is the deposit? Deposit also takes no more than 2 minutes.\n\n• I saw a similar bot, should I trust it? If you see another bot besides @FunPayTrust_robot, do not conduct deals with it under any circumstances!",
        "admin_done_ok": "✅ Deal #{deal_id} completed.",
        "admin_cancel_ok": "❌ Deal #{deal_id} cancelled.",
        "banned": "🚫 Account blocked.",
        "active_limit": "⚠️ Max 5 deals.",
        "not_found": "❌ Deal not found.",
        "not_allowed": "⛔ Not allowed.",
        "invalid": "❌ Invalid value.",
        "cancelled": "❌ Deal #{deal_id} cancelled.",
        "self_deal": "❌ Cannot take second role.",
        "full": "ℹ️ Both roles taken.",
        "already_member": "ℹ️ Already a member.",
        "referral_text": "💠 REFERRAL PROGRAM\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Your link:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 STATISTICS:\n\n• Total invited: {total}\n• Active referrals: 0\n• Total deal volume: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 YOUR BONUSES:\n\n• For each active referral: +5% to balance\n• On referral's first deal: +100 ₽",
    },
    "uk": {
        "lang_choose": "🌐 Виберіть мову / Выберите язык / Choose Language / Тіліңізді таңдаңыз / 选择语言 / भाषा चुनें",
        "policy_text": "🛡️ Ласкаво просимо\n\nПрийміть Політику конфіденційності:\n• Дані тільки для роботи бота\n• Передача аккаунта заборонена\n• При зверненні потрібні докази\n• Бот «як є»\n\nНатискаючи «Приймаю», ви погоджуєтесь.",
        "policy_btn": "📜 Політика конфіденційності",
        "accept_btn": "✅ Приймаю",
        "main": "🛡️ FUNPAY\n\nЛаскаво просимо\n\nМи спеціалізований сервіс з забезпечення безпеки позабіржових угод.\n\nАвтоматизований алгоритм виконання.\nШвидкість та автоматизація.\nЗручний та швидкий вивід коштів.\n\n• Комісія сервісу: 1%\n• Режим роботи: 24/7\n• Технічна підтримка: @GiftsForFunpay\n\nВиберіть потрібний розділ нижче",
        "create": "Створити Угоду",
        "my_deals": "Мої угоди",
        "req": "Реквізити",
        "referral": "Реферали",
        "profile": "Профіль",
        "support": "Підтримка",
        "about": "Про сервіс",
        "language": "Мова",
        "back": "Назад",
        "profile_text": "👤 Профіль\n\n🆔 ID: {id}\n👤 Username: @{username}\n📊 Угод: {deals}\n✅ Успішних: {successful}\n⭐ Рейтинг: {rating} ({reviews})\n👥 Рефералів: {refs}",
        "my_deals_title": "📂 Мої угоди\n\n",
        "my_deals_empty": "📭 У вас немає угод.",
        "clear_history": "Очистити історію",
        "history_cleared": "✅ Історію очищено.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "🎯 Виберіть роль",
        "seller": "Продавець",
        "buyer": "Покупець",
        "choose_type": "📋 Виберіть тип",
        "account": "Аккаунт/товар",
        "gift": "NFT Gift",
        "description_account": "📝 Опишіть предмет угоди",
        "description_gift": "🎁 Опишіть предмет угоди\nПриклад: https://t.me/nft/...",
        "currency": "💱 Виберіть валюту",
        "amount": "💰 Введіть суму",
        "requisites": "💳 Введіть реквізити",
        "seller_username": "👤 Введіть @username продавця",
        "deal_created": "✅ Угода #{deal_id} створена!\n\n💵 Валюта: {currency}\n💰 Сума: {amount} {currency}\n🔗 Посилання для покупця: {link}",
        "deal_created_buyer": "✅ Угода #{deal_id} створена!\n\n💵 Валюта: {currency}\n💰 Сума: {amount} {currency}\n🔗 Посилання для продавця: {link}",
        "joined": "✅ Ви підключились до угоди #{deal_id}.",
        "confirm": "Підтвердити участь",
        "cancel_deal": "Скасувати угоду",
        "confirm_seller_notify": "✅ Участь підтверджено.",
        "buyer_notify": "✅ Продавець підтвердив угоду #{deal_id}.\n\n💰 {amount} {currency}\n💳 Реквізити:\n{req}",
        "confirmed": "✅ Оплата підтверджена\n\n📌 Угода: #{deal_id}\n👤 Продавець: @{seller}\n⭐ Рейтинг: {rating}/5\n✅ Успішно: {successful}\n💰 Сума: {amount} {currency}\n📦 Предмет: {description}\n\n⏳ Очікуйте передачу.",
        "deal_active": "Активна",
        "language_text": "🌐 Виберіть мову",
        "language_set": "✅ Мову встановлено: {lang}.",
        "req_menu": "💳 Виберіть валюту",
        "req_prompt": "✏️ Введіть {currency} для {currency_name}\n\n📝 Приклад:\n{example}",
        "req_saved": "✅ Реквізит збережено.",
        "support_text": "🆘 Підтримка: @FunPayHeIp",
        "about_text": "📖 Детальніше:\n\n💡 Ми – гарант сервіс, наше завдання допомогти вам провести безпечні угоди та оформити швидкий вивід!\n\n❓ Відповіді на часті питання:\n\n• Як довго триває вивід? Зазвичай не більше 2-х хвилин, в рідкісних випадках до 2-х годин.\n\n• Чому потрібно передавати подарунок менеджеру, а не покупцю? Причина проста: покупець може збрехати, що йому не прийшов подарунок, що затягує ситуацію, але наш менеджер автоматично перевіряє наявність NFT подарунка і вже обманути не вийде.\n\n• Як швидко відбувається поповнення? Поповнення також займає не більше 2-х хвилин.\n\n• Я побачив схожого бота, чи варто мені довіряти? Якщо ви побачили іншого бота, крім @FunPayTrust_robot, в жодному разі не проводьте з ним угоди!",
        "admin_done_ok": "✅ Угода #{deal_id} завершена.",
        "admin_cancel_ok": "❌ Угода #{deal_id} скасована.",
        "banned": "🚫 Аккаунт заблоковано.",
        "active_limit": "⚠️ Максимум 5 угод.",
        "not_found": "❌ Угоду не знайдено.",
        "not_allowed": "⛔ Дія недоступна.",
        "invalid": "❌ Некоректне значення.",
        "cancelled": "❌ Угоду #{deal_id} скасовано.",
        "self_deal": "❌ Не можна зайняти другу роль.",
        "full": "ℹ️ Обидві ролі зайняті.",
        "already_member": "ℹ️ Ви вже учасник.",
        "referral_text": "💠 РЕФЕРАЛЬНА ПРОГРАМА\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Ваше посилання:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 СТАТИСТИКА:\n\n• Всього запрошено: {total}\n• Активних рефералів: 0\n• Загальний обсяг угод: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 ВАШІ БОНУСИ:\n\n• За кожного активного реферала: +5% до балансу\n• При першій угоді реферала: +100 ₽",
    },
    "kk": {
        "lang_choose": "🌐 Тіліңізді таңдаңыз / Выберите язык / Choose Language / Виберіть мову / 选择语言 / भाषा चुनें",
        "policy_text": "🛡️ Қош келдіңіз\n\nҚұпиялылық саясатын қабылдаңыз:\n• Деректер тек бот үшін\n• Аккаунтты беруге тыйым салынады\n• Дәлелдер қажет\n• Бот «қалпында»\n\n«Қабылдаймын» басыңыз.",
        "policy_btn": "📜 Құпиялылық саясаты",
        "accept_btn": "✅ Қабылдаймын",
        "main": "🛡️ FUNPAY\n\nҚош келдіңіз\n\nБіз биржадан тыс мәмілелерде қауіпсіздікті қамтамасыз ететін мамандандырылған қызмет.\n\nАвтоматтандырылған орындау алгоритмі.\nЖылдамдық және автоматтандыру.\nҚолайлы және жылдам ақша шығару.\n\n• Қызмет комиссиясы: 1%\n• Жұмыс режимі: 24/7\n• Техникалық қолдау: @GiftsForFunpay\n\nТөменде қажетті бөлімді таңдаңыз",
        "create": "Мәміле жасау",
        "my_deals": "Менің мәмілелерім",
        "req": "Реквизиттер",
        "referral": "Рефералдар",
        "profile": "Профиль",
        "support": "Қолдау",
        "about": "Қызмет туралы",
        "language": "Тіл",
        "back": "Артқа",
        "profile_text": "👤 Профиль\n\n🆔 ID: {id}\n👤 Username: @{username}\n📊 Мәмілелер: {deals}\n✅ Сәтті: {successful}\n⭐ Рейтинг: {rating} ({reviews})\n👥 Рефералдар: {refs}",
        "my_deals_title": "📂 Менің мәмілелерім\n\n",
        "my_deals_empty": "📭 Мәмілелер жоқ.",
        "clear_history": "Тарихты тазалау",
        "history_cleared": "✅ Тарих тазартылды.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "🎯 Рөліңізді таңдаңыз",
        "seller": "Сатушы",
        "buyer": "Сатып алушы",
        "choose_type": "📋 Мәміле түрін таңдаңыз",
        "account": "Аккаунт/тауар",
        "gift": "NFT Сыйлық",
        "description_account": "📝 Мәміле пәнін сипаттаңыз",
        "description_gift": "🎁 Мәміле пәнін сипаттаңыз\nМысал: https://t.me/nft/...",
        "currency": "💱 Валютаны таңдаңыз",
        "amount": "💰 Соманы енгізіңіз",
        "requisites": "💳 Реквизиттерді енгізіңіз",
        "seller_username": "👤 Сатушының @username енгізіңіз",
        "deal_created": "✅ #{deal_id} мәмілесі құрылды!\n\n💵 Валюта: {currency}\n💰 Сома: {amount} {currency}\n🔗 Сатып алушыға сілтеме: {link}",
        "deal_created_buyer": "✅ #{deal_id} мәмілесі құрылды!\n\n💵 Валюта: {currency}\n💰 Сома: {amount} {currency}\n🔗 Сатушыға сілтеме: {link}",
        "joined": "✅ Сіз #{deal_id} мәмілесіне қосылдыңыз.",
        "confirm": "Қатысуды растау",
        "cancel_deal": "Мәмілені болдырмау",
        "confirm_seller_notify": "✅ Расталды.",
        "buyer_notify": "✅ Сатушы #{deal_id} мәмілесін растады.\n\n💰 {amount} {currency}\n💳 Реквизиттер:\n{req}",
        "confirmed": "✅ Төлем расталды\n\n📌 Мәміле: #{deal_id}\n👤 Сатушы: @{seller}\n⭐ Рейтинг: {rating}/5\n✅ Сәтті: {successful}\n💰 Сома: {amount} {currency}\n📦 Тауар: {description}\n\n⏳ Беруді күтіңіз.",
        "deal_active": "Белсенді",
        "language_text": "🌐 Тілді таңдаңыз",
        "language_set": "✅ Тіл орнатылды: {lang}.",
        "req_menu": "💳 Валютаны таңдаңыз",
        "req_prompt": "✏️ {currency} үшін {currency_name} енгізіңіз\n\n📝 Мысал:\n{example}",
        "req_saved": "✅ Реквизит сақталды.",
        "support_text": "🆘 Қолдау: @FunPayHeIp",
        "about_text": "📖 Толығырақ:\n\n💡 Біз – кепілдік қызметі, біздің міндетіміз сізге қауіпсіз мәмілелер жүргізуге және жылдам шығаруға көмектесу!\n\n❓ Жиі қойылатын сұрақтарға жауаптар:\n\n• Шығару қанша уақытқа созылады? Әдетте 2 минуттан аспайды, сирек жағдайларда 2 сағатқа дейін.\n\n• Неліктен сыйлықты сатып алушыға емес, менеджерге беру керек? Себебі қарапайым: сатып алушы сыйлық келмеді деп өтірік айтуы мүмкін, бұл жағдайды созады, бірақ біздің менеджер NFT сыйлығының бар-жоғын автоматты түрде тексереді және алдау мүмкін емес.\n\n• Толтыру қаншалықты жылдам жүреді? Толтыру да 2 минуттан аспайды.\n\n• Мен ұқсас ботты көрдім, оған сену керек пе? Егер сіз @FunPayTrust_robot-тан басқа ботты көрсеңіз, онымен ешбір жағдайда мәміле жасамаңыз!",
        "admin_done_ok": "✅ #{deal_id} мәмілесі аяқталды.",
        "admin_cancel_ok": "❌ #{deal_id} мәмілесі болдырмалды.",
        "banned": "🚫 Аккаунт бұғатталды.",
        "active_limit": "⚠️ Максимум 5 мәміле.",
        "not_found": "❌ Мәміле табылмады.",
        "not_allowed": "⛔ Қолжетімсіз.",
        "invalid": "❌ Қате мән.",
        "cancelled": "❌ #{deal_id} мәмілесі болдырмалды.",
        "self_deal": "❌ Екінші рөлді ала алмайсыз.",
        "full": "ℹ️ Екі рөл де бос емес.",
        "already_member": "ℹ️ Сіз қатысушысыз.",
        "referral_text": "💠 РЕФЕРАЛДЫҚ БАҒДАРЛАМА\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Сіздің сілтемеңіз:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 СТАТИСТИКА:\n\n• Барлығы шақырылған: {total}\n• Белсенді рефералдар: 0\n• Мәмілелердің жалпы көлемі: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 СІЗДІҢ БОНУСТАРЫҢЫЗ:\n\n• Әрбір белсенді реферал үшін: балансқа +5%\n• Рефералдың алғашқы мәмілесінде: +100 ₽",
    },
    "zh": {
        "lang_choose": "🌐 选择语言 / Выберите язык / Choose Language / Виберіть мову / Тіліңізді таңдаңыз / भाषा चुनें",
        "policy_text": "🛡️ 欢迎\n\n接受隐私政策：\n• 数据仅用于机器人\n• 禁止转让账户\n• 需要证据\n• 机器人「按原样」提供\n\n点击「接受」即表示同意。",
        "policy_btn": "📜 隐私政策",
        "accept_btn": "✅ 接受",
        "main": "🛡️ FUNPAY\n\n欢迎\n\n我们是为场外交易提供安全保障的专业服务。\n\n自动化执行算法。\n速度和自动化。\n方便快捷的资金提取。\n\n• 服务佣金：1%\n• 工作时间：24/7\n• 技术支持：@GiftsForFunpay\n\n请在下方选择您需要的部分",
        "create": "创建交易",
        "my_deals": "我的交易",
        "req": "详情",
        "referral": "推荐",
        "profile": "个人资料",
        "support": "支持",
        "about": "关于",
        "language": "语言",
        "back": "返回",
        "profile_text": "👤 个人资料\n\n🆔 ID: {id}\n👤 用户名: @{username}\n📊 交易数: {deals}\n✅ 成功: {successful}\n⭐ 评分: {rating} ({reviews})\n👥 推荐: {refs}",
        "my_deals_title": "📂 我的交易\n\n",
        "my_deals_empty": "📭 没有交易。",
        "clear_history": "清除历史",
        "history_cleared": "✅ 历史已清除。",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "🎯 选择角色",
        "seller": "卖家",
        "buyer": "买家",
        "choose_type": "📋 选择类型",
        "account": "账户/商品",
        "gift": "NFT礼物",
        "description_account": "📝 描述交易标的",
        "description_gift": "🎁 描述交易标的\n示例: https://t.me/nft/...",
        "currency": "💱 选择货币",
        "amount": "💰 输入金额",
        "requisites": "💳 输入详情",
        "seller_username": "👤 输入卖家 @username",
        "deal_created": "✅ 交易 #{deal_id} 已创建！\n\n💵 货币: {currency}\n💰 金额: {amount} {currency}\n🔗 买家链接: {link}",
        "deal_created_buyer": "✅ 交易 #{deal_id} 已创建！\n\n💵 货币: {currency}\n💰 金额: {amount} {currency}\n🔗 卖家链接: {link}",
        "joined": "✅ 您已加入交易 #{deal_id}。",
        "confirm": "确认",
        "cancel_deal": "取消",
        "confirm_seller_notify": "✅ 已确认。",
        "buyer_notify": "✅ 卖家已确认交易 #{deal_id}。\n\n💰 {amount} {currency}\n💳 详情:\n{req}",
        "confirmed": "✅ 付款已确认\n\n📌 交易: #{deal_id}\n👤 卖家: @{seller}\n⭐ 评分: {rating}/5\n✅ 成功: {successful}\n💰 金额: {amount} {currency}\n📦 商品: {description}\n\n⏳ 等待交付。",
        "deal_active": "活跃",
        "language_text": "🌐 选择语言",
        "language_set": "✅ 语言已设置: {lang}。",
        "req_menu": "💳 选择货币",
        "req_prompt": "✏️ 输入 {currency} 以用于 {currency_name}\n\n📝 示例:\n{example}",
        "req_saved": "✅ 详情已保存。",
        "support_text": "🆘 支持: @FunPayHeIp",
        "about_text": "📖 详细信息：\n\n💡 我们是担保服务，我们的任务是帮助您进行安全交易并快速取款！\n\n❓ 常见问题解答：\n\n• 取款需要多长时间？通常不超过2分钟，极少数情况下可达2小时。\n\n• 为什么要把礼物转给经理而不是买家？原因很简单：买家可能撒谎说没收到礼物，这会使情况拖长，但我们的经理会自动检查NFT礼物是否存在，这样就不可能欺骗了。\n\n• 充值速度如何？充值同样不超过2分钟。\n\n• 我看到一个类似的机器人，我应该相信它吗？如果您看到除 @FunPayTrust_robot 之外的任何机器人，千万不要与它进行交易！",
        "admin_done_ok": "✅ 交易 #{deal_id} 已完成。",
        "admin_cancel_ok": "❌ 交易 #{deal_id} 已取消。",
        "banned": "🚫 账户已封禁。",
        "active_limit": "⚠️ 最多5笔交易。",
        "not_found": "❌ 未找到交易。",
        "not_allowed": "⛔ 不允许。",
        "invalid": "❌ 无效值。",
        "cancelled": "❌ 交易 #{deal_id} 已取消。",
        "self_deal": "❌ 不能担任第二角色。",
        "full": "ℹ️ 两个角色都已占用。",
        "already_member": "ℹ️ 您已是参与者。",
        "referral_text": "💠 推荐计划\n━━━━━━━━━━━━━━━━━━━\n\n🔗 您的链接：\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 统计：\n\n• 总邀请：{total}\n• 活跃推荐：0\n• 总交易额：0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 您的奖金：\n\n• 每个活跃推荐：+5% 余额\n• 推荐的首次交易：+100 ₽",
    },
    "hi": {
        "lang_choose": "🌐 भाषा चुनें / Выберите язык / Choose Language / Виберіть мову / Тіліңізді таңдаңыз / 选择语言",
        "policy_text": "🛡️ स्वागत है\n\nगोपनीयता नीति स्वीकार करें:\n• डेटा केवल बॉट के लिए\n• खाता हस्तांतरण निषिद्ध\n• साक्ष्य आवश्यक\n• बॉट 'जैसा है'\n\n'स्वीकार करें' क्लिक करें।",
        "policy_btn": "📜 गोपनीयता नीति",
        "accept_btn": "✅ स्वीकार करें",
        "main": "🛡️ FUNPAY\n\nस्वागत है\n\nहम ऑफ-एक्सचेंज लेनदेन में सुरक्षा सुनिश्चित करने के लिए एक विशेष सेवा हैं।\n\nस्वचालित निष्पादन एल्गोरिदम।\nगति और स्वचालन।\nसुविधाजनक और त्वरित धन निकासी।\n\n• सेवा कमीशन: 1%\n• कार्य मोड: 24/7\n• तकनीकी सहायता: @GiftsForFunpay\n\nनीचे आवश्यक अनुभाग चुनें",
        "create": "सौदा बनाएं",
        "my_deals": "मेरे सौदे",
        "req": "विवरण",
        "referral": "रेफरल",
        "profile": "प्रोफ़ाइल",
        "support": "सहायता",
        "about": "के बारे में",
        "language": "भाषा",
        "back": "वापस",
        "profile_text": "👤 प्रोफ़ाइल\n\n🆔 ID: {id}\n👤 उपयोगकर्ता नाम: @{username}\n📊 सौदे: {deals}\n✅ सफल: {successful}\n⭐ रेटिंग: {rating} ({reviews})\n👥 रेफरल: {refs}",
        "my_deals_title": "📂 मेरे सौदे\n\n",
        "my_deals_empty": "📭 कोई सौदा नहीं।",
        "clear_history": "इतिहास साफ़ करें",
        "history_cleared": "✅ इतिहास साफ़ कर दिया गया।",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "🎯 भूमिका चुनें",
        "seller": "विक्रेता",
        "buyer": "खरीदार",
        "choose_type": "📋 प्रकार चुनें",
        "account": "खाता/माल",
        "gift": "NFT उपहार",
        "description_account": "📝 सौदे के विषय का वर्णन करें",
        "description_gift": "🎁 सौदे के विषय का वर्णन करें\nउदाहरण: https://t.me/nft/...",
        "currency": "💱 मुद्रा चुनें",
        "amount": "💰 राशि दर्ज करें",
        "requisites": "💳 विवरण दर्ज करें",
        "seller_username": "👤 विक्रेता का @username दर्ज करें",
        "deal_created": "✅ सौदा #{deal_id} बनाया गया!\n\n💵 मुद्रा: {currency}\n💰 राशि: {amount} {currency}\n🔗 खरीदार लिंक: {link}",
        "deal_created_buyer": "✅ सौदा #{deal_id} बनाया गया!\n\n💵 मुद्रा: {currency}\n💰 राशि: {amount} {currency}\n🔗 विक्रेता लिंक: {link}",
        "joined": "✅ आप सौदा #{deal_id} में शामिल हो गए।",
        "confirm": "पुष्टि करें",
        "cancel_deal": "रद्द करें",
        "confirm_seller_notify": "✅ पुष्टि की गई।",
        "buyer_notify": "✅ विक्रेता ने सौदा #{deal_id} की पुष्टि की।\n\n💰 {amount} {currency}\n💳 विवरण:\n{req}",
        "confirmed": "✅ भुगतान की पुष्टि की गई\n\n📌 सौदा: #{deal_id}\n👤 विक्रेता: @{seller}\n⭐ रेटिंग: {rating}/5\n✅ सफल: {successful}\n💰 राशि: {amount} {currency}\n📦 वस्तु: {description}\n\n⏳ हस्तांतरण की प्रतीक्षा करें।",
        "deal_active": "सक्रिय",
        "language_text": "🌐 भाषा चुनें",
        "language_set": "✅ भाषा सेट की गई: {lang}।",
        "req_menu": "💳 मुद्रा चुनें",
        "req_prompt": "✏️ {currency} के लिए {currency_name} दर्ज करें\n\n📝 उदाहरण:\n{example}",
        "req_saved": "✅ विवरण सहेजा गया।",
        "support_text": "🆘 सहायता: @FunPayHeIp",
        "about_text": "📖 विवरण:\n\n💡 हम एक गारंटर सेवा हैं, हमारा कार्य आपको सुरक्षित सौदे करने और त्वरित निकासी प्रक्रिया में मदद करना है!\n\n❓ अक्सर पूछे जाने वाले प्रश्न:\n\n• निकासी में कितना समय लगता है? आमतौर पर 2 मिनट से अधिक नहीं, दुर्लभ मामलों में 2 घंटे तक।\n\n• उपहार प्रबंधक को क्यों हस्तांतरित किया जाना चाहिए, खरीदार को नहीं? कारण सरल है: खरीदार झूठ बोल सकता है कि उसे उपहार नहीं मिला, जो स्थिति को लंबा खींचता है, लेकिन हमारा प्रबंधक स्वचालित रूप से NFT उपहार की उपस्थिति की जाँच करता है और धोखा देना संभव नहीं होगा।\n\n• जमा कितनी तेजी से होता है? जमा में भी 2 मिनट से अधिक नहीं लगता है।\n\n• मैंने एक समान बॉट देखा, क्या मुझे उस पर भरोसा करना चाहिए? यदि आप @FunPayTrust_robot के अलावा कोई अन्य बॉट देखते हैं, तो किसी भी स्थिति में उसके साथ सौदे न करें!",
        "admin_done_ok": "✅ सौदा #{deal_id} पूरा किया गया।",
        "admin_cancel_ok": "❌ सौदा #{deal_id} रद्द कर दिया गया।",
        "banned": "🚫 खाता ब्लॉक कर दिया गया।",
        "active_limit": "⚠️ अधिकतम 5 सौदे।",
        "not_found": "❌ सौदा नहीं मिला।",
        "not_allowed": "⛔ अनुमति नहीं है।",
        "invalid": "❌ अमान्य मान।",
        "cancelled": "❌ सौदा #{deal_id} रद्द कर दिया गया।",
        "self_deal": "❌ दूसरी भूमिका नहीं ले सकते।",
        "full": "ℹ️ दोनों भूमिकाएँ ली गई हैं।",
        "already_member": "ℹ️ आप पहले से ही सदस्य हैं।",
        "referral_text": "💠 रेफरल कार्यक्रम\n━━━━━━━━━━━━━━━━━━━\n\n🔗 आपका लिंक:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 आंकड़े:\n\n• कुल आमंत्रित: {total}\n• सक्रिय रेफरल: 0\n• कुल सौदा राशि: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 आपके बोनस:\n\n• प्रत्येक सक्रिय रेफरल के लिए: शेष में +5%\n• रेफरल के पहले सौदे पर: +100 ₽",
    }
}

# ============================================================
# WEBHOOK & SERVER
# ============================================================
async def handle_webhook(request):
    try:
        data = await request.json()
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.exception("Webhook error")
        return web.Response(text="Error", status=500)

async def health(request):
    return web.Response(text="FUNPAY is running")

async def set_webhook():
    if WEBHOOK_URL:
        await bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", drop_pending_updates=True)
        logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")
    else:
        logger.warning("WEBHOOK_URL is empty")

async def main():
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_post('/webhook', handle_webhook)
    await set_webhook()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=int(os.getenv('PORT', 5000)))
    await site.start()
    logger.info("Server started on port %s", os.getenv('PORT', 5000))
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
