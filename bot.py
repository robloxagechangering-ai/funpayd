import asyncio
import logging
import os
import sqlite3
import uuid
import re
from datetime import datetime, timedelta, timezone

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
WEBHOOK_URL = ""  # Оставьте пустым для polling, либо ваш вебхук
TEST_MODE = False
TEST_CHAT_ID = ""
DB_FILE = "funpay.db"

PHOTO_URL = "https://ibb.co/dsfvdDB7"

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
# DATABASE (ПОТОКОБЕЗОПАСНАЯ)
# ============================================================

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def db_execute(sql, params=()):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()
    return cur

def db_fetchone(sql, params=()):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.close()
    return row

def db_fetchall(sql, params=()):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def column_exists(table, column):
    row = db_fetchone(
        "SELECT name FROM pragma_table_info(?) WHERE name=?",
        (table, column),
    )
    return row is not None

def add_column(table, column, definition):
    if not column_exists(table, column):
        db_execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )

def init_db():
    db_execute("""
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

    db_execute("""
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

    db_execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER,
            PRIMARY KEY(referrer_id, referred_id)
        )
    """)

    db_execute("""
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            gift_link TEXT,
            description TEXT,
            created_at TEXT
        )
    """)

    db_execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            content TEXT,
            created_at TEXT,
            sent_to INTEGER DEFAULT 0
        )
    """)

    db_execute("""
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT
        )
    """)

    db_execute("""
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

    db_execute("""
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

    db_execute("""
        CREATE TABLE IF NOT EXISTS service_balance (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            balance INTEGER DEFAULT 0
        )
    """)

    db_execute("""
        INSERT OR IGNORE INTO service_balance(id, balance)
        VALUES(1, 0)
    """)

    # Миграции старых колонок
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

init_db()


# ============================================================
# FSM (УБРАНЫ ЛИШНИЕ ШАГИ)
# ============================================================

class States(StatesGroup):
    # Deal creation
    deal_role = State()
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


# ============================================================
# TEXTS (ПОЛНЫЙ ПЕРЕВОД И УБРАНЫ ДЕБИЛЬНЫЕ ТОВАРЫ)
# ============================================================

TEXTS = {
    "ru": {
        "menu": "🛡️ <b>FUNPAY</b>\n\nБезопасный гарант для сделок в Telegram.\n\n📌 <b>Что внутри:</b>\n• защита от мошенников\n• удержание средств до завершения сделки\n• история и статусы сделок\n• поддержка через @GiftsforFunpay\n\n⬇️ Выберите действие ниже.",
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
        "verify_btn": "✅ Верификация",
        "referral_btn": "👥 Рефералы",

        "choose_role": "Выберите вашу роль:",
        "seller": "🛒 Продавец",
        "buyer": "🛍 Покупатель",

        "description": "📝 Введите описание сделки:",
        "amount": "💰 Введите сумму целым числом:",
        "currency": "💳 Выберите валюту:",
        "username": "👤 Введите username продавца. Например: @username",
        "req_input": "💳 Введите реквизиты для оплаты:",

        "created": "✅ Сделка создана.\n\nID: <code>{id}</code>\nСумма: <b>{amount} {currency}</b>\n\nСсылка для второго участника:\n<code>{link}</code>",
        "joined": "✅ Вы присоединились к сделке <code>{id}</code>.\n\nОбе стороны теперь подключены.",
        "seller_joined": "✅ Продавец присоединился к сделке #{id}.\n\nЕго реквизиты:\n{reqs}",
        "buyer_joined": "✅ Покупатель присоединился к сделке #{id}.",

        "already": "Вы уже являетесь участником этой сделки.",
        "full": "❌ Эта сделка уже укомплектована.",
        "not_found": "❌ Сделка не найдена.",
        "banned": "🚫 Ваш аккаунт заблокирован.",

        "balance_text": "💰 <b>Баланс</b>\n\nДоступно: <b>{balance}</b> виртуальных единиц\nЗаморожено: <b>{frozen}</b>",
        "profile_text": "👤 <b>Профиль</b>\n\nID: <code>{id}</code>\nUsername: @{username}\nРейтинг: ⭐ {rating:.2f}\nОтзывов: {reviews}",

        "deal_details": "📄 <b>Сделка #{id}</b>\n\nТип: {type}\nОписание: {description}\nСумма: <b>{amount} {currency}</b>\nСтатус: <b>{status}</b>\n\nПродавец: @{seller}\nПокупатель: @{buyer}",

        "cancelled": "❌ Сделка отменена.",
        "completed": "✅ Сделка завершена администратором.",
        "confirm_already": "Вы уже подтвердили участие.",
        "confirm_btn": "✅ Подтвердить участие",
        "cancel_btn": "❌ Отменить",

        "verify_text": "✅ <b>Верификация</b>\n\nВерификация доступна пользователям с 30+ успешными сделками и оборотом от 1500 USDT.\n\nПреимущества:\n• автовывод средств\n• приоритетная поддержка\n• ускоренное решение спорных ситуаций\n\nПодайте заявку, и администрация рассмотрит её.",
        "referral_text": "👥 <b>Реферальная система</b>\n\nПриглашено: <b>{count}</b> человек\n\nВаша реферальная ссылка:\n<code>{link}</code>",
        "about_text": "ℹ️ <b>О сервисе</b>\n\nFunPay OTC — демонстрационный P2P-сервис для безопасных сделок в Telegram.\n\nВсе операции в этой версии являются виртуальными.",
        "support_text": "🆘 <b>Поддержка</b>\n\nПо всем вопросам обращайтесь к @GiftsforFunpay"
    },

    "en": {
        "menu": "🛡️ <b>FUNPAY</b>\n\nSecure guarantor for Telegram deals.\n\n📌 <b>What's inside:</b>\n• protection from scammers\n• funds holding until deal completion\n• deal history and statuses\n• support via @GiftsforFunpay\n\n⬇️ Choose action below.",
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
        "verify_btn": "✅ Verification",
        "referral_btn": "👥 Referrals",

        "choose_role": "Choose your role:",
        "seller": "🛒 Seller",
        "buyer": "🛍 Buyer",

        "description": "📝 Enter deal description:",
        "amount": "💰 Enter amount as a whole number:",
        "currency": "💳 Choose currency:",
        "username": "👤 Enter seller username. Example: @username",
        "req_input": "💳 Enter requisites for payment:",

        "created": "✅ Deal created.\n\nID: <code>{id}</code>\nAmount: <b>{amount} {currency}</b>\n\nJoin link:\n<code>{link}</code>",
        "joined": "✅ You joined deal <code>{id}</code>.",
        "seller_joined": "✅ Seller joined deal #{id}.\n\nRequisites:\n{reqs}",
        "buyer_joined": "✅ Buyer joined deal #{id}.",

        "already": "You are already a participant.",
        "full": "❌ This deal is already full.",
        "not_found": "❌ Deal not found.",
        "banned": "🚫 Your account is blocked.",

        "balance_text": "💰 <b>Balance</b>\n\nAvailable: <b>{balance}</b>\nFrozen: <b>{frozen}</b>",
        "profile_text": "👤 <b>Profile</b>\n\nID: <code>{id}</code>\nUsername: @{username}\nRating: ⭐ {rating:.2f}\nReviews: {reviews}",

        "deal_details": "📄 <b>Deal #{id}</b>\n\nType: {type}\nDescription: {description}\nAmount: <b>{amount} {currency}</b>\nStatus: <b>{status}</b>\n\nSeller: @{seller}\nBuyer: @{buyer}",

        "cancelled": "❌ Deal cancelled.",
        "completed": "✅ Deal completed by administrator.",
        "confirm_already": "You have already confirmed participation.",
        "confirm_btn": "✅ Confirm participation",
        "cancel_btn": "❌ Cancel",

        "verify_text": "✅ <b>Verification</b>\n\nVerification is available to users with 30+ successful deals and turnover from 1500 USDT.\n\nAdvantages:\n• auto withdrawal\n• priority support\n• faster dispute resolution\n\nSubmit a request, and the administration will review it.",
        "referral_text": "👥 <b>Referral system</b>\n\nInvited: <b>{count}</b> people\n\nYour referral link:\n<code>{link}</code>",
        "about_text": "ℹ️ <b>About</b>\n\nFunPay OTC is a demo P2P service for secure deals in Telegram.\n\nAll operations in this version are virtual.",
        "support_text": "🆘 <b>Support</b>\n\nFor any questions, contact @GiftsforFunpay"
    },

    "zh": {
        "menu": "🛡️ <b>FUNPAY</b>\n\nTelegram 交易安全担保人。\n\n📌 内容：\n• 防止诈骗\n• 资金托管直至交易完成\n• 交易历史和状态\n• 通过 @GiftsforFunpay 获得支持\n\n⬇️ 选择以下操作。",
        "deal": "📝 创建交易",
        "my_deals": "📂 我的交易",
        "req": "💳 收款信息",
        "balance": "💰 资金",
        "gifts": "🎁 我的礼品",
        "profile": "👤 个人资料",
        "news": "📢 新闻",
        "about": "ℹ️ 关于服务",
        "support": "🆘 支持",
        "language": "🌐 语言",
        "verify_btn": "✅ 认证",
        "referral_btn": "👥 推荐",

        "choose_role": "选择您的角色：",
        "seller": "🛒 卖家",
        "buyer": "🛍 买家",

        "description": "📝 输入交易描述：",
        "amount": "💰 输入整数金额：",
        "currency": "💳 选择货币：",
        "username": "👤 输入卖家用户名。例如：@username",
        "req_input": "💳 输入付款收款信息：",

        "created": "✅ 交易已创建。\n\nID：<code>{id}</code>\n金额：<b>{amount} {currency}</b>\n\n对方加入链接：\n<code>{link}</code>",
        "joined": "✅ 您已加入交易 <code>{id}</code>。",
        "seller_joined": "✅ 卖家已加入交易 #{id}。\n\n收款信息：\n{reqs}",
        "buyer_joined": "✅ 买家已加入交易 #{id}。",

        "already": "您已经是该交易的参与者。",
        "full": "❌ 此交易已满员。",
        "not_found": "❌ 未找到交易。",
        "banned": "🚫 您的账户已被封锁。",

        "balance_text": "💰 <b>余额</b>\n\n可用：<b>{balance}</b>\n冻结：<b>{frozen}</b>",
        "profile_text": "👤 <b>个人资料</b>\n\nID：<code>{id}</code>\n用户名：@{username}\n评分：⭐ {rating:.2f}\n评论数：{reviews}",

        "deal_details": "📄 <b>交易 #{id}</b>\n\n类型：{type}\n描述：{description}\n金额：<b>{amount} {currency}</b>\n状态：<b>{status}</b>\n\n卖家：@{seller}\n买家：@{buyer}",

        "cancelled": "❌ 交易已取消。",
        "completed": "✅ 交易已由管理员完成。",
        "confirm_already": "您已确认参与。",
        "confirm_btn": "✅ 确认参与",
        "cancel_btn": "❌ 取消",

        "verify_text": "✅ <b>认证</b>\n\n拥有 30 笔以上成功交易且交易额超过 1500 USDT 的用户可获得认证。\n\n优势：\n• 自动提款\n• 优先支持\n• 加速解决争议\n\n提交申请，管理员将进行审核。",
        "referral_text": "👥 <b>推荐系统</b>\n\n已邀请：<b>{count}</b> 人\n\n您的推荐链接：\n<code>{link}</code>",
        "about_text": "ℹ️ <b>关于服务</b>\n\nFunPay OTC 是 Telegram 中用于安全交易的演示 P2P 服务。\n\n此版本中的所有操作均为虚拟操作。",
        "support_text": "🆘 <b>支持</b>\n\n如有任何问题，请联系 @GiftsforFunpay"
    }
}

def get_lang(user_id):
    row = db_fetchone("SELECT lang FROM users WHERE user_id=?", (user_id,))
    return row["lang"] if row and row["lang"] in TEXTS else "ru"

def t(user_id, key, **kwargs):
    lang = get_lang(user_id)
    text = TEXTS[lang].get(key, TEXTS["ru"].get(key, key))
    try:
        return text.format(**kwargs)
    except Exception:
        return text


# ============================================================
# KEYBOARDS (ГЛАВНОЕ МЕНЮ)
# ============================================================

def main_keyboard(user_id):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=t(user_id, "deal"), callback_data="create_deal"),
        InlineKeyboardButton(text=t(user_id, "balance"), callback_data="balance")
    )
    kb.row(
        InlineKeyboardButton(text=t(user_id, "my_deals"), callback_data="my_deals"),
        InlineKeyboardButton(text=t(user_id, "req"), callback_data="requisites")
    )
    kb.row(
        InlineKeyboardButton(text=t(user_id, "language"), callback_data="language"),
        InlineKeyboardButton(text=t(user_id, "support"), callback_data="support")
    )
    kb.row(
        InlineKeyboardButton(text=t(user_id, "verify_btn"), callback_data="verify"),
        InlineKeyboardButton(text=t(user_id, "referral_btn"), callback_data="referral")
    )
    kb.row(
        InlineKeyboardButton(text=t(user_id, "about"), callback_data="about")
    )
    return kb.as_markup()

def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]])


# ============================================================
# HELPERS
# ============================================================

def username_of(user):
    return user.username or f"id{user.id}"

def ensure_user(user):
    exists = db_fetchone("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    if not exists:
        db_execute(
            "INSERT INTO users(user_id, username, lang, created_at) VALUES(?,?,?,?)",
            (user.id, username_of(user), "ru", datetime.utcnow().isoformat())
        )
    else:
        db_execute("UPDATE users SET username=? WHERE user_id=?", (username_of(user), user.id))

def is_banned(user_id):
    row = db_fetchone("SELECT banned FROM users WHERE user_id=?", (user_id,))
    return bool(row and row["banned"])

def active_deals_count(user_id):
    row = db_fetchone("SELECT COUNT(*) AS count FROM deals WHERE (seller_id=? OR buyer_id=?) AND status NOT IN ('completed','cancelled')", (user_id, user_id))
    return row["count"] if row else 0

def frozen_balance(user_id):
    row = db_fetchone("SELECT COALESCE(SUM(amount),0) AS amount FROM deals WHERE seller_id=? AND status='active'", (user_id,))
    return row["amount"] if row else 0

async def notify(user_id, text, reply_markup=None):
    if not user_id: return
    try:
        await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        logger.exception(f"Failed notification to {user_id}")

async def admin_log(admin_id, action, details):
    db_execute("INSERT INTO admin_logs(admin_id, action, details, created_at) VALUES(?,?,?,?)", (admin_id, action, details, datetime.utcnow().isoformat()))


# ============================================================
# ФУНКЦИЯ ОТПРАВКИ ФОТО (ФОТО + ТЕКСТ В 1 СООБЩЕНИИ)
# ============================================================
async def send_safe_media(target, text, reply_markup=None, parse_mode="HTML"):
    chat_id = target.chat.id if hasattr(target, 'chat') else target
    try:
        await bot.send_photo(chat_id=chat_id, photo=PHOTO_URL, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"Ошибка фото: {e}. Отправляю текст.")
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)


# ============================================================
# START (С ЗАЩИТОЙ ОТ СПАМА)
# ============================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    ensure_user(message.from_user)

    # ЗАЩИТА ОТ СПАМА
    data = await state.get_data()
    last_time = data.get("last_start")
    if last_time:
        diff = (datetime.now(timezone.utc) - datetime.fromisoformat(last_time)).total_seconds()
        if diff < 2:
            return
    await state.update_data(last_start=datetime.now(timezone.utc).isoformat())

    await state.clear()

    if is_banned(message.from_user.id):
        await message.answer(t(message.from_user.id, "banned"))
        return

    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        payload = args[1].strip()
        if payload.startswith("deal_"):
            await join_deal(message, payload[5:].strip())
            return
        elif payload.startswith("ref_"):
            try:
                ref_id = int(payload[4:])
                if ref_id != message.from_user.id:
                    if db_fetchone("SELECT user_id FROM users WHERE user_id=?", (ref_id,)):
                        if not db_fetchone("SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?", (ref_id, message.from_user.id)):
                            db_execute("INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (ref_id, message.from_user.id))
                            db_execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?", (ref_id,))
                            await message.answer("✅ Вы были приглашены по реферальной ссылке!")
            except Exception: pass

    await send_safe_media(message, t(message.from_user.id, "menu"), reply_markup=main_keyboard(message.from_user.id), parse_mode="HTML")


# ============================================================
# CANCEL FSM & MAIN MENU
# ============================================================

@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Текущее действие отменено.", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    ensure_user(callback.from_user)
    await send_safe_media(callback.message, t(callback.from_user.id, "menu"), reply_markup=main_keyboard(callback.from_user.id), parse_mode="HTML")
    await callback.answer()


# ============================================================
# CREATE DEAL (УБРАНЫ ЛИШНИЕ ШАГИ)
# ============================================================

@dp.callback_query(F.data == "create_deal")
async def create_deal(callback: CallbackQuery, state: FSMContext):
    ensure_user(callback.from_user)
    if is_banned(callback.from_user.id):
        await callback.answer("🚫 Заблокировано", show_alert=True)
        return
    if active_deals_count(callback.from_user.id) >= 5:
        await callback.answer("❌ Максимум 5 активных сделок.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(callback.from_user.id, "seller"), callback_data="role_seller")],
        [InlineKeyboardButton(text=t(callback.from_user.id, "buyer"), callback_data="role_buyer")],
        [InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]
    ])
    await callback.message.edit_text(t(callback.from_user.id, "choose_role"), reply_markup=kb)
    await state.set_state(States.deal_role)
    await callback.answer()

@dp.callback_query(States.deal_role, F.data.in_({"role_seller", "role_buyer"}))
async def deal_role(callback: CallbackQuery, state: FSMContext):
    await state.update_data(role="seller" if callback.data == "role_seller" else "buyer")
    await callback.message.answer(t(callback.from_user.id, "description"))
    await state.set_state(States.deal_description)
    await callback.answer()

@dp.message(States.deal_description)
async def deal_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text[:2000])
    await message.answer(t(message.from_user.id, "amount"))
    await state.set_state(States.deal_amount)

@dp.message(States.deal_amount)
async def deal_amount(message: Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите целое положительное число.")
        return
    await state.update_data(amount=int(message.text))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 USDT", callback_data="currency_USDT"), InlineKeyboardButton(text="💎 TON", callback_data="currency_TON")],
        [InlineKeyboardButton(text="🇷🇺 RUB", callback_data="currency_RUB"), InlineKeyboardButton(text="🇺🇦 UAH", callback_data="currency_UAH")],
        [InlineKeyboardButton(text="🇧🇾 BYN", callback_data="currency_BYN"), InlineKeyboardButton(text="⭐ Stars", callback_data="currency_Stars")]
    ])
    await message.answer(t(message.from_user.id, "currency"), reply_markup=kb)
    await state.set_state(States.deal_currency)

@dp.callback_query(States.deal_currency, F.data.startswith("currency_"))
async def deal_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.replace("currency_", "")
    await state.update_data(currency=currency)
    data = await state.get_data()

    if data["role"] == "buyer":
        await callback.message.answer(t(callback.from_user.id, "username"))
        await state.set_state(States.deal_seller_username)
    else:
        await callback.message.answer(t(callback.from_user.id, "req_input"))
        await state.set_state(States.deal_requisites)
    await callback.answer()

@dp.message(States.deal_seller_username)
async def deal_seller_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    row = db_fetchone("SELECT user_id, username FROM users WHERE LOWER(username)=LOWER(?)", (username,))
    if not row:
        await message.answer("❌ Этот продавец ещё не запускал бота.")
        return
    if row["user_id"] == message.from_user.id:
        await message.answer("❌ Нельзя создать сделку с самим собой.")
        return
    await state.update_data(seller_username=username, seller_id=row["user_id"])
    await message.answer("Введите ваши реквизиты покупателя или напишите «нет»:")
    await state.set_state(States.deal_requisites)

@dp.message(States.deal_requisites)
async def deal_requisites(message: Message, state: FSMContext):
    data = await state.get_data()
    req = message.text.strip()
    if req.lower() == "нет": req = ""

    deal_id = uuid.uuid4().hex[:10]
    role = data["role"]
    seller_id = message.from_user.id if role == "seller" else None
    buyer_id = message.from_user.id if role == "buyer" else None
    seller_username = username_of(message.from_user) if role == "seller" else data.get("seller_username")
    buyer_username = username_of(message.from_user) if role == "buyer" else ""
    status = "waiting_buyer" if role == "seller" else "waiting_seller"
    seller_req = req if role == "seller" else ""
    buyer_req = req if role == "buyer" else ""

    db_execute("""
        INSERT INTO deals(deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (deal_id, seller_id, buyer_id, "standard", data["description"], data["amount"], data["currency"], seller_req, buyer_req, status, seller_username, buyer_username, datetime.utcnow().isoformat()))

    link = f"https://t.me/{BOT_USERNAME}?start=deal_{deal_id}"
    await state.clear()
    await message.answer(t(message.from_user.id, "created", id=deal_id, amount=data["amount"], currency=data["currency"], link=link), reply_markup=main_keyboard(message.from_user.id), parse_mode="HTML")


# ============================================================
# JOIN DEAL
# ============================================================

async def join_deal(message: Message, deal_id: str):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer(t(user_id, "banned"))
        return

    deal = db_fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await message.answer(t(user_id, "not_found"))
        return
    if deal["seller_id"] == user_id or deal["buyer_id"] == user_id:
        await message.answer(t(user_id, "already"))
        return
    if deal["seller_id"] and deal["buyer_id"]:
        await message.answer(t(user_id, "full"))
        return

    if deal["seller_id"] is None:
        user_row = db_fetchone("SELECT card, crypto, stars_username FROM users WHERE user_id=?", (user_id,))
        req_parts = []
        if user_row:
            if user_row["card"]: req_parts.append(f"Карта: {user_row['card']}")
            if user_row["crypto"]: req_parts.append(f"Crypto: {user_row['crypto']}")
            if user_row["stars_username"]: req_parts.append(f"Stars: @{user_row['stars_username']}")
        seller_req = "\n".join(req_parts) if req_parts else "Не указаны, свяжитесь с продавцом вручную."

        db_execute("UPDATE deals SET seller_id=?, seller_username=?, seller_req=?, status='active' WHERE deal_id=?", (user_id, username_of(message.from_user), seller_req, deal_id))
        await notify(deal["buyer_id"], t(deal["buyer_id"], "seller_joined", id=deal_id, reqs=seller_req))
    elif deal["buyer_id"] is None:
        db_execute("UPDATE deals SET buyer_id=?, buyer_username=?, status='active' WHERE deal_id=?", (user_id, username_of(message.from_user), deal_id))
        await notify(deal["seller_id"], t(deal["seller_id"], "buyer_joined", id=deal_id))

    await message.answer(t(user_id, "joined", id=deal_id), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t(user_id, "confirm_btn"), callback_data=f"confirm:{deal_id}")],
        [InlineKeyboardButton(text=t(user_id, "cancel_btn"), callback_data=f"canceldeal:{deal_id}")]
    ]), parse_mode="HTML")


# ============================================================
# CONFIRM (С ЗАЩИТОЙ ОТ ДУБЛЕЙ)
# ============================================================

@dp.callback_query(F.data.startswith("confirm:"))
async def confirm_deal(callback: CallbackQuery):
    deal_id = callback.data.split(":", 1)[1]
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    if callback.from_user.id != deal["seller_id"]:
        await callback.answer("Подтвердить может продавец.", show_alert=True)
        return
    if deal["seller_confirmed"] == 1:
        await callback.answer(t(callback.from_user.id, "confirm_already"), show_alert=True)
        return

    db_execute("UPDATE deals SET seller_confirmed=1 WHERE deal_id=?", (deal_id,))
    seller_req = deal["seller_req"] or "не указаны"
    await notify(deal["buyer_id"], f"✅ Продавец подтвердил сделку #{deal_id}.\n\nРеквизиты продавца:\n{seller_req}\n\nОжидайте дальнейших действий.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Участие подтверждено.")


# ============================================================
# CANCEL DEAL
# ============================================================

@dp.callback_query(F.data.startswith("canceldeal:"))
async def cancel_deal(callback: CallbackQuery):
    deal_id = callback.data.split(":", 1)[1]
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await callback.answer("Сделка не найдена", show_alert=True)
        return
    if callback.from_user.id not in (deal["seller_id"], deal["buyer_id"]):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    if deal["status"] not in ("waiting_seller", "waiting_buyer"):
        await callback.answer("Активную сделку отменяет администратор.", show_alert=True)
        return

    db_execute("UPDATE deals SET status='cancelled' WHERE deal_id=?", (deal_id,))
    for uid in (deal["seller_id"], deal["buyer_id"]):
        if uid: await notify(uid, t(uid, "cancelled"))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Сделка отменена.")


# ============================================================
# MY DEALS
# ============================================================

@dp.callback_query(F.data == "my_deals")
async def my_deals(callback: CallbackQuery):
    rows = db_fetchall("SELECT * FROM deals WHERE seller_id=? OR buyer_id=? ORDER BY created_at DESC LIMIT 20", (callback.from_user.id, callback.from_user.id))
    if not rows:
        await callback.message.edit_text("📂 У вас пока нет сделок.", reply_markup=back_keyboard())
        await callback.answer()
        return

    kb = InlineKeyboardBuilder()
    for row in rows:
        kb.row(InlineKeyboardButton(text=f"#{row['deal_id']} | {row['amount']} {row['currency']} | {row['status']}"[:64], callback_data=f"dealview:{row['deal_id']}"))
    kb.row(InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu"))
    await callback.message.edit_text("📂 <b>Мои сделки</b>\n\nВыберите сделку:", reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("dealview:"))
async def deal_view(callback: CallbackQuery):
    deal_id = callback.data.split(":", 1)[1]
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal or callback.from_user.id not in (deal["seller_id"], deal["buyer_id"]):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    text = t(callback.from_user.id, "deal_details", id=deal["deal_id"], type=deal["deal_type"], description=deal["description"], amount=deal["amount"], currency=deal["currency"], status=deal["status"], seller=deal["seller_username"] or "—", buyer=deal["buyer_username"] or "—")
    buttons = [[InlineKeyboardButton(text=t(callback.from_user.id, "cancel_btn"), callback_data=f"canceldeal:{deal_id}")]] if deal["status"] in ("waiting_seller", "waiting_buyer") else []
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="my_deals")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


# ============================================================
# BALANCE
# ============================================================

@dp.callback_query(F.data == "balance")
async def balance_menu(callback: CallbackQuery):
    row = db_fetchone("SELECT balance FROM users WHERE user_id=?", (callback.from_user.id,))
    await callback.message.edit_text(t(callback.from_user.id, "balance_text", balance=row["balance"] if row else 0, frozen=frozen_balance(callback.from_user.id)), reply_markup=back_keyboard(), parse_mode="HTML")
    await callback.answer()


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):
    row = db_fetchone("SELECT * FROM users WHERE user_id=?", (callback.from_user.id,))
    if row:
        await callback.message.edit_text(t(callback.from_user.id, "profile_text", id=row["user_id"], username=row["username"] or "—", rating=row["rating"] or 5, reviews=row["reviews_count"] or 0), reply_markup=back_keyboard(), parse_mode="HTML")
    await callback.answer()


# ============================================================
# REQUISITES
# ============================================================

@dp.callback_query(F.data == "requisites")
async def requisites(callback: CallbackQuery):
    row = db_fetchone("SELECT card, crypto, stars_username FROM users WHERE user_id=?", (callback.from_user.id,))
    card, crypto, stars = (row["card"] or "—", row["crypto"] or "—", row["stars_username"] or "—")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Изменить карту", callback_data="req_card")],
        [InlineKeyboardButton(text="₿ Изменить крипто-реквизит", callback_data="req_crypto")],
        [InlineKeyboardButton(text="⭐ Изменить Stars", callback_data="req_stars")],
        [InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]
    ])
    await callback.message.edit_text(f"💳 <b>Ваши реквизиты</b>\n\nКарта: <code>{card}</code>\nКрипто: <code>{crypto}</code>\nStars: <code>{stars}</code>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "req_card")
async def req_card(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите номер карты или другой демонстрационный реквизит:")
    await state.set_state(States.req_card)
    await callback.answer()

@dp.message(States.req_card)
async def req_card_save(message: Message, state: FSMContext):
    db_execute("UPDATE users SET card=? WHERE user_id=?", (message.text[:500], message.from_user.id))
    await state.clear()
    await message.answer("✅ Реквизит сохранён.", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "req_crypto")
async def req_crypto(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите крипто-адрес:")
    await state.set_state(States.req_crypto)
    await callback.answer()

@dp.message(States.req_crypto)
async def req_crypto_save(message: Message, state: FSMContext):
    db_execute("UPDATE users SET crypto=? WHERE user_id=?", (message.text[:500], message.from_user.id))
    await state.clear()
    await message.answer("✅ Крипто-реквизит сохранён.", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "req_stars")
async def req_stars(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите Telegram username для Stars:")
    await state.set_state(States.req_stars)
    await callback.answer()

@dp.message(States.req_stars)
async def req_stars_save(message: Message, state: FSMContext):
    db_execute("UPDATE users SET stars_username=? WHERE user_id=?", (message.text[:100], message.from_user.id))
    await state.clear()
    await message.answer("✅ Stars-реквизит сохранён.", reply_markup=main_keyboard(message.from_user.id))


# ============================================================
# LANGUAGE
# ============================================================

@dp.callback_query(F.data == "language")
async def language(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang_zh")],
        [InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")]
    ])
    await callback.message.edit_text("🌐 Выберите язык:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.in_({"lang_ru", "lang_en", "lang_zh"}))
async def set_language(callback: CallbackQuery):
    lang = callback.data[-2:]
    db_execute("UPDATE users SET lang=? WHERE user_id=?", (lang, callback.from_user.id))
    await callback.message.edit_text(t(callback.from_user.id, "menu"), reply_markup=main_keyboard(callback.from_user.id), parse_mode="HTML")
    await callback.answer("Язык изменён.")


# ============================================================
# ABOUT, SUPPORT, VERIFICATION, REFERRAL
# ============================================================

@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    await send_safe_media(callback.message, t(callback.from_user.id, "about_text"), reply_markup=back_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    await callback.message.edit_text(t(callback.from_user.id, "support_text"), reply_markup=back_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "verify")
async def verify_callback(callback: CallbackQuery):
    await send_safe_media(callback.message, t(callback.from_user.id, "verify_text"), reply_markup=back_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "referral")
async def referral_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    row = db_fetchone("SELECT ref_count FROM users WHERE user_id=?", (user_id,))
    await callback.message.edit_text(t(user_id, "referral_text", count=row["ref_count"] if row else 0, link=f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"), reply_markup=back_keyboard(), parse_mode="HTML")
    await callback.answer()


# ============================================================
# NEWS И АДМИНКА (СОХРАНЕНО КАК В ТВОЕМ КОДЕ)
# ============================================================

@dp.callback_query(F.data == "news")
async def news(callback: CallbackQuery):
    rows = db_fetchall("SELECT content, created_at FROM news ORDER BY id DESC LIMIT 5")
    text = "📢 Новостей пока нет." if not rows else "📢 <b>Последние новости</b>\n\n" + "\n\n".join(f"📌 {r['content']}\n🕒 {r['created_at']}" for r in rows)
    buttons = []
    if callback.from_user.id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="📤 Отправить новость", callback_data="admin_news")])
        buttons.append([InlineKeyboardButton(text="🛠 Управление сделками", callback_data="admin_deals")])
    buttons.append([InlineKeyboardButton(text="🔙 Меню", callback_data="main_menu")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_news")
async def admin_news(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.answer("Введите текст новости:")
    await state.set_state(States.news_text)
    await callback.answer()

@dp.message(States.news_text)
async def news_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    content = message.text[:4000]
    db_execute("INSERT INTO news(admin_id, content, created_at) VALUES(?,?,?)", (message.from_user.id, content, datetime.utcnow().isoformat()))
    rows = db_fetchall("SELECT user_id FROM users WHERE banned=0")
    sent = 0
    for row in rows:
        try:
            await bot.send_message(row["user_id"], "📢 <b>Новость</b>\n\n" + content, parse_mode="HTML")
            sent += 1
        except Exception: pass
    await admin_log(message.from_user.id, "send_news", f"sent={sent}")
    await state.clear()
    await message.answer(f"✅ Новость отправлена: {sent}", reply_markup=main_keyboard(message.from_user.id))

@dp.callback_query(F.data == "admin_deals")
async def admin_deals(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    rows = db_fetchall("SELECT * FROM deals WHERE status NOT IN ('completed','cancelled') ORDER BY created_at DESC LIMIT 50")
    if not rows:
        text = "🛠 Активных сделок нет."
    else:
        text = "🛠 <b>Активные сделки</b>\n\n" + "\n\n".join(f"#{r['deal_id']} — {r['amount']} {r['currency']}\nSeller: @{r['seller_username'] or '—'}\nBuyer: @{r['buyer_username'] or '—'}\nStatus: {r['status']}" for r in rows)

    buttons = []
    for row in rows[:10]:
        buttons.append([InlineKeyboardButton(text=f"✅ Завершить #{row['deal_id']}", callback_data=f"admin_complete:{row['deal_id']}")])
        buttons.append([InlineKeyboardButton(text=f"❌ Отменить #{row['deal_id']}", callback_data=f"admin_cancel:{row['deal_id']}")])
        buttons.append([InlineKeyboardButton(text=f"💳 Реквизиты #{row['deal_id']}", callback_data=f"admin_req:{row['deal_id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="news")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_complete:"))
async def admin_complete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    deal_id = callback.data.split(":", 1)[1]
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal or deal["status"] in ("completed", "cancelled") or deal["seller_confirmed"] != 1:
        await callback.answer("Ошибка завершения.", show_alert=True)
        return
    amount = deal["amount"] or 0
    fee = max(1, int(amount * 0.01))
    payout = max(0, amount - fee)
    if deal["seller_id"]:
        db_execute("UPDATE users SET balance=balance+?, successful_deals=successful_deals+1 WHERE user_id=?", (payout, deal["seller_id"]))
    db_execute("UPDATE service_balance SET balance=balance+? WHERE id=1", (fee,))
    db_execute("UPDATE deals SET status='completed', completed_at=? WHERE deal_id=?", (datetime.utcnow().isoformat(), deal_id))
    await admin_log(callback.from_user.id, "complete_deal", f"deal={deal_id};fee={fee};payout={payout}")
    for uid in (deal["seller_id"], deal["buyer_id"]):
        if uid:
            await notify(uid, t(uid, "completed") + f"\n\nКомиссия: {fee}\nЗачислено продавцу: {payout}")
    await callback.answer("Сделка завершена.")
    await admin_deals(callback)

@dp.callback_query(F.data.startswith("admin_cancel:"))
async def admin_cancel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    deal_id = callback.data.split(":", 1)[1]
    if db_fetchone("SELECT 1 FROM deals WHERE deal_id=?", (deal_id,)):
        db_execute("UPDATE deals SET status='cancelled' WHERE deal_id=?", (deal_id,))
        await admin_log(callback.from_user.id, "cancel_deal", f"deal={deal_id}")
        await callback.answer("Сделка отменена.")
    await admin_deals(callback)

@dp.callback_query(F.data.startswith("admin_req:"))
async def admin_req(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    deal_id = callback.data.split(":", 1)[1]
    if db_fetchone("SELECT 1 FROM deals WHERE deal_id=?", (deal_id,)):
        await state.update_data(admin_deal_id=deal_id)
        await callback.message.answer("Введите новые демонстрационные реквизиты продавца:")
        await state.set_state(States.admin_req_deal)
    await callback.answer()

@dp.message(States.admin_req_deal)
async def admin_req_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data()
    deal_id = data["admin_deal_id"]
    db_execute("UPDATE deals SET seller_req=? WHERE deal_id=?", (message.text[:1000], deal_id))
    deal = db_fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    await admin_log(message.from_user.id, "change_requisites", f"deal={deal_id}")
    if deal and deal["buyer_id"]:
        await notify(deal["buyer_id"], f"💳 Реквизиты сделки #{deal_id} были обновлены администрацией.\n\nНовые реквизиты:\n{message.text}")
    await state.clear()
    await message.answer("✅ Реквизиты обновлены.", reply_markup=main_keyboard(message.from_user.id))


# ============================================================
# /NOVATEAM (ТВОЯ РОДНАЯ ЛОГИКА)
# ============================================================

@dp.message(Command("novateam"))
async def novateam(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    rows = db_fetchall("SELECT deal_id, seller_id, buyer_id, seller_username, buyer_username, amount, currency, description, deal_type FROM deals WHERE (seller_id = ? OR buyer_id = ?) AND status != 'completed'", (user_id, user_id))
    
    if not rows:
        await message.answer("🚫 У вас нет активных сделок.")
        return

    count = 0
    for deal_id, s_id, b_id, s_uname, b_uname, amount, currency, description, deal_type in rows:
        if user_id == b_id: # Нажал покупатель
            if s_id:
                s_lang = get_lang(s_id)
                await bot.send_message(s_id, t(s_id, "novateam_seller") if "novateam_seller" in TEXTS[s_lang] else "💸 Оплата подтверждена. Передайте товар.", parse_mode="HTML")
        elif user_id == s_id: # Нажал продавец
            if b_id:
                await bot.send_message(b_id, t(b_id, "novateam_buyer") if "novateam_buyer" in TEXTS[get_lang(b_id)] else "✅ Сделка завершена.", parse_mode="HTML")
        
        db_execute("UPDATE deals SET status='completed' WHERE deal_id=?", (deal_id,))
        db_execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (user_id,))
        count += 1

    lang = get_lang(user_id)
    await message.answer(f"✅ Подтверждено и завершено {count} сделок.")


# ============================================================
# WEB SERVER И ЗАПУСК
# ============================================================

async def health(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Web server started on port %s", PORT)

async def main():
    logger.info("Starting bot...")
    await start_web_server()
    
    if WEBHOOK_URL:
        webhook = WEBHOOK_URL.rstrip("/") + "/webhook"
        try:
            await bot.set_webhook(webhook, drop_pending_updates=True)
            logger.info("Webhook configured: %s", webhook)
            return
        except Exception:
            logger.exception("Webhook failed, switching to polling")
    
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Starting polling...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
