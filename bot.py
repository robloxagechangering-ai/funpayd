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

BOT_USERNAME = os.getenv("BOT_USERNAME", "FunpayTrust_robot")
PA_USERNAME = os.getenv("PA_USERNAME", "")
if PA_USERNAME:
    WEBHOOK_URL = f"https://{PA_USERNAME}.pythonanywhere.com"
else:
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")

# Изображения главного меню и раздела «Подробнее» для каждого языка
PHOTO_URLS = {
    "ru": {"main": "https://ibb.co/rG08CGyz", "about": "https://ibb.co/rG08CGyz"},
    "en": {"main": "https://ibb.co/qYw6fVPt", "about": "https://ibb.co/qYw6fVPt"},
    "uk": {"main": "https://ibb.co/zVrbJ9Cj", "about": "https://ibb.co/zVrbJ9Cj"},
    "kk": {"main": "https://ibb.co/Z1kD9vdL", "about": "https://ibb.co/Z1kD9vdL"},
    "zh": {"main": "https://ibb.co/nMM9FhHj", "about": "https://ibb.co/nMM9FhHj"},
    "hi": {"main": "https://ibb.co/Xrg1yvFh", "about": "https://ibb.co/Xrg1yvFh"},
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
        "waiting_buyer": tr("waiting_buyer", lang),
        "waiting_seller": tr("waiting_seller", lang),
        "completed": tr("completed", lang),
        "cancelled": tr("cancelled_status", lang),
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


REQUISITE_EXAMPLES = {
    "RUB": "+7 123 456 78 90\n2020 2020 2020 2020",
    "USDT": "UQ... or EQ...",
    "UAH": "+380 67 123 45 67\n2020 2020 2020 2020",
    "BYN": "+375 29 123 45 67\n2020 2020 2020 2020",
    "TON": "UQ... or EQ...",
    "STARS": "@username\nt.me/username",
    "KZT": "+7 707 123 45 67\n2020 2020 2020 2020",
}

REQUISITE_LABELS = {
    "ru": {"RUB": "номер телефона или карта для RUB", "USDT": "криптокошелёк для USDT", "UAH": "номер телефона или карта для UAH", "BYN": "номер телефона или карта для BYN", "TON": "криптокошелёк для TON", "STARS": "@Username для STARS", "KZT": "номер телефона или карта для KZT"},
    "en": {"RUB": "phone or card for RUB", "USDT": "crypto wallet for USDT", "UAH": "phone or card for UAH", "BYN": "phone or card for BYN", "TON": "crypto wallet for TON", "STARS": "@Username for STARS", "KZT": "phone or card for KZT"},
    "uk": {"RUB": "номер телефону або картка для RUB", "USDT": "криптогаманець для USDT", "UAH": "номер телефону або картка для UAH", "BYN": "номер телефону або картка для BYN", "TON": "криптогаманець для TON", "STARS": "@Username для STARS", "KZT": "номер телефону або картка для KZT"},
    "kk": {"RUB": "RUB үшін телефон нөмірі немесе карта", "USDT": "USDT үшін крипто әмиян", "UAH": "UAH үшін телефон нөмірі немесе карта", "BYN": "BYN үшін телефон нөмірі немесе карта", "TON": "TON үшін крипто әмиян", "STARS": "STARS үшін @Username", "KZT": "KZT үшін телефон нөмірі немесе карта"},
    "zh": {"RUB": "RUB 的手机号或银行卡", "USDT": "USDT 加密钱包", "UAH": "UAH 的手机号或银行卡", "BYN": "BYN 的手机号或银行卡", "TON": "TON 加密钱包", "STARS": "STARS 的 @Username", "KZT": "KZT 的手机号或银行卡"},
    "hi": {"RUB": "RUB के लिए फ़ोन या कार्ड", "USDT": "USDT के लिए क्रिप्टो वॉलेट", "UAH": "UAH के लिए फ़ोन या कार्ड", "BYN": "BYN के लिए फ़ोन या कार्ड", "TON": "TON के लिए क्रिप्टो वॉलेट", "STARS": "STARS के लिए @Username", "KZT": "KZT के लिए फ़ोन या कार्ड"},
}

REQUISITE_EXAMPLE_LABELS = {
    "ru": "Пример:", "en": "Example:", "uk": "Приклад:", "kk": "Мысал:", "zh": "示例：", "hi": "उदाहरण:"
}

def build_requisites_prompt(lang, currency):
    lang = lang if lang in REQUISITE_LABELS else "ru"
    label = REQUISITE_LABELS[lang].get(currency, currency)
    example = REQUISITE_EXAMPLES.get(currency, "")
    return tr("req_prompt", lang).format(
        currency=label,
        currency_name=currency,
        example=f"{REQUISITE_EXAMPLE_LABELS[lang]} {example}"
    )


async def join_deal(message: Message, state: FSMContext, deal_id: str):
    """Обработать /start deal_<id> и присоединить второго участника сделки."""
    await state.clear()
    ensure_user(message.from_user)
    uid = message.from_user.id
    lang = user_lang(uid)

    if is_banned(uid):
        await message.answer(tr("banned", lang), parse_mode="HTML")
        return

    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await message.answer(tr("not_found", lang), parse_mode="HTML")
        return

    if uid in (deal["seller_id"], deal["buyer_id"]):
        await message.answer(tr("already_member", lang), parse_mode="HTML")
        return

    if deal["status"] not in ("waiting_buyer", "waiting_seller", "waiting"):
        await message.answer(tr("deal_unavailable", lang), parse_mode="HTML")
        return

    if active_count(uid) >= MAX_ACTIVE_DEALS:
        await message.answer(tr("active_limit", lang), parse_mode="HTML")
        return

    username = message.from_user.username or ""

    if deal["status"] == "waiting_buyer":
        execute(
            "UPDATE deals SET buyer_id=?, buyer_username=?, status='active' WHERE deal_id=? AND status='waiting_buyer' AND buyer_id IS NULL",
            (uid, username, deal_id)
        )
        updated = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
        if not updated or updated["buyer_id"] != uid:
            await message.answer(tr("deal_unavailable", lang), parse_mode="HTML")
            return
        await message.answer(
            tr("joined", lang).format(
                deal_id=deal_id,
                description=updated["description"],
                amount=updated["amount"],
                currency=updated["currency"],
                req=updated["seller_req"] or updated["buyer_req"] or tr("not_specified", lang),
                deal_type=tr("account" if updated["deal_type"] == "account" else "gift", lang),
            ),
            reply_markup=kb_back(lang),
            parse_mode="HTML"
        )
        seller_id = updated["seller_id"]
        if seller_id:
            seller_lang = user_lang(seller_id)
            confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ " + tr("confirm", seller_lang), callback_data=f"confirm_{deal_id}", style="primary")],
                [InlineKeyboardButton(text="❌ " + tr("cancel_deal", seller_lang), callback_data=f"cancel_{deal_id}", style="primary")]
            ])
            await notify(
                seller_id,
                tr("buyer_joined_notify", seller_lang).format(
                    deal_id=deal_id,
                    buyer=username or uid,
                    amount=updated["amount"],
                    currency=updated["currency"]
                ),
                markup=confirm_kb
            )
        return

    if deal["status"] in ("waiting_seller", "waiting"):
        if deal["seller_id"] is not None and deal["seller_id"] != uid:
            await message.answer(tr("not_allowed", lang), parse_mode="HTML")
            return
        execute(
            "UPDATE deals SET seller_id=?, seller_username=?, status='active' WHERE deal_id=? AND status IN ('waiting_seller','waiting') AND seller_id IS NULL",
            (uid, username, deal_id)
        )
        updated = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
        if not updated or updated["seller_id"] != uid:
            await message.answer(tr("deal_unavailable", lang), parse_mode="HTML")
            return
        await message.answer(
            tr("joined", lang).format(
                deal_id=deal_id,
                description=updated["description"],
                amount=updated["amount"],
                currency=updated["currency"],
                req=updated["seller_req"] or updated["buyer_req"] or tr("not_specified", lang),
                deal_type=tr("account" if updated["deal_type"] == "account" else "gift", lang),
            ),
            reply_markup=kb_back(lang),
            parse_mode="HTML"
        )
        buyer_id = updated["buyer_id"]
        if buyer_id:
            buyer_lang = user_lang(buyer_id)
            await notify(
                buyer_id,
                tr("seller_joined_notify", buyer_lang).format(
                    deal_id=deal_id,
                    seller=username or uid,
                    amount=updated["amount"],
                    currency=updated["currency"]
                )
            )
        return

    await message.answer(tr("deal_unavailable", lang), parse_mode="HTML")

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
        [InlineKeyboardButton(text="📎 " + tr("about", lang), callback_data="about", style="primary")],
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
    args = message.text.split(maxsplit=1)
    param = args[1].strip() if len(args) > 1 else ""
    if param.startswith("deal_") and not (row and row["accepted_policy"]):
        await state.update_data(pending_deal_id=param[5:])
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
            if row and row["accepted_policy"]:
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
async def accept_policy(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    pending = (await state.get_data()).get("pending_deal_id")
    execute("UPDATE users SET accepted_policy=1 WHERE user_id=?", (uid,))
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()
    if pending:
        await join_deal(call.message, state, pending)
    else:
        await show_main(call.message.chat.id, uid)

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
        await message.answer(tr("description_short", user_lang(message.from_user.id)), parse_mode="HTML")
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
    data = await state.get_data()
    await state.set_state(States.seller_req)
    await message.answer(build_requisites_prompt(user_lang(message.from_user.id), data["currency"]), parse_mode="HTML")

@dp.message(States.seller_req)
async def seller_req(message: Message, state: FSMContext):
    req = (message.text or "").strip()
    if len(req) < 3:
        await message.answer(tr("requisites_short", user_lang(message.from_user.id)), parse_mode="HTML")
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
        await message.answer(tr("deal_error", user_lang(message.from_user.id)), parse_mode="HTML")
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
        await message.answer(tr("description_short", user_lang(message.from_user.id)), parse_mode="HTML")
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
        await message.answer(tr("deal_error", user_lang(message.from_user.id)), parse_mode="HTML")
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
        seller_lang = user_lang(seller_id)
        invite_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 " + tr("open_deal", seller_lang), url=deal_link(deal_id), style="primary")]
        ])
        await notify(
            seller_id,
            tr("seller_invite_notify", seller_lang).format(
                deal_id=deal_id,
                buyer=message.from_user.username or uid,
                amount=data["amount"],
                currency=data["currency"]
            ),
            markup=invite_kb
        )

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
    await notify(deal["buyer_id"], tr("buyer_notify", buyer_lang).format(deal_id=deal_id, amount=deal["amount"], currency=deal["currency"], req=deal["seller_req"] or tr("not_specified", buyer_lang)))
    await call.answer()

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
    await call.answer()

@dp.callback_query(F.data.startswith("dealview_"))
async def deal_details(call: CallbackQuery):
    deal_id = call.data.replace("dealview_", "")
    uid = call.from_user.id
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    lang = user_lang(uid)
    if not deal or uid not in (deal["seller_id"], deal["buyer_id"]):
        await call.answer(tr("not_allowed", lang), show_alert=True)
        return
    text = tr("deal_details", lang).format(
        deal_id=deal_id,
        deal_type=tr("account" if deal["deal_type"] == "account" else "gift", lang),
        description=deal["description"],
        amount=deal["amount"],
        currency=deal["currency"],
        seller=deal["seller_username"] or "-",
        buyer=deal["buyer_username"] or "-",
        status=status_text(deal["status"], lang)
    )
    if deal["seller_req"] and uid == deal["seller_id"]:
        text += tr("seller_requisites_line", lang).format(req=deal["seller_req"])
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
        type_text = tr("account" if d["deal_type"] == "account" else "gift", lang)
        text += f"#{d['deal_id']} | {type_text} | {d['amount']} {d['currency']}  | {status_text(d['status'], lang)}\n"
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
    await message.answer(tr("completed_last_deals", user_lang(message.from_user.id)).format(count=count))

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
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_main(call.message.chat.id, call.from_user.id)
    await call.answer()

@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    photo = PHOTO_URLS.get(lang, PHOTO_URLS["ru"])["about"]
    await safe_send(
        call.message.chat.id,
        tr("about_text", lang),
        markup=kb_back(lang),
        photo_url=photo
    )
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
    prompt = build_requisites_prompt(lang, currency)
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
        "cancelled_status": "❌ Отменена",
        "completed": "✅ Завершена",
        "waiting_seller": "⏳ Ожидает продавца",
        "waiting_buyer": "⏳ Ожидает покупателя",
        "completed_last_deals": "✅ Последние сделки завершены: {count}",
        "deal_error": "⚠️ Ошибка создания сделки. Попробуйте позже.",
        "requisites_short": "❌ Реквизиты слишком короткие.",
        "description_short": "❌ Описание слишком короткое.",
        "deal_unavailable": "❌ Сделка уже недоступна для подключения.",
        "not_specified": "не указаны",
        "buyer_joined_notify": "👤 Покупатель @{buyer} подключился к сделке #{deal_id}.\n\n💰 Сумма: {amount} {currency}\n\nПодтвердите участие или отмените сделку.",
        "seller_joined_notify": "👤 Продавец @{seller} подключился к сделке #{deal_id}.\n\n💰 Сумма: {amount} {currency}",
        "seller_invite_notify": "📦 Покупатель @{buyer} создал сделку #{deal_id} и указал вас продавцом.\n\n💰 Сумма: {amount} {currency}\n🔗 Откройте сделку по кнопке ниже.",
        "open_deal": "Открыть сделку",
        "deal_details": "📄 Сделка #{deal_id}\n\n📋 Тип: {deal_type}\n📝 Описание: {description}\n💰 Сумма: {amount} {currency}\n👤 Продавец: @{seller}\n👤 Покупатель: @{buyer}\n📊 Статус: {status}\n",
        "seller_requisites_line": "\n💳 Реквизиты продавца: {req}\n",
        "lang_choose": "🌐 Выберите язык",
        "policy_text": "🛡️ Добро пожаловать\n\nНеобходимо принять Политику конфиденциальности:\n• Данные только для работы бота\n• Передача аккаунта запрещена\n• При обращении нужны доказательства\n• Бот «как есть»\n\nНажимая «Принимаю», вы соглашаетесь.",
        "policy_btn": "Политика конфиденциальности",
        "accept_btn": "Принимаю",
        "main": "🛡️ Добро пожаловать\n\nFunPay — Мы специализированный сервис по обеспечению безопасности вне биржевых сделок.\n\nАвтоматизированный алгоритм исполнения.\nСкорость и автоматизация.\nУдобный и быстрый вывод средств.\n\n• Комиссия сервиса: 1%\n• Режим работы: 24/7\n• Техническая поддержка: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>\n\n🤝 Выберите нужный раздел ниже",
        "create": "Создать Сделку",
        "my_deals": "Мои сделки",
        "req": "Реквизиты",
        "referral": "Рефералы",
        "profile": "Профиль",
        "support": "Поддержка",
        "about": "Подробнее",
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
        "description_gift": "🎁 Опишите предмет сделки\nПример: t.me/nft/DurovsCap-1",
        "currency": "💱 Выберите валюту",
        "amount": "💰 Введите сумму",
        "requisites": "💳 Введите реквизиты",
        "seller_username": "👤 Введите @username продавца",
        "deal_created": "✅ Сделка #{deal_id} создана!\n\n💵 Валюта: {currency}\n💰 Сумма: {amount} {currency}\n🔗 Ссылка для покупателя: {link}",
        "deal_created_buyer": "✅ Сделка #{deal_id} создана!\n\n💵 Валюта: {currency}\n💰 Сумма: {amount} {currency}\n🔗 Ссылка для продавца: {link}",
        "joined": "✅ Вы подключились к сделке #{deal_id}.\n\n📦 Товар: {description}\n💰 Сумма: {amount} {currency}\n💳 Реквизиты: {req}\n📋 Тип: {deal_type}",
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
        "support_text": "🆘 Поддержка: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>",
        "about_text": "Мы – гарант сервис, наша задача помочь вам провести безопасные сделки, и оформить быстрый вывод!\n\nОтветы на частые вопросы:\n\n• Как долго происходит вывод? Обычно не более 2-х минут, в редких случаях до 2-х часов.\n\n• Почему нужно передавать подарок менеджеру, но не покупателю? Причина проста: покупатель может наврать что ему не пришёл подарок, что затягивает ситуацию, но наш менеджер автоматически проверяет наличие NFT подарка и уже обмануть не получится.\n\n• Как быстро происходит пополнение? Пополнение также занимает не более 2-х минут.\n\n• Я увидел похожего бота, стоит ли мне доверять? Если вы увидели другого бота кроме <a href=\"https://t.me/FunpayTrust_robot\">@FunpayTrust_robot</a>, ни в коем случае не проводите с ним сделки!",
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
        "cancelled_status": "❌ Cancelled",
        "completed": "✅ Completed",
        "waiting_seller": "⏳ Waiting for seller",
        "waiting_buyer": "⏳ Waiting for buyer",
        "completed_last_deals": "✅ Completed last deals: {count}",
        "deal_error": "⚠️ Error creating deal. Please try again later.",
        "requisites_short": "❌ Requisites are too short.",
        "description_short": "❌ Description is too short.",
        "deal_unavailable": "❌ This deal is no longer available to join.",
        "not_specified": "not specified",
        "buyer_joined_notify": "👤 Buyer @{buyer} joined deal #{deal_id}.\n\n💰 Amount: {amount} {currency}\n\nConfirm participation or cancel the deal.",
        "seller_joined_notify": "👤 Seller @{seller} joined deal #{deal_id}.\n\n💰 Amount: {amount} {currency}",
        "seller_invite_notify": "📦 Buyer @{buyer} created deal #{deal_id} and selected you as the seller.\n\n💰 Amount: {amount} {currency}\n🔗 Open the deal using the button below.",
        "open_deal": "Open deal",
        "deal_details": "📄 Deal #{deal_id}\n\n📋 Type: {deal_type}\n📝 Description: {description}\n💰 Amount: {amount} {currency}\n👤 Seller: @{seller}\n👤 Buyer: @{buyer}\n📊 Status: {status}\n",
        "seller_requisites_line": "\n💳 Seller requisites: {req}\n",
        "lang_choose": "🌐 Choose language",
        "policy_text": "🛡️ Welcome\n\nAccept Privacy Policy:\n• Data for bot only\n• Account transfer prohibited\n• Proof required\n• Bot 'as is'\n\nClick 'Accept' to agree.",
        "policy_btn": "Privacy Policy",
        "accept_btn": "Accept",
        "main": "🛡️ Welcome\n\nFunPay — We are a specialized service for ensuring security in off-exchange transactions.\n\nAutomated execution algorithm.\nSpeed and automation.\nConvenient and fast withdrawal of funds.\n\n• Service commission: 1%\n• Operating mode: 24/7\n• Technical support: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>\n\n🤝 Choose the section you need below",
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
        "description_gift": "🎁 Describe the deal item\nExample: t.me/nft/DurovsCap-1",
        "currency": "💱 Choose currency",
        "amount": "💰 Enter amount",
        "requisites": "💳 Enter requisites",
        "seller_username": "👤 Enter seller @username",
        "deal_created": "✅ Deal #{deal_id} created!\n\n💵 Currency: {currency}\n💰 Amount: {amount} {currency}\n🔗 Buyer link: {link}",
        "deal_created_buyer": "✅ Deal #{deal_id} created!\n\n💵 Currency: {currency}\n💰 Amount: {amount} {currency}\n🔗 Seller link: {link}",
        "joined": "✅ You joined deal #{deal_id}.\n\n📦 Item: {description}\n💰 Amount: {amount} {currency}\n💳 Requisites: {req}\n📋 Type: {deal_type}",
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
        "support_text": "🆘 Support: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>",
        "about_text": "We are a guarantor service. Our task is to help you conduct safe deals and arrange a fast withdrawal!\n\nAnswers to frequently asked questions:\n\n• How long does a withdrawal take? Usually no more than 2 minutes, in rare cases up to 2 hours.\n\n• Why should the gift be transferred to the manager instead of the buyer? The reason is simple: the buyer may lie that they did not receive the gift, which delays the situation, but our manager automatically checks whether the NFT gift exists, so it will not be possible to deceive the service.\n\n• How fast is the deposit? A deposit also usually takes no more than 2 minutes.\n\n• I saw a similar bot, should I trust it? If you saw any bot other than <a href=\"https://t.me/FunpayTrust_robot\">@FunpayTrust_robot</a>, do not conduct deals with it!",
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
        "cancelled_status": "❌ Скасована",
        "completed": "✅ Завершена",
        "waiting_seller": "⏳ Очікує продавця",
        "waiting_buyer": "⏳ Очікує покупця",
        "completed_last_deals": "✅ Останні угоди завершено: {count}",
        "deal_error": "⚠️ Помилка створення угоди. Спробуйте пізніше.",
        "requisites_short": "❌ Реквізити занадто короткі.",
        "description_short": "❌ Опис занадто короткий.",
        "deal_unavailable": "❌ Ця угода більше недоступна для підключення.",
        "not_specified": "не вказані",
        "buyer_joined_notify": "👤 Покупець @{buyer} підключився до угоди #{deal_id}.\n\n💰 Сума: {amount} {currency}\n\nПідтвердьте участь або скасуйте угоду.",
        "seller_joined_notify": "👤 Продавець @{seller} підключився до угоди #{deal_id}.\n\n💰 Сума: {amount} {currency}",
        "seller_invite_notify": "📦 Покупець @{buyer} створив угоду #{deal_id} і вказав вас продавцем.\n\n💰 Сума: {amount} {currency}\n🔗 Відкрийте угоду кнопкою нижче.",
        "open_deal": "Відкрити угоду",
        "deal_details": "📄 Угода #{deal_id}\n\n📋 Тип: {deal_type}\n📝 Опис: {description}\n💰 Сума: {amount} {currency}\n👤 Продавець: @{seller}\n👤 Покупець: @{buyer}\n📊 Статус: {status}\n",
        "seller_requisites_line": "\n💳 Реквізити продавця: {req}\n",
        "lang_choose": "🌐 Виберіть мову",
        "policy_text": "🛡️ Ласкаво просимо\n\nПрийміть Політику конфіденційності:\n• Дані тільки для роботи бота\n• Передача аккаунта заборонена\n• При зверненні потрібні докази\n• Бот «як є»\n\nНатискаючи «Приймаю», ви погоджуєтесь.",
        "policy_btn": "Політика конфіденційності",
        "accept_btn": "Приймаю",
        "main": "🛡️ Ласкаво просимо\n\nFunPay — Ми спеціалізований сервіс із забезпечення безпеки позабіржових угод.\n\nАвтоматизований алгоритм виконання.\nШвидкість та автоматизація.\nЗручний та швидкий вивід коштів.\n\n• Комісія сервісу: 1%\n• Режим роботи: 24/7\n• Технічна підтримка: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>\n\n🤝 Виберіть потрібний розділ нижче",
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
        "description_gift": "🎁 Опишіть предмет угоди\nПриклад: t.me/nft/DurovsCap-1",
        "currency": "💱 Виберіть валюту",
        "amount": "💰 Введіть суму",
        "requisites": "💳 Введіть реквізити",
        "seller_username": "👤 Введіть @username продавця",
        "deal_created": "✅ Угода #{deal_id} створена!\n\n💵 Валюта: {currency}\n💰 Сума: {amount} {currency}\n🔗 Посилання для покупця: {link}",
        "deal_created_buyer": "✅ Угода #{deal_id} створена!\n\n💵 Валюта: {currency}\n💰 Сума: {amount} {currency}\n🔗 Посилання для продавця: {link}",
        "joined": "✅ Ви підключилися до угоди #{deal_id}.\n\n📦 Товар: {description}\n💰 Сума: {amount} {currency}\n💳 Реквізити: {req}\n📋 Тип: {deal_type}",
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
        "support_text": "🆘 Підтримка: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>",
        "about_text": "Ми – гарант-сервіс, наше завдання допомогти вам провести безпечні угоди та оформити швидкий вивід!\n\nВідповіді на часті запитання:\n\n• Скільки часу триває виведення? Зазвичай не більше 2 хвилин, у рідкісних випадках до 2 годин.\n\n• Чому потрібно передавати подарунок менеджеру, а не покупцю? Причина проста: покупець може сказати неправду, що не отримав подарунок, що затягує ситуацію, але наш менеджер автоматично перевіряє наявність NFT-подарунка, тому обманути сервіс не вийде.\n\n• Як швидко відбувається поповнення? Поповнення також займає не більше 2 хвилин.\n\n• Я побачив схожого бота, чи варто йому довіряти? Якщо ви побачили будь-якого іншого бота, крім <a href=\"https://t.me/FunpayTrust_robot\">@FunpayTrust_robot</a>, у жодному разі не проводьте з ним угоди!",
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
        "cancelled_status": "❌ Болдырылмады",
        "completed": "✅ Аяқталды",
        "waiting_seller": "⏳ Сатушы күтілуде",
        "waiting_buyer": "⏳ Сатып алушы күтілуде",
        "completed_last_deals": "✅ Соңғы мәмілелер аяқталды: {count}",
        "deal_error": "⚠️ Мәміле жасау қатесі. Кейінірек қайталап көріңіз.",
        "requisites_short": "❌ Реквизиттер тым қысқа.",
        "description_short": "❌ Сипаттама тым қысқа.",
        "deal_unavailable": "❌ Бұл мәмілеге қосылу енді мүмкін емес.",
        "not_specified": "көрсетілмеген",
        "buyer_joined_notify": "👤 Сатып алушы @{buyer} #{deal_id} мәмілесіне қосылды.\n\n💰 Сома: {amount} {currency}\n\nҚатысуды растаңыз немесе мәмілені болдырмаңыз.",
        "seller_joined_notify": "👤 Сатушы @{seller} #{deal_id} мәмілесіне қосылды.\n\n💰 Сома: {amount} {currency}",
        "seller_invite_notify": "📦 Сатып алушы @{buyer} #{deal_id} мәмілесін құрды және сізді сатушы ретінде көрсетті.\n\n💰 Сома: {amount} {currency}\n🔗 Мәмілені төмендегі батырма арқылы ашыңыз.",
        "open_deal": "Мәмілені ашу",
        "deal_details": "📄 #{deal_id} мәмілесі\n\n📋 Түрі: {deal_type}\n📝 Сипаттама: {description}\n💰 Сома: {amount} {currency}\n👤 Сатушы: @{seller}\n👤 Сатып алушы: @{buyer}\n📊 Күйі: {status}\n",
        "seller_requisites_line": "\n💳 Сатушы реквизиттері: {req}\n",
        "lang_choose": "🌐 Тілді таңдаңыз",
        "policy_text": "🛡️ Қош келдіңіз\n\nҚұпиялылық саясатын қабылдаңыз:\n• Деректер тек бот үшін\n• Аккаунтты беруге тыйым салынады\n• Дәлелдер қажет\n• Бот «қалпында»\n\n«Қабылдаймын» басыңыз.",
        "policy_btn": "Құпиялылық саясаты",
        "accept_btn": "Қабылдаймын",
        "main": "🛡️ Қош келдіңіз\n\nFunPay — Біз биржадан тыс мәмілелердегі қауіпсіздікті қамтамасыз ететін мамандандырылған сервиспіз.\n\nАвтоматтандырылған орындау алгоритмі.\nЖылдамдық және автоматтандыру.\nҚаражатты ыңғайлы әрі жылдам шығару.\n\n• Қызмет комиссиясы: 1%\n• Жұмыс режимі: 24/7\n• Техникалық қолдау: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>\n\n🤝 Қажетті бөлімді төменнен таңдаңыз",
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
        "description_gift": "🎁 Мәміле пәнін сипаттаңыз\nМысал: t.me/nft/DurovsCap-1",
        "currency": "💱 Валютаны таңдаңыз",
        "amount": "💰 Соманы енгізіңіз",
        "requisites": "💳 Реквизиттерді енгізіңіз",
        "seller_username": "👤 Сатушының @username енгізіңіз",
        "deal_created": "✅ #{deal_id} мәмілесі құрылды!\n\n💵 Валюта: {currency}\n💰 Сома: {amount} {currency}\n🔗 Сатып алушыға сілтеме: {link}",
        "deal_created_buyer": "✅ #{deal_id} мәмілесі құрылды!\n\n💵 Валюта: {currency}\n💰 Сома: {amount} {currency}\n🔗 Сатушыға сілтеме: {link}",
        "joined": "✅ Сіз #{deal_id} мәмілесіне қосылдыңыз.\n\n📦 Тауар: {description}\n💰 Сома: {amount} {currency}\n💳 Реквизиттер: {req}\n📋 Түрі: {deal_type}",
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
        "support_text": "🆘 Қолдау: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>",
        "about_text": "Біз – кепілдік қызметіміз. Біздің міндетіміз сізге қауіпсіз мәмілелер жүргізуге және жылдам ақша шығаруды рәсімдеуге көмектесу!\n\nЖиі қойылатын сұрақтарға жауаптар:\n\n• Ақша шығару қанша уақыт алады? Әдетте 2 минуттан аспайды, сирек жағдайларда 2 сағатқа дейін созылуы мүмкін.\n\n• Неліктен сыйлықты сатып алушыға емес, менеджерге беру керек? Себебі сатып алушы сыйлық келмеді деп өтірік айтуы мүмкін, бұл жағдайды созады, бірақ менеджеріміз NFT сыйлығының бар-жоғын автоматты түрде тексереді, сондықтан қызметті алдау мүмкін болмайды.\n\n• Толықтыру қаншалықты жылдам жүреді? Толықтыру да әдетте 2 минуттан аспайды.\n\n• Мен ұқсас ботты көрдім, оған сенуге бола ма? Егер сіз <a href=\"https://t.me/FunpayTrust_robot\">@FunpayTrust_robot</a>-тан басқа ботты көрсеңіз, онымен еш жағдайда мәміле жасамаңыз!",
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
        "cancelled_status": "❌ 已取消",
        "completed": "✅ 已完成",
        "waiting_seller": "⏳ 等待卖家",
        "waiting_buyer": "⏳ 等待买家",
        "completed_last_deals": "✅ 最近交易已完成：{count}",
        "deal_error": "⚠️ 创建交易时出错，请稍后再试。",
        "requisites_short": "❌ 详情太短。",
        "description_short": "❌ 描述太短。",
        "deal_unavailable": "❌ 此交易已无法加入。",
        "not_specified": "未填写",
        "buyer_joined_notify": "👤 买家 @{buyer} 已加入交易 #{deal_id}。\n\n💰 金额：{amount} {currency}\n\n请确认参与或取消交易。",
        "seller_joined_notify": "👤 卖家 @{seller} 已加入交易 #{deal_id}。\n\n💰 金额：{amount} {currency}",
        "seller_invite_notify": "📦 买家 @{buyer} 创建了交易 #{deal_id} 并指定您为卖家。\n\n💰 金额：{amount} {currency}\n🔗 请使用下面的按钮打开交易。",
        "open_deal": "打开交易",
        "deal_details": "📄 交易 #{deal_id}\n\n📋 类型：{deal_type}\n📝 描述：{description}\n💰 金额：{amount} {currency}\n👤 卖家：@{seller}\n👤 买家：@{buyer}\n📊 状态：{status}\n",
        "seller_requisites_line": "\n💳 卖家收款信息：{req}\n",
        "lang_choose": "🌐 选择语言",
        "policy_text": "🛡️ 欢迎\n\n接受隐私政策：\n• 数据仅用于机器人\n• 禁止转让账户\n• 需要证据\n• 机器人「按原样」提供\n\n点击「接受」即表示同意。",
        "policy_btn": "隐私政策",
        "accept_btn": "接受",
        "main": "🛡️ 欢迎\n\nFunPay — 我们是专门为场外交易提供安全保障的服务。\n\n自动化执行算法。\n快速且自动化。\n方便快捷的资金提取。\n\n• 服务佣金：1%\n• 工作时间：24/7\n• 技术支持：<a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>\n\n🤝 请在下方选择您需要的部分",
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
        "description_gift": "🎁 描述交易标的\n示例: t.me/nft/DurovsCap-1",
        "currency": "💱 选择货币",
        "amount": "💰 输入金额",
        "requisites": "💳 输入详情",
        "seller_username": "👤 输入卖家 @username",
        "deal_created": "✅ 交易 #{deal_id} 已创建！\n\n💵 货币: {currency}\n💰 金额: {amount} {currency}\n🔗 买家链接: {link}",
        "deal_created_buyer": "✅ 交易 #{deal_id} 已创建！\n\n💵 货币: {currency}\n💰 金额: {amount} {currency}\n🔗 卖家链接: {link}",
        "joined": "✅ 您已加入交易 #{deal_id}。\n\n📦 商品：{description}\n💰 金额：{amount} {currency}\n💳 收款信息：{req}\n📋 类型：{deal_type}",
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
        "support_text": "🆘 支持：<a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>",
        "about_text": "我们是担保服务，致力于帮助您安全完成交易并快速提现！\n\n常见问题：\n\n• 提现需要多久？通常不超过 2 分钟，极少数情况下最长可达 2 小时。\n\n• 为什么要把礼物交给管理员，而不是直接交给买家？原因很简单：买家可能谎称没有收到礼物，从而拖延处理，但我们的管理员会自动检查 NFT 礼物是否存在，因此无法欺骗服务。\n\n• 充值有多快？充值通常也不超过 2 分钟。\n\n• 我看到了类似的机器人，应该相信它吗？如果你看到除了 <a href=\"https://t.me/FunpayTrust_robot\">@FunpayTrust_robot</a> 之外的其他机器人，请绝对不要与其进行交易！",
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
        "cancelled_status": "❌ रद्द किया गया",
        "completed": "✅ पूरा हुआ",
        "waiting_seller": "⏳ विक्रेता की प्रतीक्षा",
        "waiting_buyer": "⏳ खरीदार की प्रतीक्षा",
        "completed_last_deals": "✅ पिछली डील पूरी हुई: {count}",
        "deal_error": "⚠️ सौदा बनाने में त्रुटि हुई। बाद में पुनः प्रयास करें।",
        "requisites_short": "❌ विवरण बहुत छोटा है।",
        "description_short": "❌ विवरण बहुत छोटा है।",
        "deal_unavailable": "❌ यह डील अब शामिल होने के लिए उपलब्ध नहीं है।",
        "not_specified": "निर्दिष्ट नहीं",
        "buyer_joined_notify": "👤 खरीदार @{buyer} डील #{deal_id} में शामिल हो गया।\n\n💰 राशि: {amount} {currency}\n\nभागीदारी की पुष्टि करें या डील रद्द करें।",
        "seller_joined_notify": "👤 विक्रेता @{seller} डील #{deal_id} में शामिल हो गया।\n\n💰 राशि: {amount} {currency}",
        "seller_invite_notify": "📦 खरीदार @{buyer} ने डील #{deal_id} बनाई और आपको विक्रेता चुना।\n\n💰 राशि: {amount} {currency}\n🔗 नीचे दिए बटन से डील खोलें।",
        "open_deal": "डील खोलें",
        "deal_details": "📄 डील #{deal_id}\n\n📋 प्रकार: {deal_type}\n📝 विवरण: {description}\n💰 राशि: {amount} {currency}\n👤 विक्रेता: @{seller}\n👤 खरीदार: @{buyer}\n📊 स्थिति: {status}\n",
        "seller_requisites_line": "\n💳 विक्रेता के विवरण: {req}\n",
        "lang_choose": "🌐 भाषा चुनें",
        "policy_text": "🛡️ स्वागत है\n\nगोपनीयता नीति स्वीकार करें:\n• डेटा केवल बॉट के लिए\n• खाता हस्तांतरण निषिद्ध\n• साक्ष्य आवश्यक\n• बॉट 'जैसा है'\n\n'स्वीकार करें' क्लिक करें।",
        "policy_btn": "गोपनीयता नीति",
        "accept_btn": "स्वीकार करें",
        "main": "🛡️ स्वागत है\n\nFunPay — हम ऑफ-एक्सचेंज लेनदेन में सुरक्षा सुनिश्चित करने वाली एक विशेष सेवा हैं।\n\nस्वचालित निष्पादन एल्गोरिदम।\nगति और स्वचालन।\nसुविधाजनक और तेज़ धन निकासी।\n\n• सेवा कमीशन: 1%\n• कार्य मोड: 24/7\n• तकनीकी सहायता: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>\n\n🤝 नीचे आवश्यक अनुभाग चुनें",
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
        "description_gift": "🎁 सौदे के विषय का वर्णन करें\nउदाहरण: t.me/nft/DurovsCap-1",
        "currency": "💱 मुद्रा चुनें",
        "amount": "💰 राशि दर्ज करें",
        "requisites": "💳 विवरण दर्ज करें",
        "seller_username": "👤 विक्रेता का @username दर्ज करें",
        "deal_created": "✅ सौदा #{deal_id} बनाया गया!\n\n💵 मुद्रा: {currency}\n💰 राशि: {amount} {currency}\n🔗 खरीदार लिंक: {link}",
        "deal_created_buyer": "✅ सौदा #{deal_id} बनाया गया!\n\n💵 मुद्रा: {currency}\n💰 राशि: {amount} {currency}\n🔗 विक्रेता लिंक: {link}",
        "joined": "✅ आप सौदा #{deal_id} में शामिल हो गए।\n\n📦 आइटम: {description}\n💰 राशि: {amount} {currency}\n💳 भुगतान विवरण: {req}\n📋 प्रकार: {deal_type}",
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
        "support_text": "🆘 सहायता: <a href=\"https://t.me/FunPayHeIp\">@GiftsForFunpay</a>",
        "about_text": "हम एक गारंटी सेवा हैं। हमारा उद्देश्य आपको सुरक्षित लेन-देन करने और तेज़ निकासी की व्यवस्था करने में मदद करना है!\n\nअक्सर पूछे जाने वाले प्रश्न:\n\n• निकासी में कितना समय लगता है? आमतौर पर 2 मिनट से अधिक नहीं, दुर्लभ मामलों में 2 घंटे तक।\n\n• गिफ्ट खरीदार को देने के बजाय मैनेजर को क्यों देना चाहिए? कारण सरल है: खरीदार झूठ बोल सकता है कि उसे गिफ्ट नहीं मिला, जिससे मामला लंबा हो जाता है, लेकिन हमारा मैनेजर स्वचालित रूप से NFT गिफ्ट की मौजूदगी जांचता है, इसलिए सेवा को धोखा नहीं दिया जा सकता।\n\n• जमा कितनी जल्दी होता है? जमा भी आमतौर पर 2 मिनट से अधिक नहीं लेता।\n\n• मुझे एक जैसा बॉट दिखा, क्या मुझे उस पर भरोसा करना चाहिए? यदि आपको <a href=\"https://t.me/FunpayTrust_robot\">@FunpayTrust_robot</a> के अलावा कोई अन्य बॉट दिखे, तो उसके साथ किसी भी हालत में डील न करें!",
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
