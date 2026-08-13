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

PHOTO_URLS = {
    "ru": {"main": "https://ibb.co/rG08CGyz", "about": "https://ibb.co/ZpWsBSbx"},
    "en": {"main": "https://ibb.co/qYw6fVPt", "about": "https://ibb.co/TDrvMWX3"},
    "uk": {"main": "https://ibb.co/zVrbJ9Cj", "about": "https://ibb.co/93dcDwgx"},
    "kk": {"main": "https://ibb.co/Z1kD9vdL", "about": "https://ibb.co/9HRX2991"},
    "zh": {"main": "https://ibb.co/nMM9FhHj", "about": "https://ibb.co/MD9gNrcj"},
    "hi": {"main": "https://ibb.co/Xrg1yvFh", "about": "https://ibb.co/3mjhGpQh"},
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
        "waiting_buyer": "[?] Ожидает покупателя",
        "waiting_seller": "[?] Ожидает продавца",
        "completed": "[+] Завершена",
        "cancelled": "[-] Отменена",
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
            await bot.send_message(admin_id, "[!] Bot error:\n" + text)
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
# KEYBOARDS (SILNIYE KNPOTKI – SINIYE)
# ============================================================
def kb_main(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("create", lang), callback_data="create_deal", button_color="blue")],
        [InlineKeyboardButton(text=tr("my_deals", lang), callback_data="my_deals", button_color="blue"),
         InlineKeyboardButton(text=tr("req", lang), callback_data="requisites", button_color="blue")],
        [InlineKeyboardButton(text=tr("referral", lang), callback_data="referral", button_color="blue"),
         InlineKeyboardButton(text=tr("profile", lang), callback_data="profile", button_color="blue")],
        [InlineKeyboardButton(text=tr("language", lang), callback_data="lang", button_color="blue"),
         InlineKeyboardButton(text=tr("support", lang), url="https://t.me/FunPayHeIp", button_color="blue")],
        [InlineKeyboardButton(text=tr("about", lang), callback_data="about", button_color="blue")],
    ])

def kb_back(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")]])

def kb_roles(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("seller", lang), callback_data="role_seller", button_color="blue"),
         InlineKeyboardButton(text=tr("buyer", lang), callback_data="role_buyer", button_color="blue")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")]
    ])

def kb_types(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("account", lang), callback_data="type_account", button_color="blue"),
         InlineKeyboardButton(text=tr("gift", lang), callback_data="type_gift", button_color="blue")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")]
    ])

def kb_currencies(lang, prefix):
    labels = [
        ("USDT", tr("curr_usdt", lang)), ("RUB", tr("curr_rub", lang)),
        ("UAH", tr("curr_uah", lang)), ("BYN", tr("curr_byn", lang)),
        ("TON", tr("curr_ton", lang)), ("STARS", tr("curr_stars", lang)),
        ("KZT", tr("curr_kzt", lang))
    ]
    rows = []
    rows.append([InlineKeyboardButton(text=labels[0][1], callback_data=f"{prefix}{labels[0][0]}", button_color="blue")])
    for i in range(1, len(labels), 2):
        pair = labels[i:i+2]
        row = [InlineKeyboardButton(text=pair[0][1], callback_data=f"{prefix}{pair[0][0]}", button_color="blue")]
        if len(pair) > 1:
            row.append(InlineKeyboardButton(text=pair[1][1], callback_data=f"{prefix}{pair[1][0]}", button_color="blue"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_balance(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("deposit", lang), callback_data="deposit", button_color="blue")],
        [InlineKeyboardButton(text=tr("withdraw", lang), callback_data="withdraw", button_color="blue")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")]
    ])

def kb_my_deals(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("clear_history", lang), callback_data="clear_history", button_color="blue")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")]
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
            [InlineKeyboardButton(text="[ru] Русский", callback_data="onboard_ru", button_color="blue")],
            [InlineKeyboardButton(text="[en] English", callback_data="onboard_en", button_color="blue")],
            [InlineKeyboardButton(text="[uk] Українська", callback_data="onboard_uk", button_color="blue")],
            [InlineKeyboardButton(text="[kk] Қазақша", callback_data="onboard_kk", button_color="blue")],
            [InlineKeyboardButton(text="[zh] 中文", callback_data="onboard_zh", button_color="blue")],
            [InlineKeyboardButton(text="[hi] हिन्दी", callback_data="onboard_hi", button_color="blue")]
        ])
        await message.answer(tr("lang_choose", row["lang"] if row else "ru"), reply_markup=kb)
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
            [InlineKeyboardButton(text=tr("policy_btn", lang), url="https://t.me/PrivatePoliceFunpay", button_color="blue")],
            [InlineKeyboardButton(text=tr("accept_btn", lang), callback_data="accept_policy", button_color="blue")]
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
    await call.message.answer(tr("choose_role", lang), reply_markup=kb_roles(lang))
    await call.answer()

@dp.callback_query(F.data == "role_seller")
async def seller_role(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.seller_type)
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("choose_type", lang), reply_markup=kb_types(lang))
    await call.answer()

@dp.callback_query(F.data.startswith("type_"), States.seller_type)
async def seller_type(call: CallbackQuery, state: FSMContext):
    deal_type = call.data.replace("type_", "")
    await state.update_data(deal_type=deal_type)
    await state.set_state(States.seller_description)
    lang = user_lang(call.from_user.id)
    if deal_type == "account":
        await call.message.answer(tr("description_account", lang))
    else:
        await call.message.answer(tr("description_gift", lang))
    await call.answer()

@dp.message(States.seller_description)
async def seller_description(message: Message, state: FSMContext):
    if not await check_operation_allowed(message):
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("[-] Description too short.")
        return
    await state.update_data(description=text)
    await state.set_state(States.seller_currency)
    await message.answer(tr("currency", user_lang(message.from_user.id)), reply_markup=kb_currencies(user_lang(message.from_user.id), "sellcurr_"))

@dp.callback_query(F.data.startswith("sellcurr_"), States.seller_currency)
async def seller_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("sellcurr_", "")
    await state.update_data(currency=currency)
    await state.set_state(States.seller_amount)
    await call.message.answer(tr("amount", user_lang(call.from_user.id)))
    await call.answer()

@dp.message(States.seller_amount)
async def seller_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(tr("amount", user_lang(message.from_user.id)))
        return
    await state.update_data(amount=amount)
    await state.set_state(States.seller_req)
    await message.answer(tr("requisites", user_lang(message.from_user.id)))

@dp.message(States.seller_req)
async def seller_req(message: Message, state: FSMContext):
    req = (message.text or "").strip()
    if len(req) < 3:
        await message.answer("[-] Requisites too short.")
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
        await message.answer("[!] Error creating deal. Try later.")
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
    await call.message.answer(tr("choose_type", lang), reply_markup=kb_types(lang))
    await call.answer()

@dp.callback_query(F.data.startswith("type_"), States.buyer_type)
async def buyer_type(call: CallbackQuery, state: FSMContext):
    deal_type = call.data.replace("type_", "")
    await state.update_data(deal_type=deal_type)
    await state.set_state(States.buyer_description)
    lang = user_lang(call.from_user.id)
    if deal_type == "account":
        await call.message.answer(tr("description_account", lang))
    else:
        await call.message.answer(tr("description_gift", lang))
    await call.answer()

@dp.message(States.buyer_description)
async def buyer_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("[-] Description too short.")
        return
    await state.update_data(description=text)
    await state.set_state(States.buyer_currency)
    await message.answer(tr("currency", user_lang(message.from_user.id)), reply_markup=kb_currencies(user_lang(message.from_user.id), "buycurr_"))

@dp.callback_query(F.data.startswith("buycurr_"), States.buyer_currency)
async def buyer_currency(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("buycurr_", "")
    await state.update_data(currency=currency)
    await state.set_state(States.buyer_amount)
    await call.message.answer(tr("amount", user_lang(call.from_user.id)))
    await call.answer()

@dp.message(States.buyer_amount)
async def buyer_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(tr("amount", user_lang(message.from_user.id)))
        return
    await state.update_data(amount=amount)
    await state.set_state(States.buyer_username)
    await message.answer(tr("seller_username", user_lang(message.from_user.id)))

@dp.message(States.buyer_username)
async def buyer_username(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    username = raw.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        await message.answer(tr("invalid", user_lang(message.from_user.id)))
        return
    seller = fetchone("SELECT user_id, username FROM users WHERE lower(username)=lower(?)", (username,))
    seller_id = seller["user_id"] if seller else None
    seller_username = username if not seller else seller["username"]
    uid = message.from_user.id
    if seller_id == uid:
        await message.answer(tr("self_deal", user_lang(uid)))
        return
    if active_count(uid) >= MAX_ACTIVE_DEALS:
        await message.answer(tr("active_limit", user_lang(uid)))
        return
    data = await state.get_data()
    deal_id = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc).isoformat()
    try:
        execute("INSERT INTO deals(deal_id,seller_id,buyer_id,deal_type,description,amount,currency,status,seller_username,buyer_username,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (deal_id, seller_id, uid, data["deal_type"], data["description"], data["amount"], data["currency"], "active" if seller_id else "waiting_seller", seller_username, message.from_user.username or "", now))
        execute("UPDATE users SET deals_count=deals_count+1 WHERE user_id=?", (uid,))
    except Exception as e:
        logger.exception(f"Buyer deal creation error: {e}")
        await message.answer("[!] Error creating deal. Try later.")
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
        await notify(seller_id, f"[box] Buyer @{message.from_user.username or uid} created deal #{deal_id} and set you as seller.\n[link] {deal_link(deal_id)}\nOpen link to confirm role.")

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
    await call.message.answer(tr("cancelled", lang).format(deal_id=deal_id), reply_markup=kb_back(lang))
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
    text = f"[i] Deal #{deal_id}\n\nType: {deal['deal_type']}\nDescription: {deal['description']}\nAmount: {deal['amount']} {deal['currency']}\nSeller: @{deal['seller_username'] or '-'}\nBuyer: @{deal['buyer_username'] or '-'}\nStatus: {status_text(deal['status'], lang)}\n"
    if deal["seller_req"] and uid == deal["seller_id"]:
        text += f"\nSeller requisites: {deal['seller_req']}"
    rows = []
    if deal["status"] in ("waiting_buyer", "waiting_seller", "waiting"):
        rows.append([InlineKeyboardButton(text=tr("cancel_deal", lang), callback_data=f"cancel_{deal_id}", button_color="blue")])
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="my_deals", button_color="blue")])
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()

@dp.callback_query(F.data == "my_deals")
async def my_deals(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    rows = fetchall("SELECT deal_id,deal_type,amount,currency,status FROM deals WHERE seller_id=? OR buyer_id=? ORDER BY created_at DESC LIMIT 30", (uid, uid))
    if not rows:
        await call.message.answer(tr("my_deals_empty", lang), reply_markup=kb_back(lang))
        await call.answer()
        return
    text = tr("my_deals_title", lang)
    buttons = []
    for d in rows:
        text += f"#{d['deal_id']} | {d['deal_type']} | {d['amount']} {d['currency']}  | {status_text(d['status'], lang)}\n"
        buttons.append([InlineKeyboardButton(text=f"[i] #{d['deal_id']}", callback_data=f"dealview_{d['deal_id']}", button_color="blue")])
    buttons.append([InlineKeyboardButton(text=tr("clear_history", lang), callback_data="clear_history", button_color="blue")])
    buttons.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")])
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()

@dp.callback_query(F.data == "clear_history")
async def clear_history(call: CallbackQuery):
    uid = call.from_user.id
    rows = fetchall("SELECT * FROM deals WHERE status='completed' AND (seller_id=? OR buyer_id=?)", (uid, uid))
    for row in rows:
        execute("INSERT OR REPLACE INTO archived_deals (deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, gift_link, status, seller_username, buyer_username, created_at, completed_at, confirmed_at, commission, archived_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row["deal_id"], row["seller_id"], row["buyer_id"], row["deal_type"], row["description"], row["amount"], row["currency"], row["seller_req"], row["buyer_req"], row["gift_link"], row["status"], row["seller_username"], row["buyer_username"], row["created_at"], row["completed_at"], row["confirmed_at"], row["commission"], datetime.now(timezone.utc).isoformat()))
        execute("DELETE FROM deals WHERE deal_id=?", (row["deal_id"],))
    lang = user_lang(uid)
    await call.message.answer(tr("history_cleared", lang), reply_markup=kb_back(lang))
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
    await message.answer(f"[+] Completed last deals: {count}")

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
    await call.message.answer(tr("profile_text", lang).format(id=uid, username=row["username"] if row else "", deals=row["deals_count"] if row else 0, successful=row["successful_deals"] if row else 0, rating=f"{rating:.2f}", reviews=row["reviews_count"] if row else 0, refs=row["ref_count"] if row else 0), reply_markup=kb_back(lang))
    await call.answer()

@dp.callback_query(F.data == "referral")
async def referral(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    total = fetchone("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,))["c"]
    link = f"https://t.me/{BOT_USERNAME}?start=ref{uid}"
    await call.message.answer(tr("referral_text", lang).format(link=link, total=total), reply_markup=kb_back(lang))
    await call.answer()

@dp.callback_query(F.data == "lang")
async def lang_menu(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    await call.message.answer(tr("language_text", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[ru] Русский", callback_data="setlang_ru", button_color="blue")],
        [InlineKeyboardButton(text="[en] English", callback_data="setlang_en", button_color="blue")],
        [InlineKeyboardButton(text="[uk] Українська", callback_data="setlang_uk", button_color="blue")],
        [InlineKeyboardButton(text="[kk] Қазақша", callback_data="setlang_kk", button_color="blue")],
        [InlineKeyboardButton(text="[zh] 中文", callback_data="setlang_zh", button_color="blue")],
        [InlineKeyboardButton(text="[hi] हिन्दी", callback_data="setlang_hi", button_color="blue")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", button_color="blue")]
    ]))
    await call.answer()

@dp.callback_query(F.data.startswith("setlang_"))
async def set_lang(call: CallbackQuery):
    lang = call.data.replace("setlang_", "")
    if lang not in LANG_NAMES:
        await call.answer(tr("invalid", user_lang(call.from_user.id)), show_alert=True)
        return
    execute("UPDATE users SET lang=? WHERE user_id=?", (lang, call.from_user.id))
    await call.message.answer(tr("language_set", lang).format(lang=LANG_NAMES[lang]))
    await call.answer()

@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    photo = PHOTO_URLS.get(lang, PHOTO_URLS["ru"])["about"]
    await safe_send(call.message.chat.id, tr("about_text", lang), reply_markup=kb_back(lang), photo_url=photo)
    await call.answer()

@dp.callback_query(F.data == "requisites")
async def requisites_menu(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("req_menu", lang), reply_markup=kb_currencies(lang, "req_"))
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
    await call.message.answer(prompt)
    await call.answer()

@dp.message(States.req_input)
async def req_input(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer(tr("invalid", user_lang(message.from_user.id)))
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
    await message.answer(tr("req_saved", user_lang(message.from_user.id)), reply_markup=kb_back(user_lang(message.from_user.id)))

# ============================================================
# TRANSLATION DICTIONARY – ВСЕ 6 ЯЗЫКОВ (ПОЛНЫЙ)
# ============================================================
LANG_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська", "kk": "Қазақша", "zh": "中文", "hi": "हिन्दी"}

T = {
    "ru": {
        "lang_choose": "Выберите язык:",
        "policy_text": "[i] Добро пожаловать\n\nНеобходимо принять Политику конфиденциальности:\n• Данные только для работы бота\n• Передача аккаунта запрещена\n• При обращении нужны доказательства\n• Бот «как есть»\n\nНажимая «Принимаю», вы соглашаетесь.",
        "policy_btn": "[i] Политика конфиденциальности",
        "accept_btn": "[+] Принимаю",
        "main": "[i] Добро пожаловать\n\n<b>FunPay</b> - сервис безопасности внебиржевых сделок.\n\n[+] Автоматизация.\n[+] Скорость.\n[+] Удобный вывод.\n\n• Комиссия: 1%\n• Режим: 24/7\n• Поддержка: @FunPayHeIp\n\nВыберите раздел:",
        "create": "[+] Создать Сделку",
        "my_deals": "[i] Мои сделки",
        "req": "[i] Реквизиты",
        "referral": "[i] Рефералы",
        "profile": "[i] Профиль",
        "support": "[?] ТехПоддержка",
        "about": "[i] О сервисе",
        "back": "[x] Назад",
        "profile_text": "[i] <b>Профиль</b>\n\nID: {id}\nUsername: @{username}\nСделок: {deals}\nУспешных: {successful}\nРейтинг: {rating} ({reviews})\nРефералов: {refs}",
        "my_deals_title": "[i] <b>Мои сделки</b>\n\n",
        "my_deals_empty": "[x] У вас нет сделок.",
        "clear_history": "[x] Очистить историю",
        "history_cleared": "[+] История очищена.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "[?] <b>Выберите роль</b>:",
        "seller": "[i] Продавец",
        "buyer": "[i] Покупатель",
        "choose_type": "[?] <b>Выберите тип</b>:",
        "account": "[i] Аккаунт/товар",
        "gift": "[i] NFT Gift",
        "description_account": "[i] Опишите предмет сделки",
        "description_gift": "[i] Опишите предмет сделки\nПример: https://t.me/nft/...",
        "currency": "[?] <b>Выберите валюту</b>:",
        "amount": "[i] <b>Введите сумму</b>:",
        "requisites": "[i] <b>Введите реквизиты</b>:",
        "seller_username": "[i] <b>Введите @username продавца</b>:",
        "deal_created": "[+] <b>Сделка #{deal_id} создана!</b>\n\nВалюта: {currency}\nСумма: {amount} {currency}\nСсылка для покупателя: {link}",
        "deal_created_buyer": "[+] <b>Сделка #{deal_id} создана!</b>\n\nВалюта: {currency}\nСумма: {amount} {currency}\nСсылка для продавца: {link}",
        "joined": "[+] Вы подключились к сделке #{deal_id}.",
        "confirm": "[+] Подтвердить участие",
        "cancel_deal": "[x] Отменить сделку",
        "confirm_seller_notify": "[+] Участие подтверждено.",
        "buyer_notify": "[+] <b>Продавец подтвердил сделку #{deal_id}.</b>\n\n{amount} {currency}\nРеквизиты:\n{req}",
        "confirmed": "[+] <b>Оплата подтверждена</b>\n\nСделка: #{deal_id}\nПродавец: @{seller}\nРейтинг: {rating}/5\nУспешно: {successful}\nСумма: {amount} {currency}\nПредмет: {description}\n\nОжидайте передачу.",
        "deal_active": "[+] Активна",
        "language_text": "[?] <b>Выберите язык</b>:",
        "language_set": "[+] Язык установлен: {lang}.",
        "req_menu": "[i] <b>Выберите валюту</b>:",
        "req_prompt": "[i] Введите {currency} для {currency_name}\n\nПример:\n{example}",
        "req_saved": "[+] Реквизит сохранён.",
        "support_text": "[?] Поддержка: @FunPayHeIp",
        "about_text": "[i] <b>Подробнее</b>:\n\nМы гарант-сервис.\n\n• Вывод до 2 минут.\n• Передавайте подарок менеджеру.\n• Пополнение до 2 минут.\n• Доверяйте только @FunpayTrustly_robot.",
        "admin_done_ok": "[+] Сделка #{deal_id} завершена.",
        "admin_cancel_ok": "[x] Сделка #{deal_id} отменена.",
        "banned": "[x] Аккаунт заблокирован.",
        "active_limit": "[x] Максимум 5 сделок.",
        "not_found": "[x] Сделка не найдена.",
        "not_allowed": "[x] Действие недоступно.",
        "invalid": "[x] Некорректное значение.",
        "cancelled": "[x] Сделка #{deal_id} отменена.",
        "self_deal": "[x] Нельзя занять вторую роль.",
        "full": "[i] Обе роли заняты.",
        "already_member": "[i] Вы уже участник.",
        "referral_text": "[i] <b>Реферальная ссылка</b>: {link}\nВсего: {total}",
    },
    "en": {
        "lang_choose": "Choose language:",
        "policy_text": "[i] Welcome\n\nAccept Privacy Policy:\n• Data for bot only\n• Account transfer prohibited\n• Proof required\n• Bot 'as is'\n\nClick 'Accept' to agree.",
        "policy_btn": "[i] Privacy Policy",
        "accept_btn": "[+] Accept",
        "main": "[i] Welcome\n\n<b>FunPay</b> - security for OTC deals.\n\n[+] Automation.\n[+] Speed.\n[+] Fast withdrawal.\n\n• Commission: 1%\n• Mode: 24/7\n• Support: @FunPayHeIp\n\nSelect section:",
        "create": "[+] Create Deal",
        "my_deals": "[i] My deals",
        "req": "[i] Requisites",
        "referral": "[i] Referrals",
        "profile": "[i] Profile",
        "support": "[?] Support",
        "about": "[i] About",
        "back": "[x] Back",
        "profile_text": "[i] <b>Profile</b>\n\nID: {id}\nUsername: @{username}\nDeals: {deals}\nSuccessful: {successful}\nRating: {rating} ({reviews})\nReferrals: {refs}",
        "my_deals_title": "[i] <b>My deals</b>\n\n",
        "my_deals_empty": "[x] No deals.",
        "clear_history": "[x] Clear history",
        "history_cleared": "[+] History cleared.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "[?] <b>Choose role</b>:",
        "seller": "[i] Seller",
        "buyer": "[i] Buyer",
        "choose_type": "[?] <b>Choose type</b>:",
        "account": "[i] Account/goods",
        "gift": "[i] NFT Gift",
        "description_account": "[i] Describe deal item",
        "description_gift": "[i] Describe deal item\nExample: https://t.me/nft/...",
        "currency": "[?] <b>Choose currency</b>:",
        "amount": "[i] <b>Enter amount</b>:",
        "requisites": "[i] <b>Enter requisites</b>:",
        "seller_username": "[i] <b>Enter seller @username</b>:",
        "deal_created": "[+] <b>Deal #{deal_id} created!</b>\n\nCurrency: {currency}\nAmount: {amount} {currency}\nBuyer link: {link}",
        "deal_created_buyer": "[+] <b>Deal #{deal_id} created!</b>\n\nCurrency: {currency}\nAmount: {amount} {currency}\nSeller link: {link}",
        "joined": "[+] You joined deal #{deal_id}.",
        "confirm": "[+] Confirm",
        "cancel_deal": "[x] Cancel",
        "confirm_seller_notify": "[+] Confirmed.",
        "buyer_notify": "[+] <b>Seller confirmed deal #{deal_id}.</b>\n\n{amount} {currency}\nRequisites: {req}",
        "confirmed": "[+] <b>Payment confirmed</b>\n\nDeal: #{deal_id}\nSeller: @{seller}\nRating: {rating}/5\nSuccessful: {successful}\nAmount: {amount} {currency}\nItem: {description}\n\nWait for transfer.",
        "deal_active": "[+] Active",
        "language_text": "[?] <b>Choose language</b>:",
        "language_set": "[+] Language set: {lang}.",
        "req_menu": "[i] <b>Choose currency</b>:",
        "req_prompt": "[i] Enter {currency} for {currency_name}\n\nExample:\n{example}",
        "req_saved": "[+] Requisite saved.",
        "support_text": "[?] Support: @FunPayHeIp",
        "about_text": "[i] <b>About</b>:\n\nGuarantor service.\n\n• Withdrawal up to 2 min.\n• Transfer gift to manager.\n• Deposit up to 2 min.\n• Trust only @FunpayTrustly_robot.",
        "admin_done_ok": "[+] Deal #{deal_id} completed.",
        "admin_cancel_ok": "[x] Deal #{deal_id} cancelled.",
        "banned": "[x] Account blocked.",
        "active_limit": "[x] Max 5 deals.",
        "not_found": "[x] Deal not found.",
        "not_allowed": "[x] Not allowed.",
        "invalid": "[x] Invalid value.",
        "cancelled": "[x] Deal #{deal_id} cancelled.",
        "self_deal": "[x] Cannot take second role.",
        "full": "[i] Both roles taken.",
        "already_member": "[i] Already a member.",
        "referral_text": "[i] <b>Referral link</b>: {link}\nTotal: {total}",
    },
    "uk": {
        "lang_choose": "Виберіть мову:",
        "policy_text": "[i] Ласкаво просимо\n\nПрийміть Політику конфіденційності:\n• Дані тільки для роботи бота\n• Передача аккаунта заборонена\n• При зверненні потрібні докази\n• Бот «як є»\n\nНатискаючи «Приймаю», ви погоджуєтесь.",
        "policy_btn": "[i] Політика конфіденційності",
        "accept_btn": "[+] Приймаю",
        "main": "[i] Ласкаво просимо\n\n<b>FunPay</b> - сервіс безпеки позабіржових угод.\n\n[+] Автоматизація.\n[+] Швидкість.\n[+] Зручний вивід.\n\n• Комісія: 1%\n• Режим: 24/7\n• Підтримка: @FunPayHeIp\n\nВиберіть розділ:",
        "create": "[+] Створити Угоду",
        "my_deals": "[i] Мої угоди",
        "req": "[i] Реквізити",
        "referral": "[i] Реферали",
        "profile": "[i] Профіль",
        "support": "[?] ТехПідтримка",
        "about": "[i] Про сервіс",
        "back": "[x] Назад",
        "profile_text": "[i] <b>Профіль</b>\n\nID: {id}\nUsername: @{username}\nУгод: {deals}\nУспішних: {successful}\nРейтинг: {rating} ({reviews})\nРефералів: {refs}",
        "my_deals_title": "[i] <b>Мої угоди</b>\n\n",
        "my_deals_empty": "[x] У вас немає угод.",
        "clear_history": "[x] Очистити історію",
        "history_cleared": "[+] Історію очищено.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "[?] <b>Виберіть роль</b>:",
        "seller": "[i] Продавець",
        "buyer": "[i] Покупець",
        "choose_type": "[?] <b>Виберіть тип</b>:",
        "account": "[i] Аккаунт/товар",
        "gift": "[i] NFT Gift",
        "description_account": "[i] Опишіть предмет угоди",
        "description_gift": "[i] Опишіть предмет угоди\nПриклад: https://t.me/nft/...",
        "currency": "[?] <b>Виберіть валюту</b>:",
        "amount": "[i] <b>Введіть суму</b>:",
        "requisites": "[i] <b>Введіть реквізити</b>:",
        "seller_username": "[i] <b>Введіть @username продавця</b>:",
        "deal_created": "[+] <b>Угода #{deal_id} створена!</b>\n\nВалюта: {currency}\nСума: {amount} {currency}\nПосилання для покупця: {link}",
        "deal_created_buyer": "[+] <b>Угода #{deal_id} створена!</b>\n\nВалюта: {currency}\nСума: {amount} {currency}\nПосилання для продавця: {link}",
        "joined": "[+] Ви підключились до угоди #{deal_id}.",
        "confirm": "[+] Підтвердити участь",
        "cancel_deal": "[x] Скасувати угоду",
        "confirm_seller_notify": "[+] Участь підтверджено.",
        "buyer_notify": "[+] <b>Продавець підтвердив угоду #{deal_id}.</b>\n\n{amount} {currency}\nРеквізити:\n{req}",
        "confirmed": "[+] <b>Оплата підтверджена</b>\n\nУгода: #{deal_id}\nПродавець: @{seller}\nРейтинг: {rating}/5\nУспішно: {successful}\nСума: {amount} {currency}\nПредмет: {description}\n\nОчікуйте передачу.",
        "deal_active": "[+] Активна",
        "language_text": "[?] <b>Виберіть мову</b>:",
        "language_set": "[+] Мову встановлено: {lang}.",
        "req_menu": "[i] <b>Виберіть валюту</b>:",
        "req_prompt": "[i] Введіть {currency} для {currency_name}\n\nПриклад:\n{example}",
        "req_saved": "[+] Реквізит збережено.",
        "support_text": "[?] Підтримка: @FunPayHeIp",
        "about_text": "[i] <b>Детальніше</b>:\n\nМи гарант-сервіс.\n\n• Вивід до 2 хв.\n• Передавайте подарунок менеджеру.\n• Поповнення до 2 хв.\n• Довіряйте тільки @FunpayTrustly_robot.",
        "admin_done_ok": "[+] Угода #{deal_id} завершена.",
        "admin_cancel_ok": "[x] Угода #{deal_id} скасована.",
        "banned": "[x] Аккаунт заблоковано.",
        "active_limit": "[x] Максимум 5 угод.",
        "not_found": "[x] Угоду не знайдено.",
        "not_allowed": "[x] Дія недоступна.",
        "invalid": "[x] Некоректне значення.",
        "cancelled": "[x] Угоду #{deal_id} скасовано.",
        "self_deal": "[x] Не можна зайняти другу роль.",
        "full": "[i] Обидві ролі зайняті.",
        "already_member": "[i] Ви вже учасник.",
        "referral_text": "[i] <b>Реферальне посилання</b>: {link}\nВсього: {total}",
    },
    "kk": {
        "lang_choose": "Тіліңізді таңдаңыз:",
        "policy_text": "[i] Қош келдіңіз\n\nҚұпиялылық саясатын қабылдаңыз:\n• Деректер тек бот үшін\n• Аккаунтты беруге тыйым салынады\n• Дәлелдер қажет\n• Бот «қалпында»\n\n«Қабылдаймын» басыңыз.",
        "policy_btn": "[i] Құпиялылық саясаты",
        "accept_btn": "[+] Қабылдаймын",
        "main": "[i] Қош келдіңіз\n\n<b>FunPay</b> - биржадан тыс мәмілелерде қауіпсіздік.\n\n[+] Автоматтандыру.\n[+] Жылдамдық.\n[+] Ыңғайлы шығару.\n\n• Комиссия: 1%\n• Режим: 24/7\n• Қолдау: @FunPayHeIp\n\nБөлімді таңдаңыз:",
        "create": "[+] Мәміле жасау",
        "my_deals": "[i] Менің мәмілелерім",
        "req": "[i] Реквизиттер",
        "referral": "[i] Рефералдар",
        "profile": "[i] Профиль",
        "support": "[?] ТехҚолдау",
        "about": "[i] Қызмет туралы",
        "back": "[x] Артқа",
        "profile_text": "[i] <b>Профиль</b>\n\nID: {id}\nUsername: @{username}\nМәмілелер: {deals}\nСәтті: {successful}\nРейтинг: {rating} ({reviews})\nРефералдар: {refs}",
        "my_deals_title": "[i] <b>Менің мәмілелерім</b>\n\n",
        "my_deals_empty": "[x] Мәмілелер жоқ.",
        "clear_history": "[x] Тарихты тазалау",
        "history_cleared": "[+] Тарих тазартылды.",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "[?] <b>Рөліңізді таңдаңыз</b>:",
        "seller": "[i] Сатушы",
        "buyer": "[i] Сатып алушы",
        "choose_type": "[?] <b>Мәміле түрін таңдаңыз</b>:",
        "account": "[i] Аккаунт/тауар",
        "gift": "[i] NFT Сыйлық",
        "description_account": "[i] Мәміле пәнін сипаттаңыз",
        "description_gift": "[i] Мәміле пәнін сипаттаңыз\nМысал: https://t.me/nft/...",
        "currency": "[?] <b>Валютаны таңдаңыз</b>:",
        "amount": "[i] <b>Соманы енгізіңіз</b>:",
        "requisites": "[i] <b>Реквизиттерді енгізіңіз</b>:",
        "seller_username": "[i] <b>Сатушының @username енгізіңіз</b>:",
        "deal_created": "[+] <b>#{deal_id} мәмілесі құрылды!</b>\n\nВалюта: {currency}\nСома: {amount} {currency}\nСатып алушыға сілтеме: {link}",
        "deal_created_buyer": "[+] <b>#{deal_id} мәмілесі құрылды!</b>\n\nВалюта: {currency}\nСома: {amount} {currency}\nСатушыға сілтеме: {link}",
        "joined": "[+] Сіз #{deal_id} мәмілесіне қосылдыңыз.",
        "confirm": "[+] Қатысуды растау",
        "cancel_deal": "[x] Мәмілені болдырмау",
        "confirm_seller_notify": "[+] Расталды.",
        "buyer_notify": "[+] <b>Сатушы #{deal_id} мәмілесін растады.</b>\n\n{amount} {currency}\nРеквизиттер:\n{req}",
        "confirmed": "[+] <b>Төлем расталды</b>\n\nМәміле: #{deal_id}\nСатушы: @{seller}\nРейтинг: {rating}/5\nСәтті: {successful}\nСома: {amount} {currency}\nТауар: {description}\n\nБеруді күтіңіз.",
        "deal_active": "[+] Белсенді",
        "language_text": "[?] <b>Тілді таңдаңыз</b>:",
        "language_set": "[+] Тіл орнатылды: {lang}.",
        "req_menu": "[i] <b>Валютаны таңдаңыз</b>:",
        "req_prompt": "[i] {currency} үшін {currency_name} енгізіңіз\n\nМысал:\n{example}",
        "req_saved": "[+] Реквизит сақталды.",
        "support_text": "[?] Қолдау: @FunPayHeIp",
        "about_text": "[i] <b>Толығырақ</b>:\n\nКепілдік қызметі.\n\n• Шығару 2 мин дейін.\n• Сыйлықты менеджерге беріңіз.\n• Толтыру 2 мин дейін.\n• Тек @FunpayTrustly_robot-қа сеніңіз.",
        "admin_done_ok": "[+] #{deal_id} мәмілесі аяқталды.",
        "admin_cancel_ok": "[x] #{deal_id} мәмілесі болдырмалды.",
        "banned": "[x] Аккаунт бұғатталды.",
        "active_limit": "[x] Максимум 5 мәміле.",
        "not_found": "[x] Мәміле табылмады.",
        "not_allowed": "[x] Қолжетімсіз.",
        "invalid": "[x] Қате мән.",
        "cancelled": "[x] #{deal_id} мәмілесі болдырмалды.",
        "self_deal": "[x] Екінші рөлді ала алмайсыз.",
        "full": "[i] Екі рөл де бос емес.",
        "already_member": "[i] Сіз қатысушысыз.",
        "referral_text": "[i] <b>Рефералдық сілтеме</b>: {link}\nБарлығы: {total}",
    },
    "zh": {
        "lang_choose": "选择语言：",
        "policy_text": "[i] 欢迎\n\n接受隐私政策：\n• 数据仅用于机器人\n• 禁止转让账户\n• 需要证据\n• 机器人「按原样」提供\n\n点击「接受」即表示同意。",
        "policy_btn": "[i] 隐私政策",
        "accept_btn": "[+] 接受",
        "main": "[i] 欢迎\n\n<b>FunPay</b> - 场外交易安全保障服务。\n\n[+] 自动化。\n[+] 速度。\n[+] 快速提现。\n\n• 佣金：1%\n• 模式：24/7\n• 支持：@FunPayHeIp\n\n选择部分：",
        "create": "[+] 创建交易",
        "my_deals": "[i] 我的交易",
        "req": "[i] 详情",
        "referral": "[i] 推荐",
        "profile": "[i] 个人资料",
        "support": "[?] 支持",
        "about": "[i] 关于",
        "back": "[x] 返回",
        "profile_text": "[i] <b>个人资料</b>\n\nID: {id}\n用户名: @{username}\n交易数: {deals}\n成功: {successful}\n评分: {rating} ({reviews})\n推荐: {refs}",
        "my_deals_title": "[i] <b>我的交易</b>\n\n",
        "my_deals_empty": "[x] 没有交易。",
        "clear_history": "[x] 清除历史",
        "history_cleared": "[+] 历史已清除。",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "[?] <b>选择角色</b>:",
        "seller": "[i] 卖家",
        "buyer": "[i] 买家",
        "choose_type": "[?] <b>选择类型</b>:",
        "account": "[i] 账户/商品",
        "gift": "[i] NFT礼物",
        "description_account": "[i] 描述交易标的",
        "description_gift": "[i] 描述交易标的\n示例: https://t.me/nft/...",
        "currency": "[?] <b>选择货币</b>:",
        "amount": "[i] <b>输入金额</b>:",
        "requisites": "[i] <b>输入详情</b>:",
        "seller_username": "[i] <b>输入卖家 @username</b>:",
        "deal_created": "[+] <b>交易 #{deal_id} 已创建！</b>\n\n货币: {currency}\n金额: {amount} {currency}\n买家链接: {link}",
        "deal_created_buyer": "[+] <b>交易 #{deal_id} 已创建！</b>\n\n货币: {currency}\n金额: {amount} {currency}\n卖家链接: {link}",
        "joined": "[+] 您已加入交易 #{deal_id}。",
        "confirm": "[+] 确认",
        "cancel_deal": "[x] 取消",
        "confirm_seller_notify": "[+] 已确认。",
        "buyer_notify": "[+] <b>卖家已确认交易 #{deal_id}。</b>\n\n{amount} {currency}\n详情:\n{req}",
        "confirmed": "[+] <b>付款已确认</b>\n\n交易: #{deal_id}\n卖家: @{seller}\n评分: {rating}/5\n成功: {successful}\n金额: {amount} {currency}\n商品: {description}\n\n等待交付。",
        "deal_active": "[+] 活跃",
        "language_text": "[?] <b>选择语言</b>:",
        "language_set": "[+] 语言已设置: {lang}。",
        "req_menu": "[i] <b>选择货币</b>:",
        "req_prompt": "[i] 输入 {currency} 以用于 {currency_name}\n\n示例:\n{example}",
        "req_saved": "[+] 详情已保存。",
        "support_text": "[?] 支持: @FunPayHeIp",
        "about_text": "[i] <b>关于</b>:\n\n担保服务。\n\n• 提现最多2分钟。\n• 将礼物转交给经理。\n• 充值最多2分钟。\n• 只信任 @FunpayTrustly_robot。",
        "admin_done_ok": "[+] 交易 #{deal_id} 已完成。",
        "admin_cancel_ok": "[x] 交易 #{deal_id} 已取消。",
        "banned": "[x] 账户已封禁。",
        "active_limit": "[x] 最多5笔交易。",
        "not_found": "[x] 未找到交易。",
        "not_allowed": "[x] 不允许。",
        "invalid": "[x] 无效值。",
        "cancelled": "[x] 交易 #{deal_id} 已取消。",
        "self_deal": "[x] 不能担任第二角色。",
        "full": "[i] 两个角色都已占用。",
        "already_member": "[i] 您已是参与者。",
        "referral_text": "[i] <b>推荐链接</b>: {link}\n总数: {total}",
    },
    "hi": {
        "lang_choose": "भाषा चुनें:",
        "policy_text": "[i] स्वागत है\n\nगोपनीयता नीति स्वीकार करें:\n• डेटा केवल बॉट के लिए\n• खाता हस्तांतरण निषिद्ध\n• साक्ष्य आवश्यक\n• बॉट 'जैसा है'\n\n'स्वीकार करें' क्लिक करें।",
        "policy_btn": "[i] गोपनीयता नीति",
        "accept_btn": "[+] स्वीकार करें",
        "main": "[i] स्वागत है\n\n<b>FunPay</b> - ऑफ-एक्सचेंज सौदों के लिए सुरक्षा।\n\n[+] स्वचालन।\n[+] गति।\n[+] त्वरित निकासी।\n\n• कमीशन: 1%\n• मोड: 24/7\n• सहायता: @FunPayHeIp\n\nअनुभाग चुनें:",
        "create": "[+] सौदा बनाएं",
        "my_deals": "[i] मेरे सौदे",
        "req": "[i] विवरण",
        "referral": "[i] रेफरल",
        "profile": "[i] प्रोफ़ाइल",
        "support": "[?] सहायता",
        "about": "[i] के बारे में",
        "back": "[x] वापस",
        "profile_text": "[i] <b>प्रोफ़ाइल</b>\n\nID: {id}\nउपयोगकर्ता नाम: @{username}\nसौदे: {deals}\nसफल: {successful}\nरेटिंग: {rating} ({reviews})\nरेफरल: {refs}",
        "my_deals_title": "[i] <b>मेरे सौदे</b>\n\n",
        "my_deals_empty": "[x] कोई सौदा नहीं।",
        "clear_history": "[x] इतिहास साफ़ करें",
        "history_cleared": "[+] इतिहास साफ़ कर दिया गया।",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "[?] <b>भूमिका चुनें</b>:",
        "seller": "[i] विक्रेता",
        "buyer": "[i] खरीदार",
        "choose_type": "[?] <b>प्रकार चुनें</b>:",
        "account": "[i] खाता/माल",
        "gift": "[i] NFT उपहार",
        "description_account": "[i] सौदे के विषय का वर्णन करें",
        "description_gift": "[i] सौदे के विषय का वर्णन करें\nउदाहरण: https://t.me/nft/...",
        "currency": "[?] <b>मुद्रा चुनें</b>:",
        "amount": "[i] <b>राशि दर्ज करें</b>:",
        "requisites": "[i] <b>विवरण दर्ज करें</b>:",
        "seller_username": "[i] <b>विक्रेता का @username दर्ज करें</b>:",
        "deal_created": "[+] <b>सौदा #{deal_id} बनाया गया!</b>\n\nमुद्रा: {currency}\nराशि: {amount} {currency}\nखरीदार लिंक: {link}",
        "deal_created_buyer": "[+] <b>सौदा #{deal_id} बनाया गया!</b>\n\nमुद्रा: {currency}\nराशि: {amount} {currency}\nविक्रेता लिंक: {link}",
        "joined": "[+] आप सौदा #{deal_id} में शामिल हो गए।",
        "confirm": "[+] पुष्टि करें",
        "cancel_deal": "[x] रद्द करें",
        "confirm_seller_notify": "[+] पुष्टि की गई।",
        "buyer_notify": "[+] <b>विक्रेता ने सौदा #{deal_id} की पुष्टि की।</b>\n\n{amount} {currency}\nविवरण:\n{req}",
        "confirmed": "[+] <b>भुगतान की पुष्टि की गई</b>\n\nसौदा: #{deal_id}\nविक्रेता: @{seller}\nरेटिंग: {rating}/5\nसफल: {successful}\nराशि: {amount} {currency}\nवस्तु: {description}\n\nहस्तांतरण की प्रतीक्षा करें।",
        "deal_active": "[+] सक्रिय",
        "language_text": "[?] <b>भाषा चुनें</b>:",
        "language_set": "[+] भाषा सेट की गई: {lang}।",
        "req_menu": "[i] <b>मुद्रा चुनें</b>:",
        "req_prompt": "[i] {currency} के लिए {currency_name} दर्ज करें\n\nउदाहरण:\n{example}",
        "req_saved": "[+] विवरण सहेजा गया।",
        "support_text": "[?] सहायता: @FunPayHeIp",
        "about_text": "[i] <b>के बारे में</b>:\n\nगारंटर सेवा।\n\n• निकासी 2 मिनट तक।\n• उपहार प्रबंधक को हस्तांतरित करें।\n• जमा 2 मिनट तक।\n• केवल @FunpayTrustly_robot पर भरोसा करें।",
        "admin_done_ok": "[+] सौदा #{deal_id} पूरा किया गया।",
        "admin_cancel_ok": "[x] सौदा #{deal_id} रद्द कर दिया गया।",
        "banned": "[x] खाता ब्लॉक कर दिया गया।",
        "active_limit": "[x] अधिकतम 5 सौदे।",
        "not_found": "[x] सौदा नहीं मिला।",
        "not_allowed": "[x] अनुमति नहीं है।",
        "invalid": "[x] अमान्य मान।",
        "cancelled": "[x] सौदा #{deal_id} रद्द कर दिया गया।",
        "self_deal": "[x] दूसरी भूमिका नहीं ले सकते।",
        "full": "[i] दोनों भूमिकाएँ ली गई हैं।",
        "already_member": "[i] आप पहले से ही सदस्य हैं।",
        "referral_text": "[i] <b>रेफरल लिंक</b>: {link}\nकुल: {total}",
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
