import asyncio
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============================================================
# CONFIG (ВАШИ ДАННЫЕ)
# ============================================================

BOT_TOKEN = "8497462129:AAEC2hO1pZVwXA2eATQp4uk3YdSX63K0hAs"
ADMIN_IDS = {8282073669}
BOT_USERNAME = "FunpayTrustly_robot"
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = ""  # Оставьте пустым для polling
TEST_MODE = False
TEST_CHAT_ID = ""
DB_FILE = "funpay.db"

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("errors.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("funpay")


# ============================================================
# BOT
# ============================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ============================================================
# DATABASE
# ============================================================

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.row_factory = sqlite3.Row


def execute(sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    return cur


def fetchone(sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()


def fetchall(sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def column_exists(table, column):
    row = fetchone(
        "SELECT name FROM pragma_table_info(?) WHERE name=?",
        (table, column),
    )
    return row is not None


def add_column(table, column, definition):
    if not column_exists(table, column):
        execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():

    execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            lang TEXT DEFAULT 'ru',
            card TEXT,
            crypto TEXT,
            stars_username TEXT,
            ref_count INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            deals_count INTEGER DEFAULT 0,
            successful_deals INTEGER DEFAULT 0,
            rating REAL DEFAULT 5.0,
            reviews_count INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    execute("""
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

            seller_confirmed INTEGER DEFAULT 0,

            created_at TEXT,
            completed_at TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER,
            PRIMARY KEY(referrer_id, referred_id)
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            gift_link TEXT,
            description TEXT,
            created_at TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            content TEXT,
            created_at TEXT,
            sent_to INTEGER DEFAULT 0
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER,
            to_user_id INTEGER,
            deal_id TEXT,
            rating INTEGER,
            comment TEXT,
            created_at TEXT,

            UNIQUE(from_user_id, deal_id)
        )
    """)

    execute("""
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
            archived_at TEXT
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS service_balance (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            balance INTEGER DEFAULT 0
        )
    """)

    execute("""
        INSERT OR IGNORE INTO service_balance(id, balance)
        VALUES(1, 0)
    """)

    # --------------------------------------------------------
    # Migration of old databases
    # --------------------------------------------------------

    user_columns = {
        "rating": "REAL DEFAULT 5.0",
        "reviews_count": "INTEGER DEFAULT 0",
        "banned": "INTEGER DEFAULT 0",
        "created_at": "TEXT",
    }

    for name, definition in user_columns.items():
        add_column("users", name, definition)

    deal_columns = {
        "seller_id": "INTEGER",
        "buyer_id": "INTEGER",
        "seller_username": "TEXT",
        "buyer_username": "TEXT",
        "seller_req": "TEXT",
        "buyer_req": "TEXT",
        "gift_link": "TEXT",
        "status": "TEXT DEFAULT 'waiting_buyer'",
        "seller_confirmed": "INTEGER DEFAULT 0",
        "completed_at": "TEXT",
    }

    for name, definition in deal_columns.items():
        add_column("deals", name, definition)

    conn.commit()


init_db()


# ============================================================
# FSM
# ============================================================

class States(StatesGroup):

    # Deal creation
    deal_role = State()
    deal_type = State()
    deal_description = State()
    deal_amount = State()
    deal_currency = State()
    deal_seller_username = State()
    deal_requisites = State()

    # Requisites
    req_card = State()
    req_crypto = State()
    req_stars = State()

    # Gifts
    gift_link = State()
    gift_description = State()

    # Admin
    news_text = State()
    admin_req_deal = State()

    # Reviews
    review_rating = State()
    review_comment = State()


# ============================================================
# TEXTS (обновлённое меню)
# ============================================================

TEXTS = {
    "ru": {
        "menu": (
            "🛡️ <b>FUNPAY</b>\n\n"
            "Безопасный гарант для сделок в Telegram.\n\n"
            "📌 <b>Что внутри:</b>\n"
            "• защита от мошенников\n"
            "• удержание средств до завершения сделки\n"
            "• история и статусы сделок\n"
            "• поддержка через @GiftsforFunpay\n\n"
            "⬇️ Выберите действие ниже."
        ),

        "deal": "📝 Создать сделку",
        "my_deals": "📂 Мои сделки",
        "req": "💳 Реквизиты",
        "balance": "💰 Средства",
        "gifts": "🎁 Мои подарки",
        "profile": "👤 Профиль",
        "news": "📢 Новости",
        "about": "ℹ️ О сервисе",
        "support": "🆘 Поддержка",
        "language": "🌐 Язык",

        "choose_role": "Выберите вашу роль:",
        "seller": "🛒 Продавец",
        "buyer": "🛍 Покупатель",

        "choose_type": "Выберите тип сделки:",
        "goods": "📦 Товар",
        "service": "🛠 Услуга",
        "gift": "🎁 Подарок",

        "description": "Введите описание сделки:",
        "amount": "Введите сумму целым числом:",
        "currency": "Выберите валюту:",

        "username": (
            "Введите username продавца.\n"
            "Например: @username"
        ),

        "req_input": "Введите реквизиты продавца:",

        "created": (
            "✅ Сделка создана.\n\n"
            "ID: <code>{id}</code>\n"
            "Сумма: <b>{amount} {currency}</b>\n\n"
            "Ссылка для второго участника:\n"
            "<code>{link}</code>"
        ),

        "joined": (
            "✅ Вы присоединились к сделке <code>{id}</code>.\n\n"
            "Обе стороны теперь подключены."
        ),

        "already": "Вы уже являетесь участником этой сделки.",
        "full": "❌ Эта сделка уже укомплектована.",
        "not_found": "❌ Сделка не найдена.",
        "banned": "🚫 Ваш аккаунт заблокирован.",

        "balance_text": (
            "💰 <b>Баланс</b>\n\n"
            "Доступно: <b>{balance}</b> виртуальных единиц\n"
            "Заморожено: <b>{frozen}</b>"
        ),

        "profile_text": (
            "👤 <b>Профиль</b>\n\n"
            "ID: <code>{id}</code>\n"
            "Username: @{username}\n"
            "Рейтинг: ⭐ {rating:.2f}\n"
            "Отзывов: {reviews}"
        ),

        "deal_details": (
            "📄 <b>Сделка #{id}</b>\n\n"
            "Тип: {type}\n"
            "Описание: {description}\n"
            "Сумма: <b>{amount} {currency}</b>\n"
            "Статус: <b>{status}</b>\n\n"
            "Продавец: @{seller}\n"
            "Покупатель: @{buyer}"
        ),

        "cancelled": "❌ Сделка отменена.",
        "completed": "✅ Сделка завершена администратором.",
    },

    "en": {
        "menu": (
            "🛡️ <b>FUNPAY</b>\n\n"
            "Secure guarantor for Telegram deals.\n\n"
            "📌 <b>What's inside:</b>\n"
            "• protection from scammers\n"
            "• funds holding until deal completion\n"
            "• deal history and statuses\n"
            "• support via @GiftsforFunpay\n\n"
            "⬇️ Choose action below."
        ),

        "deal": "📝 Create deal",
        "my_deals": "📂 My deals",
        "req": "💳 Requisites",
        "balance": "💰 Funds",
        "gifts": "🎁 My gifts",
        "profile": "👤 Profile",
        "news": "📢 News",
        "about": "ℹ️ About",
        "support": "🆘 Support",
        "language": "🌐 Language",

        "choose_role": "Choose your role:",
        "seller": "🛒 Seller",
        "buyer": "🛍 Buyer",

        "choose_type": "Choose deal type:",
        "goods": "📦 Goods",
        "service": "🛠 Service",
        "gift": "🎁 Gift",

        "description": "Enter deal description:",
        "amount": "Enter amount as a whole number:",
        "currency": "Choose currency:",

        "username": "Enter seller username. Example: @username",

        "req_input": "Enter seller requisites:",

        "created": (
            "✅ Deal created.\n\n"
            "ID: <code>{id}</code>\n"
            "Amount: <b>{amount} {currency}</b>\n\n"
            "Join link:\n"
            "<code>{link}</code>"
        ),

        "joined": "✅ You joined deal <code>{id}</code>.",
        "already": "You are already a participant.",
        "full": "❌ This deal is already full.",
        "not_found": "❌ Deal not found.",
        "banned": "🚫 Your account is blocked.",

        "balance_text": (
            "💰 <b>Balance</b>\n\n"
            "Available: <b>{balance}</b>\n"
            "Frozen: <b>{frozen}</b>"
        ),

        "profile_text": (
            "👤 <b>Profile</b>\n\n"
            "ID: <code>{id}</code>\n"
            "Username: @{username}\n"
            "Rating: ⭐ {rating:.2f}\n"
            "Reviews: {reviews}"
        ),

        "deal_details": (
            "📄 <b>Deal #{id}</b>\n\n"
            "Type: {type}\n"
            "Description: {description}\n"
            "Amount: <b>{amount} {currency}</b>\n"
            "Status: <b>{status}</b>\n\n"
            "Seller: @{seller}\n"
            "Buyer: @{buyer}"
        ),

        "cancelled": "❌ Deal cancelled.",
        "completed": "✅ Deal completed by administrator.",
    }
}


def get_lang(user_id):
    row = fetchone(
        "SELECT lang FROM users WHERE user_id=?",
        (user_id,),
    )

    if row and row["lang"] in TEXTS:
        return row["lang"]

    return "ru"


def t(user_id, key, **kwargs):
    lang = get_lang(user_id)
    text = TEXTS[lang].get(key, TEXTS["ru"].get(key, key))

    try:
        return text.format(**kwargs)
    except Exception:
        return text


# ============================================================
# KEYBOARDS (НОВОЕ ГЛАВНОЕ МЕНЮ)
# ============================================================

def main_keyboard(user_id):
    kb = InlineKeyboardBuilder()

    # Первая строка: Создать сделку | Средства
    kb.row(
        InlineKeyboardButton(
            text=t(user_id, "deal"),
            callback_data="create_deal",
        ),
        InlineKeyboardButton(
            text=t(user_id, "balance"),
            callback_data="balance",
        )
    )

    # Вторая строка: Мои сделки | Реквизиты
    kb.row(
        InlineKeyboardButton(
            text=t(user_id, "my_deals"),
            callback_data="my_deals",
        ),
        InlineKeyboardButton(
            text=t(user_id, "req"),
            callback_data="requisites",
        )
    )

    # Третья строка: Язык | Поддержка
    kb.row(
        InlineKeyboardButton(
            text=t(user_id, "language"),
            callback_data="language",
        ),
        InlineKeyboardButton(
            text=t(user_id, "support"),
            callback_data="support",
        )
    )

    # Четвёртая строка: Верификация | Рефералы
    kb.row(
        InlineKeyboardButton(
            text="✅ Верификация",
            callback_data="verify",
        ),
        InlineKeyboardButton(
            text="👥 Рефералы",
            callback_data="referral",
        )
    )

    # Пятая строка: О сервисе (на всю ширину)
    kb.row(
        InlineKeyboardButton(
            text=t(user_id, "about"),
            callback_data="about",
        )
    )

    return kb.as_markup()


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Меню",
                    callback_data="main_menu",
                )
            ]
        ]
    )


# ============================================================
# HELPERS
# ============================================================

def username_of(user):
    return user.username or f"id{user.id}"


def ensure_user(user):

    exists = fetchone(
        "SELECT user_id FROM users WHERE user_id=?",
        (user.id,),
    )

    if not exists:
        execute(
            """
            INSERT INTO users(
                user_id,
                username,
                lang,
                created_at
            )
            VALUES(?,?,?,?)
            """,
            (
                user.id,
                username_of(user),
                "ru",
                datetime.utcnow().isoformat(),
            ),
        )
    else:
        execute(
            """
            UPDATE users
            SET username=?
            WHERE user_id=?
            """,
            (
                username_of(user),
                user.id,
            ),
        )


def is_banned(user_id):
    row = fetchone(
        "SELECT banned FROM users WHERE user_id=?",
        (user_id,),
    )

    return bool(row and row["banned"])


def active_deals_count(user_id):

    row = fetchone(
        """
        SELECT COUNT(*) AS count
        FROM deals
        WHERE (seller_id=? OR buyer_id=?)
        AND status NOT IN ('completed','cancelled')
        """,
        (user_id, user_id),
    )

    return row["count"] if row else 0


def frozen_balance(user_id):

    row = fetchone(
        """
        SELECT COALESCE(SUM(amount),0) AS amount
        FROM deals
        WHERE seller_id=?
        AND status='active'
        """,
        (user_id,),
    )

    return row["amount"] if row else 0


async def notify(user_id, text, reply_markup=None):

    if not user_id:
        return

    try:
        await bot.send_message(
            user_id,
            text,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception(
            "Failed notification to %s",
            user_id,
        )


async def admin_log(admin_id, action, details):

    execute(
        """
        INSERT INTO admin_logs(
            admin_id,
            action,
            details,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            admin_id,
            action,
            details,
            datetime.utcnow().isoformat(),
        ),
    )


async def admin_error(text):

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "⚠️ Ошибка бота:\n\n" + text,
            )
        except Exception:
            pass

    if TEST_MODE and TEST_CHAT_ID.isdigit():

        try:
            await bot.send_message(
                int(TEST_CHAT_ID),
                "🧪 TEST ERROR\n\n" + text,
            )
        except Exception:
            pass


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):

    ensure_user(message.from_user)

    await state.clear()

    if is_banned(message.from_user.id):
        await message.answer(
            t(message.from_user.id, "banned")
        )
        return

    args = message.text.split(maxsplit=1)

    if len(args) > 1:

        payload = args[1]

        if payload.startswith("deal_"):

            deal_id = payload[5:]

            await join_deal(
                message,
                deal_id,
            )

            return

        # ==================== РЕФЕРАЛЬНАЯ СИСТЕМА ====================
        elif payload.startswith("ref_"):
            try:
                ref_id = int(payload[4:])
                if ref_id != message.from_user.id:
                    referrer = fetchone(
                        "SELECT user_id FROM users WHERE user_id=?",
                        (ref_id,)
                    )
                    if referrer:
                        exists = fetchone(
                            """
                            SELECT 1 FROM referrals
                            WHERE referrer_id=? AND referred_id=?
                            """,
                            (ref_id, message.from_user.id)
                        )
                        if not exists:
                            execute(
                                """
                                INSERT INTO referrals (referrer_id, referred_id)
                                VALUES (?, ?)
                                """,
                                (ref_id, message.from_user.id)
                            )
                            execute(
                                """
                                UPDATE users
                                SET ref_count = ref_count + 1
                                WHERE user_id=?
                                """,
                                (ref_id,)
                            )
                            await message.answer(
                                "✅ Вы были приглашены по реферальной ссылке!"
                            )
            except Exception:
                pass

    await message.answer(
        t(message.from_user.id, "menu"),
        reply_markup=main_keyboard(
            message.from_user.id
        ),
        parse_mode="HTML"
    )


# ============================================================
# CANCEL FSM
# ============================================================

@dp.message(Command("cancel"))
async def cancel_command(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "❌ Текущее действие отменено.",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


# ============================================================
# MAIN MENU
# ============================================================

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):

    ensure_user(callback.from_user)

    await callback.message.edit_text(
        t(callback.from_user.id, "menu"),
        reply_markup=main_keyboard(
            callback.from_user.id
        ),
        parse_mode="HTML"
    )

    await callback.answer()


# ============================================================
# CREATE DEAL (ОСТАЁТСЯ БЕЗ ИЗМЕНЕНИЙ)
# ============================================================

@dp.callback_query(F.data == "create_deal")
async def create_deal(
    callback: CallbackQuery,
    state: FSMContext,
):

    ensure_user(callback.from_user)

    if is_banned(callback.from_user.id):
        await callback.answer(
            "🚫 Заблокировано",
            show_alert=True,
        )
        return

    if active_deals_count(callback.from_user.id) >= 5:

        await callback.answer(
            "❌ Максимум 5 активных сделок.",
            show_alert=True,
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Продавец",
                    callback_data="role_seller",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛍 Покупатель",
                    callback_data="role_buyer",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Меню",
                    callback_data="main_menu",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        t(callback.from_user.id, "choose_role"),
        reply_markup=kb,
    )

    await state.set_state(
        States.deal_role
    )

    await callback.answer()


@dp.callback_query(
    States.deal_role,
    F.data.in_({"role_seller", "role_buyer"}),
)
async def deal_role(
    callback: CallbackQuery,
    state: FSMContext,
):

    role = (
        "seller"
        if callback.data == "role_seller"
        else "buyer"
    )

    await state.update_data(
        role=role
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Товар",
                    callback_data="type_goods",
                ),
                InlineKeyboardButton(
                    text="🛠 Услуга",
                    callback_data="type_service",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Подарок",
                    callback_data="type_gift",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        t(callback.from_user.id, "choose_type"),
        reply_markup=kb,
    )

    await state.set_state(
        States.deal_type
    )

    await callback.answer()


@dp.callback_query(
    States.deal_type,
    F.data.in_({
        "type_goods",
        "type_service",
        "type_gift",
    }),
)
async def deal_type(
    callback: CallbackQuery,
    state: FSMContext,
):

    mapping = {
        "type_goods": "goods",
        "type_service": "service",
        "type_gift": "gift",
    }

    await state.update_data(
        deal_type=mapping[callback.data]
    )

    await callback.message.answer(
        t(callback.from_user.id, "description")
    )

    await state.set_state(
        States.deal_description
    )

    await callback.answer()


@dp.message(States.deal_description)
async def deal_description(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        description=message.text[:2000]
    )

    await message.answer(
        t(message.from_user.id, "amount")
    )

    await state.set_state(
        States.deal_amount
    )


@dp.message(States.deal_amount)
async def deal_amount(
    message: Message,
    state: FSMContext,
):

    if not message.text.isdigit():

        await message.answer(
            "❌ Введите целое положительное число."
        )

        return

    amount = int(message.text)

    if amount <= 0:

        await message.answer(
            "❌ Сумма должна быть больше нуля."
        )

        return

    await state.update_data(
        amount=amount
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="USDT",
                    callback_data="currency_USDT",
                ),
                InlineKeyboardButton(
                    text="TON",
                    callback_data="currency_TON",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="RUB",
                    callback_data="currency_RUB",
                ),
                InlineKeyboardButton(
                    text="UAH",
                    callback_data="currency_UAH",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="BYN",
                    callback_data="currency_BYN",
                ),
                InlineKeyboardButton(
                    text="Stars",
                    callback_data="currency_Stars",
                ),
            ],
        ]
    )

    await message.answer(
        t(message.from_user.id, "currency"),
        reply_markup=kb,
    )

    await state.set_state(
        States.deal_currency
    )


@dp.callback_query(
    States.deal_currency,
    F.data.startswith("currency_"),
)
async def deal_currency(
    callback: CallbackQuery,
    state: FSMContext,
):

    currency = callback.data.replace(
        "currency_",
        "",
    )

    await state.update_data(
        currency=currency
    )

    data = await state.get_data()

    if data["role"] == "buyer":

        await callback.message.answer(
            t(callback.from_user.id, "username")
        )

        await state.set_state(
            States.deal_seller_username
        )

    else:

        await callback.message.answer(
            t(callback.from_user.id, "req_input")
        )

        await state.set_state(
            States.deal_requisites
        )

    await callback.answer()


@dp.message(States.deal_seller_username)
async def deal_seller_username(
    message: Message,
    state: FSMContext,
):

    username = message.text.strip().lstrip("@")

    row = fetchone(
        """
        SELECT user_id, username
        FROM users
        WHERE LOWER(username)=LOWER(?)
        """,
        (username,),
    )

    if not row:

        await message.answer(
            "❌ Этот продавец ещё не запускал бота."
        )

        return

    if row["user_id"] == message.from_user.id:

        await message.answer(
            "❌ Нельзя создать сделку с самим собой."
        )

        return

    await state.update_data(
        seller_username=username,
        seller_id=row["user_id"],
    )

    await message.answer(
        "Введите ваши реквизиты покупателя "
        "или напишите «нет»:"
    )

    await state.set_state(
        States.deal_requisites
    )


@dp.message(States.deal_requisites)
async def deal_requisites(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    req = message.text.strip()

    if req.lower() == "нет":
        req = ""

    deal_id = uuid.uuid4().hex[:10]

    role = data["role"]

    seller_id = (
        message.from_user.id
        if role == "seller"
        else None
    )

    buyer_id = (
        message.from_user.id
        if role == "buyer"
        else None
    )

    seller_username = (
        username_of(message.from_user)
        if role == "seller"
        else data.get("seller_username")
    )

    buyer_username = (
        username_of(message.from_user)
        if role == "buyer"
        else ""
    )

    status = (
        "waiting_buyer"
        if role == "seller"
        else "waiting_seller"
    )

    seller_req = (
        req
        if role == "seller"
        else ""
    )

    buyer_req = (
        req
        if role == "buyer"
        else ""
    )

    execute(
        """
        INSERT INTO deals(
            deal_id,
            seller_id,
            buyer_id,
            deal_type,
            description,
            amount,
            currency,
            seller_req,
            buyer_req,
            status,
            seller_username,
            buyer_username,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            deal_id,
            seller_id,
            buyer_id,
            data["deal_type"],
            data["description"],
            data["amount"],
            data["currency"],
            seller_req,
            buyer_req,
            status,
            seller_username,
            buyer_username,
            datetime.utcnow().isoformat(),
        ),
    )

    link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=deal_{deal_id}"
    )

    await state.clear()

    await message.answer(
        t(
            message.from_user.id,
            "created",
            id=deal_id,
            amount=data["amount"],
            currency=data["currency"],
            link=link,
        ),
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


# ============================================================
# JOIN DEAL
# ============================================================

async def join_deal(
    message: Message,
    deal_id: str,
):

    user_id = message.from_user.id

    if is_banned(user_id):

        await message.answer(
            t(user_id, "banned")
        )

        return

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await message.answer(
            t(user_id, "not_found")
        )

        return

    if (
        deal["seller_id"] == user_id
        or deal["buyer_id"] == user_id
    ):

        await message.answer(
            t(user_id, "already")
        )

        return

    if (
        deal["seller_id"]
        and deal["buyer_id"]
    ):

        await message.answer(
            t(user_id, "full")
        )

        return

    if deal["seller_id"] is None:

        # ----------- ПОДТЯГИВАЕМ РЕКВИЗИТЫ ПРОДАВЦА ИЗ ПРОФИЛЯ -----------
        user_row = fetchone(
            "SELECT card, crypto, stars_username FROM users WHERE user_id=?",
            (user_id,)
        )
        req_parts = []
        if user_row:
            if user_row["card"]:
                req_parts.append(f"Карта: {user_row['card']}")
            if user_row["crypto"]:
                req_parts.append(f"Crypto: {user_row['crypto']}")
            if user_row["stars_username"]:
                req_parts.append(f"Stars: @{user_row['stars_username']}")
        seller_req = "\n".join(req_parts) if req_parts else "Не указаны, свяжитесь с продавцом вручную."

        execute(
            """
            UPDATE deals
            SET seller_id=?,
                seller_username=?,
                seller_req=?,
                status='active'
            WHERE deal_id=?
            """,
            (
                user_id,
                username_of(message.from_user),
                seller_req,
                deal_id,
            ),
        )

        await notify(
            deal["buyer_id"],
            f"✅ Продавец присоединился к сделке #{deal_id}.\n"
            f"Его реквизиты:\n{seller_req}",
        )

    elif deal["buyer_id"] is None:

        execute(
            """
            UPDATE deals
            SET buyer_id=?,
                buyer_username=?,
                status='active'
            WHERE deal_id=?
            """,
            (
                user_id,
                username_of(message.from_user),
                deal_id,
            ),
        )

        await notify(
            deal["seller_id"],
            f"✅ Покупатель присоединился к сделке #{deal_id}.",
        )

    await message.answer(
        t(
            user_id,
            "joined",
            id=deal_id,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить участие",
                        callback_data=f"confirm:{deal_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить",
                        callback_data=f"canceldeal:{deal_id}",
                    )
                ],
            ]
        ),
    )


# ============================================================
# CONFIRM
# ============================================================

@dp.callback_query(
    F.data.startswith("confirm:")
)
async def confirm_deal(
    callback: CallbackQuery,
):

    deal_id = callback.data.split(":", 1)[1]

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await callback.answer(
            "Сделка не найдена",
            show_alert=True,
        )

        return

    if callback.from_user.id != deal["seller_id"]:

        await callback.answer(
            "Подтвердить может продавец.",
            show_alert=True,
        )

        return

    execute(
        """
        UPDATE deals
        SET seller_confirmed=1
        WHERE deal_id=?
        """,
        (deal_id,),
    )

    # ----------- ОТПРАВЛЯЕМ РЕКВИЗИТЫ ПРОДАВЦА ПОКУПАТЕЛЮ -----------
    seller_req = deal["seller_req"] or "не указаны"
    await notify(
        deal["buyer_id"],
        f"✅ Продавец подтвердил сделку #{deal_id}.\n\n"
        f"Реквизиты продавца:\n{seller_req}\n\n"
        f"Ожидайте дальнейших действий.",
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "Участие подтверждено."
    )


# ============================================================
# CANCEL DEAL
# ============================================================

@dp.callback_query(
    F.data.startswith("canceldeal:")
)
async def cancel_deal(
    callback: CallbackQuery,
):

    deal_id = callback.data.split(":", 1)[1]

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await callback.answer(
            "Сделка не найдена",
            show_alert=True,
        )

        return

    if callback.from_user.id not in (
        deal["seller_id"],
        deal["buyer_id"],
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    if deal["status"] not in (
        "waiting_seller",
        "waiting_buyer",
    ):

        await callback.answer(
            "Активную сделку отменяет администратор.",
            show_alert=True,
        )

        return

    execute(
        """
        UPDATE deals
        SET status='cancelled'
        WHERE deal_id=?
        """,
        (deal_id,),
    )

    for uid in (
        deal["seller_id"],
        deal["buyer_id"],
    ):

        if uid:
            await notify(
                uid,
                t(uid, "cancelled"),
            )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "Сделка отменена."
    )


# ============================================================
# MY DEALS
# ============================================================

@dp.callback_query(F.data == "my_deals")
async def my_deals(
    callback: CallbackQuery,
):

    rows = fetchall(
        """
        SELECT *
        FROM deals
        WHERE seller_id=?
           OR buyer_id=?
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (
            callback.from_user.id,
            callback.from_user.id,
        ),
    )

    if not rows:

        await callback.message.edit_text(
            "📂 У вас пока нет сделок.",
            reply_markup=back_keyboard(),
        )

        await callback.answer()
        return

    kb = InlineKeyboardBuilder()

    for row in rows:

        status = row["status"]

        button_text = (
            f"#{row['deal_id']} | "
            f"{row['amount']} {row['currency']} | "
            f"{status}"
        )

        kb.row(
            InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"dealview:{row['deal_id']}",
            )
        )

    kb.row(
        InlineKeyboardButton(
            text="🔙 Меню",
            callback_data="main_menu",
        )
    )

    await callback.message.edit_text(
        "📂 <b>Мои сделки</b>\n\n"
        "Выберите сделку:",
        reply_markup=kb.as_markup(),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("dealview:")
)
async def deal_view(
    callback: CallbackQuery,
):

    deal_id = callback.data.split(":", 1)[1]

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await callback.answer(
            "Не найдена",
            show_alert=True,
        )

        return

    if callback.from_user.id not in (
        deal["seller_id"],
        deal["buyer_id"],
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    seller = deal["seller_username"] or "—"
    buyer = deal["buyer_username"] or "—"

    text = t(
        callback.from_user.id,
        "deal_details",
        id=deal["deal_id"],
        type=deal["deal_type"],
        description=deal["description"],
        amount=deal["amount"],
        currency=deal["currency"],
        status=deal["status"],
        seller=seller,
        buyer=buyer,
    )

    buttons = []

    if deal["status"] in (
        "waiting_seller",
        "waiting_buyer",
    ):

        buttons.append(
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"canceldeal:{deal_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="my_deals",
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# ============================================================
# BALANCE
# ============================================================

@dp.callback_query(F.data == "balance")
async def balance_menu(
    callback: CallbackQuery,
):

    row = fetchone(
        "SELECT balance FROM users WHERE user_id=?",
        (callback.from_user.id,),
    )

    balance = row["balance"] if row else 0
    frozen = frozen_balance(
        callback.from_user.id
    )

    await callback.message.edit_text(
        t(
            callback.from_user.id,
            "balance_text",
            balance=balance,
            frozen=frozen,
        ),
        reply_markup=back_keyboard(),
    )

    await callback.answer()


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(
    callback: CallbackQuery,
):

    row = fetchone(
        "SELECT * FROM users WHERE user_id=?",
        (callback.from_user.id,),
    )

    if not row:
        await callback.answer()
        return

    await callback.message.edit_text(
        t(
            callback.from_user.id,
            "profile_text",
            id=row["user_id"],
            username=row["username"] or "—",
            rating=row["rating"] or 5,
            reviews=row["reviews_count"] or 0,
        ),
        reply_markup=back_keyboard(),
    )

    await callback.answer()


# ============================================================
# REQUISITES
# ============================================================

@dp.callback_query(F.data == "requisites")
async def requisites(
    callback: CallbackQuery,
):

    row = fetchone(
        """
        SELECT card, crypto, stars_username
        FROM users
        WHERE user_id=?
        """,
        (callback.from_user.id,),
    )

    card = row["card"] or "—"
    crypto = row["crypto"] or "—"
    stars = row["stars_username"] or "—"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Изменить карту",
                    callback_data="req_card",
                )
            ],
            [
                InlineKeyboardButton(
                    text="₿ Изменить крипто-реквизит",
                    callback_data="req_crypto",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Изменить Stars",
                    callback_data="req_stars",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Меню",
                    callback_data="main_menu",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        f"💳 <b>Ваши реквизиты</b>\n\n"
        f"Карта: <code>{card}</code>\n"
        f"Крипто: <code>{crypto}</code>\n"
        f"Stars: <code>{stars}</code>",
        reply_markup=kb,
    )

    await callback.answer()


@dp.callback_query(F.data == "req_card")
async def req_card(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.message.answer(
        "Введите номер карты или другой "
        "демонстрационный реквизит:"
    )

    await state.set_state(
        States.req_card
    )

    await callback.answer()


@dp.message(States.req_card)
async def req_card_save(
    message: Message,
    state: FSMContext,
):

    execute(
        """
        UPDATE users
        SET card=?
        WHERE user_id=?
        """,
        (
            message.text[:500],
            message.from_user.id,
        ),
    )

    await state.clear()

    await message.answer(
        "✅ Реквизит сохранён.",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


@dp.callback_query(F.data == "req_crypto")
async def req_crypto(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.message.answer(
        "Введите крипто-адрес:"
    )

    await state.set_state(
        States.req_crypto
    )

    await callback.answer()


@dp.message(States.req_crypto)
async def req_crypto_save(
    message: Message,
    state: FSMContext,
):

    execute(
        """
        UPDATE users
        SET crypto=?
        WHERE user_id=?
        """,
        (
            message.text[:500],
            message.from_user.id,
        ),
    )

    await state.clear()

    await message.answer(
        "✅ Крипто-реквизит сохранён.",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


@dp.callback_query(F.data == "req_stars")
async def req_stars(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.message.answer(
        "Введите Telegram username для Stars:"
    )

    await state.set_state(
        States.req_stars
    )

    await callback.answer()


@dp.message(States.req_stars)
async def req_stars_save(
    message: Message,
    state: FSMContext,
):

    execute(
        """
        UPDATE users
        SET stars_username=?
        WHERE user_id=?
        """,
        (
            message.text[:100],
            message.from_user.id,
        ),
    )

    await state.clear()

    await message.answer(
        "✅ Stars-реквизит сохранён.",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


# ============================================================
# GIFTS (ОСТАВЛЯЕМ, ХОТЯ И НЕТ В МЕНЮ)
# ============================================================

@dp.callback_query(F.data == "gifts")
async def gifts(
    callback: CallbackQuery,
):

    rows = fetchall(
        """
        SELECT gift_link, description
        FROM gifts
        WHERE user_id=?
        ORDER BY id DESC
        """,
        (callback.from_user.id,),
    )

    if not rows:

        text = "🎁 У вас пока нет сохранённых подарков."

    else:

        parts = ["🎁 <b>Мои подарки</b>\n"]

        for row in rows:

            parts.append(
                f"🔗 {row['gift_link']}\n"
                f"📝 {row['description']}\n"
            )

        text = "\n".join(parts)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить",
                    callback_data="gift_add",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Меню",
                    callback_data="main_menu",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=kb,
    )

    await callback.answer()


@dp.callback_query(F.data == "gift_add")
async def gift_add(
    callback: CallbackQuery,
    state: FSMContext,
):

    await callback.message.answer(
        "Введите ссылку на подарок:"
    )

    await state.set_state(
        States.gift_link
    )

    await callback.answer()


@dp.message(States.gift_link)
async def gift_link(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        gift_link=message.text.strip()
    )

    await message.answer(
        "Введите описание подарка:"
    )

    await state.set_state(
        States.gift_description
    )


@dp.message(States.gift_description)
async def gift_description(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    execute(
        """
        INSERT INTO gifts(
            user_id,
            gift_link,
            description,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            message.from_user.id,
            data["gift_link"],
            message.text[:1000],
            datetime.utcnow().isoformat(),
        ),
    )

    await state.clear()

    await message.answer(
        "✅ Подарок сохранён.",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


# ============================================================
# LANGUAGE
# ============================================================

@dp.callback_query(F.data == "language")
async def language(
    callback: CallbackQuery,
):

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇷🇺 Русский",
                    callback_data="lang_ru",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🇬🇧 English",
                    callback_data="lang_en",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Меню",
                    callback_data="main_menu",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        "🌐 Выберите язык:",
        reply_markup=kb,
    )

    await callback.answer()


@dp.callback_query(
    F.data.in_({"lang_ru", "lang_en"})
)
async def set_language(
    callback: CallbackQuery,
):

    lang = callback.data[-2:]

    execute(
        """
        UPDATE users
        SET lang=?
        WHERE user_id=?
        """,
        (
            lang,
            callback.from_user.id,
        ),
    )

    await callback.message.edit_text(
        t(
            callback.from_user.id,
            "menu",
        ),
        reply_markup=main_keyboard(
            callback.from_user.id
        ),
    )

    await callback.answer(
        "Язык изменён."
    )


# ============================================================
# ABOUT
# ============================================================

@dp.callback_query(F.data == "about")
async def about(
    callback: CallbackQuery,
):

    await callback.message.edit_text(
        "ℹ️ <b>О сервисе</b>\n\n"
        "FunPay OTC — демонстрационный P2P-сервис для безопасных сделок в Telegram.\n\n"
        "Все операции в этой версии являются виртуальными.",
        reply_markup=back_keyboard(),
    )

    await callback.answer()


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(F.data == "support")
async def support(
    callback: CallbackQuery,
):

    await callback.message.edit_text(
        "🆘 <b>Поддержка</b>\n\n"
        "По всем вопросам обращайтесь к @GiftsforFunpay",
        reply_markup=back_keyboard(),
    )

    await callback.answer()


# ============================================================
# VERIFICATION (НОВЫЙ ОБРАБОТЧИК)
# ============================================================

@dp.callback_query(F.data == "verify")
async def verify_callback(callback: CallbackQuery):
    text = (
        "✅ <b>Верификация</b>\n\n"
        "Верификация доступна пользователям с 30+ успешными сделками и оборотом от 1500 USDT.\n\n"
        "Преимущества:\n"
        "• автовывод средств\n"
        "• приоритетная поддержка\n"
        "• ускоренное решение спорных ситуаций\n\n"
        "Подайте заявку, и администрация рассмотрит её."
    )
    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


# ============================================================
# REFERRAL (НОВЫЙ ОБРАБОТЧИК)
# ============================================================

@dp.callback_query(F.data == "referral")
async def referral_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    ensure_user(callback.from_user)
    row = fetchone("SELECT ref_count FROM users WHERE user_id=?", (user_id,))
    ref_count = row["ref_count"] if row else 0

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    text = (
        "👥 <b>Реферальная система</b>\n\n"
        f"Приглашено: <b>{ref_count}</b> человек\n\n"
        "Ваша реферальная ссылка:\n"
        f"<code>{link}</code>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


# ============================================================
# NEWS
# ============================================================

@dp.callback_query(F.data == "news")
async def news(
    callback: CallbackQuery,
):

    rows = fetchall(
        """
        SELECT content, created_at
        FROM news
        ORDER BY id DESC
        LIMIT 5
        """
    )

    if not rows:

        text = "📢 Новостей пока нет."

    else:

        text = "📢 <b>Последние новости</b>\n\n"

        for row in rows:

            text += (
                f"📌 {row['content']}\n"
                f"🕒 {row['created_at']}\n\n"
            )

    buttons = []

    if callback.from_user.id in ADMIN_IDS:

        buttons.append(
            [
                InlineKeyboardButton(
                    text="📤 Отправить новость",
                    callback_data="admin_news",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text="🛠 Управление сделками",
                    callback_data="admin_deals",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Меню",
                callback_data="main_menu",
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# ============================================================
# ADMIN NEWS
# ============================================================

@dp.callback_query(F.data == "admin_news")
async def admin_news(
    callback: CallbackQuery,
    state: FSMContext,
):

    if callback.from_user.id not in ADMIN_IDS:

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    await callback.message.answer(
        "Введите текст новости:"
    )

    await state.set_state(
        States.news_text
    )

    await callback.answer()


@dp.message(States.news_text)
async def news_text(
    message: Message,
    state: FSMContext,
):

    if message.from_user.id not in ADMIN_IDS:

        await state.clear()
        return

    content = message.text[:4000]

    execute(
        """
        INSERT INTO news(
            admin_id,
            content,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            message.from_user.id,
            content,
            datetime.utcnow().isoformat(),
        ),
    )

    rows = fetchall(
        "SELECT user_id FROM users WHERE banned=0"
    )

    sent = 0

    for row in rows:

        try:

            await bot.send_message(
                row["user_id"],
                "📢 <b>Новость</b>\n\n"
                + content,
            )

            sent += 1

        except Exception:

            pass

    await admin_log(
        message.from_user.id,
        "send_news",
        f"sent={sent}",
    )

    await state.clear()

    await message.answer(
        f"✅ Новость отправлена: {sent}",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


# ============================================================
# ADMIN DEALS
# ============================================================

@dp.callback_query(F.data == "admin_deals")
async def admin_deals(
    callback: CallbackQuery,
):

    if callback.from_user.id not in ADMIN_IDS:

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    rows = fetchall(
        """
        SELECT *
        FROM deals
        WHERE status NOT IN ('completed','cancelled')
        ORDER BY created_at DESC
        LIMIT 50
        """
    )

    if not rows:

        text = "🛠 Активных сделок нет."

    else:

        text = "🛠 <b>Активные сделки</b>\n\n"

        for row in rows:

            text += (
                f"#{row['deal_id']} — "
                f"{row['amount']} {row['currency']}\n"
                f"Seller: @{row['seller_username'] or '—'}\n"
                f"Buyer: @{row['buyer_username'] or '—'}\n"
                f"Status: {row['status']}\n\n"
            )

    buttons = []

    # Ограничиваем количество кнопок, чтобы не превысить лимит Telegram (макс 100 кнопок)
    max_buttons = 10
    display_rows = rows[:max_buttons]

    for row in display_rows:

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"✅ Завершить #{row['deal_id']}",
                    callback_data=f"admin_complete:{row['deal_id']}",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Отменить #{row['deal_id']}",
                    callback_data=f"admin_cancel:{row['deal_id']}",
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"💳 Реквизиты #{row['deal_id']}",
                    callback_data=f"admin_req:{row['deal_id']}",
                )
            ]
        )

    if len(rows) > max_buttons:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"📋 Ещё {len(rows) - max_buttons} сделок (откройте вручную)",
                    callback_data="ignore"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="news",
            )
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


# ============================================================
# ADMIN COMPLETE
# ============================================================

@dp.callback_query(
    F.data.startswith("admin_complete:")
)
async def admin_complete(
    callback: CallbackQuery,
):

    if callback.from_user.id not in ADMIN_IDS:

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    deal_id = callback.data.split(":", 1)[1]

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await callback.answer(
            "Сделка не найдена.",
            show_alert=True,
        )

        return

    if deal["status"] in (
        "completed",
        "cancelled",
    ):

        await callback.answer(
            "Сделка уже закрыта.",
            show_alert=True,
        )

        return

    # Проверяем, подтвердил ли продавец участие
    if deal["seller_confirmed"] != 1:

        await callback.answer(
            "❌ Продавец ещё не подтвердил участие.\n"
            "Дождитесь его подтверждения.",
            show_alert=True,
        )

        return

    amount = deal["amount"] or 0

    # 1% virtual service fee
    fee = max(
        1,
        int(amount * 0.01),
    )

    payout = max(
        0,
        amount - fee,
    )

    if deal["seller_id"]:

        execute(
            """
            UPDATE users
            SET balance=balance+?,
                successful_deals=successful_deals+1
            WHERE user_id=?
            """,
            (
                payout,
                deal["seller_id"],
            ),
        )

    execute(
        """
        UPDATE service_balance
        SET balance=balance+?
        WHERE id=1
        """,
        (fee,),
    )

    execute(
        """
        UPDATE deals
        SET status='completed',
            completed_at=?
        WHERE deal_id=?
        """,
        (
            datetime.utcnow().isoformat(),
            deal_id,
        ),
    )

    await admin_log(
        callback.from_user.id,
        "complete_deal",
        f"deal={deal_id};fee={fee};payout={payout}",
    )

    for uid in (
        deal["seller_id"],
        deal["buyer_id"],
    ):

        if uid:

            await notify(
                uid,
                t(uid, "completed")
                + f"\n\nКомиссия: {fee}\n"
                  f"Зачислено продавцу: {payout}",
            )

    await callback.answer(
        "Сделка завершена."
    )

    await admin_deals(callback)


# ============================================================
# ADMIN CANCEL
# ============================================================

@dp.callback_query(
    F.data.startswith("admin_cancel:")
)
async def admin_cancel(
    callback: CallbackQuery,
):

    if callback.from_user.id not in ADMIN_IDS:

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    deal_id = callback.data.split(":", 1)[1]

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await callback.answer(
            "Сделка не найдена.",
            show_alert=True,
        )

        return

    execute(
        """
        UPDATE deals
        SET status='cancelled'
        WHERE deal_id=?
        """,
        (deal_id,),
    )

    await admin_log(
        callback.from_user.id,
        "cancel_deal",
        f"deal={deal_id}",
    )

    for uid in (
        deal["seller_id"],
        deal["buyer_id"],
    ):

        if uid:

            await notify(
                uid,
                t(uid, "cancelled"),
            )

    await callback.answer(
        "Сделка отменена."
    )

    await admin_deals(callback)


# ============================================================
# ADMIN CHANGE REQUISITES
# ============================================================

@dp.callback_query(
    F.data.startswith("admin_req:")
)
async def admin_req(
    callback: CallbackQuery,
    state: FSMContext,
):

    if callback.from_user.id not in ADMIN_IDS:

        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    deal_id = callback.data.split(":", 1)[1]

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    if not deal:

        await callback.answer(
            "Сделка не найдена.",
            show_alert=True,
        )

        return

    await state.update_data(
        admin_deal_id=deal_id
    )

    await callback.message.answer(
        "Введите новые демонстрационные реквизиты продавца:"
    )

    await state.set_state(
        States.admin_req_deal
    )

    await callback.answer()


@dp.message(States.admin_req_deal)
async def admin_req_save(
    message: Message,
    state: FSMContext,
):

    if message.from_user.id not in ADMIN_IDS:

        await state.clear()
        return

    data = await state.get_data()

    deal_id = data["admin_deal_id"]

    execute(
        """
        UPDATE deals
        SET seller_req=?
        WHERE deal_id=?
        """,
        (
            message.text[:1000],
            deal_id,
        ),
    )

    deal = fetchone(
        "SELECT * FROM deals WHERE deal_id=?",
        (deal_id,),
    )

    await admin_log(
        message.from_user.id,
        "change_requisites",
        f"deal={deal_id}",
    )

    if deal and deal["buyer_id"]:

        await notify(
            deal["buyer_id"],
            f"💳 Реквизиты сделки #{deal_id} были обновлены администрацией.\n\n"
            f"Новые реквизиты:\n{message.text}",
        )

    await state.clear()

    await message.answer(
        "✅ Реквизиты обновлены.",
        reply_markup=main_keyboard(
            message.from_user.id
        ),
    )


# ============================================================
# ADMIN COMMANDS
# ============================================================

@dp.message(Command("stats"))
async def stats(
    message: Message,
):

    if message.from_user.id not in ADMIN_IDS:
        return

    users = fetchone(
        "SELECT COUNT(*) AS c FROM users"
    )["c"]

    active = fetchone(
        """
        SELECT COUNT(*) AS c
        FROM deals
        WHERE status NOT IN ('completed','cancelled')
        """
    )["c"]

    completed = fetchone(
        """
        SELECT COUNT(*) AS c
        FROM deals
        WHERE status='completed'
        """
    )["c"]

    cancelled = fetchone(
        """
        SELECT COUNT(*) AS c
        FROM deals
        WHERE status='cancelled'
        """
    )["c"]

    logs = fetchone(
        "SELECT COUNT(*) AS c FROM admin_logs"
    )["c"]

    service = fetchone(
        "SELECT balance FROM service_balance WHERE id=1"
    )["balance"]

    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: {users}\n"
        f"Активных сделок: {active}\n"
        f"Завершённых: {completed}\n"
        f"Отменённых: {cancelled}\n"
        f"Логов админов: {logs}\n"
        f"Баланс сервиса: {service}"
    )


@dp.message(Command("ban"))
async def ban(
    message: Message,
):

    if message.from_user.id not in ADMIN_IDS:
        return

    parts = message.text.split()

    if len(parts) != 2 or not parts[1].isdigit():

        await message.answer(
            "Использование: /ban USER_ID"
        )

        return

    user_id = int(parts[1])

    execute(
        """
        UPDATE users
        SET banned=1
        WHERE user_id=?
        """,
        (user_id,),
    )

    await admin_log(
        message.from_user.id,
        "ban",
        f"user={user_id}",
    )

    await message.answer(
        f"🚫 Пользователь {user_id} заблокирован."
    )


@dp.message(Command("unban"))
async def unban(
    message: Message,
):

    if message.from_user.id not in ADMIN_IDS:
        return

    parts = message.text.split()

    if len(parts) != 2 or not parts[1].isdigit():

        await message.answer(
            "Использование: /unban USER_ID"
        )

        return

    user_id = int(parts[1])

    execute(
        """
        UPDATE users
        SET banned=0
        WHERE user_id=?
        """,
        (user_id,),
    )

    await admin_log(
        message.from_user.id,
        "unban",
        f"user={user_id}",
    )

    await message.answer(
        f"✅ Пользователь {user_id} разблокирован."
    )


# ============================================================
# AUTO ARCHIVE
# ============================================================

async def archive_worker():

    while True:

        try:

            border = (
                datetime.utcnow()
                - timedelta(hours=24)
            ).isoformat()

            rows = fetchall(
                """
                SELECT *
                FROM deals
                WHERE status='completed'
                AND completed_at IS NOT NULL
                AND completed_at < ?
                """,
                (border,),
            )

            for row in rows:

                execute(
                    """
                    INSERT OR REPLACE INTO archived_deals(
                        deal_id,
                        seller_id,
                        buyer_id,
                        deal_type,
                        description,
                        amount,
                        currency,
                        seller_req,
                        buyer_req,
                        gift_link,
                        status,
                        seller_username,
                        buyer_username,
                        created_at,
                        completed_at,
                        archived_at
                    )
                    VALUES(
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        row["deal_id"],
                        row["seller_id"],
                        row["buyer_id"],
                        row["deal_type"],
                        row["description"],
                        row["amount"],
                        row["currency"],
                        row["seller_req"],
                        row["buyer_req"],
                        row["gift_link"],
                        row["status"],
                        row["seller_username"],
                        row["buyer_username"],
                        row["created_at"],
                        row["completed_at"],
                        datetime.utcnow().isoformat(),
                    ),
                )

                execute(
                    "DELETE FROM deals WHERE deal_id=?",
                    (row["deal_id"],),
                )

        except Exception:

            logger.exception(
                "Archive worker error"
            )

        await asyncio.sleep(3600)


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@dp.error()
async def global_error_handler(
    event,
    exception,
):

    logger.exception(
        "Unhandled bot error",
        exc_info=exception,
    )

    try:

        await admin_error(
            str(exception)
        )

    except Exception:

        pass


# ============================================================
# WEB SERVER (для вебхука – если задан WEBHOOK_URL)
# ============================================================

async def health(request):

    return web.Response(
        text="OK"
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health,
    )

    app.router.add_get(
        "/health",
        health,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logger.info(
        "Web server started on port %s",
        PORT,
    )


# ============================================================
# RUN
# ============================================================

async def main():

    logger.info(
        "Starting bot..."
    )

    asyncio.create_task(
        archive_worker()
    )

    await start_web_server()

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    if WEBHOOK_URL:

        webhook = (
            WEBHOOK_URL.rstrip("/")
            + "/webhook"
        )

        try:

            await bot.set_webhook(
                webhook,
                drop_pending_updates=True,
            )

            info = await bot.get_webhook_info()

            if info.url != webhook:

                raise RuntimeError(
                    "Webhook verification failed"
                )

            logger.info(
                "Webhook configured: %s",
                webhook,
            )

            # aiohttp route for Telegram
            # polling is not started when webhook is configured.

            return

        except Exception:

            logger.exception(
                "Webhook failed, switching to polling"
            )

    # --------------------------------------------------------
    # POLLING
    # --------------------------------------------------------

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    logger.info(
        "Starting polling..."
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "Bot stopped"
        )
