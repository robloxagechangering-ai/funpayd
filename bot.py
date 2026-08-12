import os
import re
import uuid
import asyncio
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# ============================================================
# ЛОГГИРОВАНИЕ
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
# НАСТРОЙКИ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан ни в переменных окружения, ни в коде!")

BOT_USERNAME = os.getenv("BOT_USERNAME", "FunpayTrustly_robot")
# Картинки для каждой страны (главная и о сервисе)
PHOTO_URLS = {
    "ru": {"main": "https://ibb.co/rG08CGyz", "about": "https://ibb.co/ZpWsBSbx"},
    "en": {"main": "https://ibb.co/qYw6fVPt", "about": "https://ibb.co/TDrvMWX3"},
    "uk": {"main": "https://ibb.co/zVrbJ9Cj", "about": "https://ibb.co/93dcDwgx"},
    "kk": {"main": "https://ibb.co/Z1kD9vdL", "about": "https://ibb.co/9HRX2991"},
    "zh": {"main": "https://ibb.co/nMM9FhHj", "about": "https://ibb.co/MD9gNrcj"},
    "hi": {"main": "https://ibb.co/Xrg1yvFh", "about": "https://ibb.co/3mjhGpQh"},
}
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "8822297551").split(",") if x.strip().isdigit()}
DB_NAME = os.getenv("DB_NAME", "database.db")
COMMISSION_BPS = 100
MAX_ACTIVE_DEALS = 5
ARCHIVE_AFTER_HOURS = 24

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# ВСТРОЕННЫЙ СБРОС ВЕБХУКА
# ============================================================
async def reset_webhook():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅ Вебхук принудительно сброшен.")
    finally:
        await bot.session.close()

# ============================================================
# БАЗА ДАННЫХ
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
# ЛОКАЛИЗАЦИЯ (6 ПОЛНЫХ ЯЗЫКОВ С ТГ ПРЕМИУМ ЭМОДЗИ В ТЕКСТАХ)
# ============================================================
LANG_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська", "kk": "Қазақша", "zh": "中文", "hi": "हिन्दी"}

T = {
    "ru": {
        "lang_choose": "Выберите свой язык:",
        "policy_text": (
            "<tg-emoji emoji-id=\"5985478698722136468\"></tg-emoji> Добро пожаловать\n\n"
            "Для продолжения необходимо принять Политику конфиденциальности:\n\n"
            "• Все данные используются только для работы бота\n"
            "• Передача аккаунта третьим лицам запрещена\n"
            "• При обращении в поддержку нужны доказательства\n"
            "• Бот предоставляется «как есть»\n\n"
            "Нажимая «Принимаю», вы соглашаетесь с условиями политики конфиденциальности."
        ),
        "policy_btn": "📜 Политика конфиденциальности",
        "accept_btn": "✅ Принимаю",
        "main": (
            "<tg-emoji emoji-id=\"6041921818896372382\"></tg-emoji> Добро пожаловать\n\n"
            "<tg-emoji emoji-id=\"5893255507380014983\"></tg-emoji> FunPay - Мы специализированный сервис по обеспечению безопасности вне биржевых сделок.\n\n"
            "<tg-emoji emoji-id=\"5890925363067886150\"></tg-emoji> Автоматизированный алгоритм исполнения.\n"
            "<tg-emoji emoji-id=\"5920515922505765329\"></tg-emoji> Скорость и автоматизация.\n"
            "<tg-emoji emoji-id=\"5902056028513505203\"></tg-emoji>💰 Удобный и быстрый вывод средств.\n\n"
            "• Комиссия сервиса: 1%\n"
            "• Режим работы: 24/7\n"
            "• Техническая поддержка: @FunPayHeIp\n\n"
            "<tg-emoji emoji-id=\"6030445631921721471\"></tg-emoji> Выберите нужный раздел ниже"
        ),
        "create": "Создать Сделку",
        "my_deals": "Мои сделки",
        "req": "Реквизиты",
        "referral": "Рефералы",
        "profile": "Профиль",
        "support": "ТехПоддержка",
        "about": "О сервисе",
        "back": "Назад",
        "profile_text": (
            "<tg-emoji emoji-id=\"6035084557378654059\"></tg-emoji> Профиль\n\n"
            "ID: {id}\n"
            "<tg-emoji emoji-id=\"5893100690988863311\"></tg-emoji> Username: @{username}\n"
            "<tg-emoji emoji-id=\"5395732581780040886\"></tg-emoji> Сделок: {deals}\n"
            "<tg-emoji emoji-id=\"5895514131896733546\"></tg-emoji> Успешных: {successful}\n"
            "Рейтинг: {rating} ({reviews})\n"
            "Рефералов: {refs}\n"
        ),
        "my_deals_title": "<tg-emoji emoji-id=\"5893255507380014983\"></tg-emoji> Мои сделки\n\n",
        "my_deals_empty": "<tg-emoji emoji-id=\"6032636795387121097\"></tg-emoji> У вас нет сделок.",
        "clear_history": "Очистить историю",
        "history_cleared": "✅ История сделок очищена (завершённые сделки заархивированы).",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "<tg-emoji emoji-id=\"5902335789798265487\"></tg-emoji> Выберите вашу роль:",
        "seller": "Я продавец",
        "buyer": "Я покупатель",
        "choose_type": "<tg-emoji emoji-id=\"5836907383292436018\"></tg-emoji> Выберите тип сделки:",
        "account": "Аккаунт / товар",
        "gift": "NFT Gift",
        "description_account": "<tg-emoji emoji-id=\"6039614175917903752\"></tg-emoji> Опишите предмет сделки в виде текста",
        "description_gift": (
            "<tg-emoji emoji-id=\"6039614175917903752\"></tg-emoji> Опишите предмет сделки:\n\n"
            "Например: https://t.me/nft/PlushPepe-111\n"
            "или просто текстовое описание товара"
        ),
        "currency": "<tg-emoji emoji-id=\"5402186569006210455\"></tg-emoji> Выберите валюту:",
        "amount": "💰 Введите сумму целым числом:",
        "requisites": "<tg-emoji emoji-id=\"6039641775377748623\"></tg-emoji> Введите реквизиты для получения оплаты:",
        "seller_username": "👤 Введите @username продавца:",
        "deal_created": (
            "✅ Сделка #<b>{deal_id}</b> успешно создана!\n\n"
            "💵 Валюта: {currency}\n"
            "💰 Сумма: {amount} {currency}\n"
            "🎁 Количество NFT: 1\n\n"
            "📎 Ссылки на NFT:\n• {gift_link}\n\n"
            "🔗 Ссылка для покупателя:\n{link}\n\n"
            "⏳ Ожидайте подключения покупателя."
        ),
        "deal_created_buyer": (
            "✅ Сделка #<b>{deal_id}</b> успешно создана!\n\n"
            "💵 Валюта: {currency}\n"
            "💰 Сумма: {amount} {currency}\n\n"
            "🔗 Ссылка для продавца:\n{link}\n\n"
            "⏳ Ожидайте подключения продавца."
        ),
        "joined": "<tg-emoji emoji-id=\"5895514131896733546\"></tg-emoji> Вы подключились к сделке #<b>{deal_id}</b>.",
        "confirm": "Подтвердить участие",
        "cancel_deal": "Отменить сделку",
        "confirm_seller_notify": "<tg-emoji emoji-id=\"5895514131896733546\"></tg-emoji> Вы подтвердили участие. Ожидайте завершения сделки.",
        "buyer_notify": (
            "<tg-emoji emoji-id=\"5382357040008021292\"></tg-emoji> Продавец подтвердил участие в сделке #<b>{deal_id}</b>.\n\n"
            "<tg-emoji emoji-id=\"5893473283696759404\"></tg-emoji> {amount} {currency}\n"
            "<tg-emoji emoji-id=\"5902056028513505203\"></tg-emoji> Реквизиты продавца:\n{req}"
        ),
        "confirmed": (
            "<tg-emoji emoji-id=\"5902056028513505203\"></tg-emoji> Первичная Оплата подтверждена\n\n"
            "Сделка: #<b>{deal_id}</b>\n"
            "Продавец: @{seller}\n"
            "Рейтинг: {rating}/5\n"
            "Успешных сделок: {successful}\n"
            "Сумма: {amount} {currency}\n"
            "Предмет: {description}\n\n"
            "Ожидаем передачу товара менеджеру @GiftsForFunpay."
        ),
        "deal_active": "<tg-emoji emoji-id=\"5206607081334906820\"></tg-emoji> Активна",
        "language_text": "🌐 Выберите язык:",
        "language_set": "✅ Язык установлен: {lang}.",
        "req_menu": "✏️ Выберите валюту для изменения реквизитов",
        "req_prompt": "✏️ Введите ваш номер {currency} для {currency_name}\n\n📝 Пример:\n{example}",
        "req_saved": "✅ Реквизит сохранён.",
        "support_text": "🆘 Поддержка: @FunPayHeIp\n\nПо всем вопросам обращайтесь к менеджеру.",
        "about_text": (
            "<tg-emoji emoji-id=\"5766994197705921104\"></tg-emoji> Подробнее:\n\n"
            "<tg-emoji emoji-id=\"6039486778597970865\"></tg-emoji> Мы – гарант сервис, наша задача помочь вам провести безопасные сделки, и оформить быстрый вывод!\n\n"
            "<tg-emoji emoji-id=\"6037421444789440735\"></tg-emoji> Ответы на частые вопросы:\n\n"
            "• Как долго происходит вывод? Обычно не более 2-х минут, в редких случаях до 2-х часов.\n\n"
            "• Почему нужно передавать подарок менеджеру, но не покупателю? Причина проста: покупатель может наврать что ему не пришёл подарок, что затягивает ситуацию, но наш менеджер автоматически проверяет наличие NFT подарка и уже обмануть не получится.\n\n"
            "• Как быстро происходит пополнение? Пополнение также занимает не более 2-х минут.\n\n"
            "• Я увидел похожего бота, стоит ли мне доверять? Если вы увидели другого бота кроме @FunpayTrustly_robot, ни в коем случае не проводите с ним сделки!"
        ),
        "admin_done_ok": "✅ Сделка #{deal_id} завершена администратором.",
        "admin_cancel_ok": "❌ Сделка #{deal_id} отменена администратором.",
        "banned": "🚫 Ваш аккаунт заблокирован для операций.",
        "active_limit": "❌ Максимум 5 незавершённых сделок.",
        "not_found": "🚫 Сделка не найдена.",
        "not_allowed": "🚫 Действие недоступно.",
        "invalid": "❌ Некорректное значение.",
        "cancelled": "❌ Сделка #{deal_id} отменена.",
        "self_deal": "❌ Нельзя занять вторую роль в собственной сделке.",
        "full": "ℹ️ У сделки уже заняты обе роли.",
        "already_member": "ℹ️ Вы уже являетесь участником этой сделки.",
    },
    "en": {
        "lang_choose": "Choose your language:",
        "policy_text": (
            "<tg-emoji emoji-id=\"5985478698722136468\"></tg-emoji> Welcome\n\n"
            "To continue, you must accept the Privacy Policy:\n\n"
            "• All data is used solely for the bot's operation\n"
            "• Transfer of the account to third parties is prohibited\n"
            "• Proof is required when contacting support\n"
            "• The bot is provided «as is»\n\n"
            "By clicking «Accept», you agree to the terms of the privacy policy."
        ),
        "policy_btn": "📜 Privacy Policy",
        "accept_btn": "✅ Accept",
        "main": (
            "<tg-emoji emoji-id=\"6041921818896372382\"></tg-emoji> Welcome\n\n"
            "<tg-emoji emoji-id=\"5893255507380014983\"></tg-emoji> FunPay - We are a specialized service for ensuring security in off-exchange transactions.\n\n"
            "<tg-emoji emoji-id=\"5890925363067886150\"></tg-emoji> Automated execution algorithm.\n"
            "<tg-emoji emoji-id=\"5920515922505765329\"></tg-emoji> Speed and automation.\n"
            "<tg-emoji emoji-id=\"5902056028513505203\"></tg-emoji>💰 Convenient and fast withdrawal of funds.\n\n"
            "• Service commission: 1%\n"
            "• Operating mode: 24/7\n"
            "• Technical support: @FunPayHeIp\n\n"
            "<tg-emoji emoji-id=\"6030445631921721471\"></tg-emoji> Select the section you need below"
        ),
        "create": "Create Deal",
        "my_deals": "My deals",
        "req": "Requisites",
        "referral": "Referrals",
        "profile": "Profile",
        "support": "TechSupport",
        "about": "About",
        "back": "Back",
        "profile_text": (
            "<tg-emoji emoji-id=\"6035084557378654059\"></tg-emoji> Profile\n\n"
            "ID: {id}\n"
            "<tg-emoji emoji-id=\"5893100690988863311\"></tg-emoji> Username: @{username}\n"
            "<tg-emoji emoji-id=\"5395732581780040886\"></tg-emoji> Deals: {deals}\n"
            "<tg-emoji emoji-id=\"5895514131896733546\"></tg-emoji> Successful: {successful}\n"
            "Rating: {rating} ({reviews})\n"
            "Referrals: {refs}\n"
        ),
        "my_deals_title": "<tg-emoji emoji-id=\"5893255507380014983\"></tg-emoji> My deals\n\n",
        "my_deals_empty": "<tg-emoji emoji-id=\"6032636795387121097\"></tg-emoji> You have no deals.",
        "clear_history": "Clear history",
        "curr_usdt": "USDT",
        "curr_rub": "RUB",
        "curr_uah": "UAH",
        "curr_byn": "BYN",
        "curr_ton": "TON",
        "curr_stars": "STARS",
        "curr_kzt": "KZT",
        "choose_role": "<tg-emoji emoji-id=\"5902335789798265487\"></tg-emoji> Choose your role:",
        "seller": "I am seller",
        "buyer": "I am buyer",
        "choose_type": "<tg-emoji emoji-id=\"5836907383292436018\"></tg-emoji> Choose deal type:",
        "account": "Account / goods",
        "gift": "NFT Gift",
        "description_account": "<tg-emoji emoji-id=\"6039614175917903752\"></tg-emoji> Describe the subject of the deal in text",
        "description_gift": (
            "<tg-emoji emoji-id=\"6039614175917903752\"></tg-emoji> Describe the subject of the deal:\n\n"
            "Example: https://t.me/nft/PlushPepe-111\n"
            "or just a text description"
        ),
        "currency": "<tg-emoji emoji-id=\"5402186569006210455\"></tg-emoji> Choose currency:",
        "amount": "💰 Enter integer amount:",
        "requisites": "<tg-emoji emoji-id=\"6039641775377748623\"></tg-emoji> Enter receiving requisites:",
        "seller_username": "👤 Enter seller @username:",
        "deal_created": (
            "✅ Deal #<b>{deal_id}</b> successfully created!\n\n"
            "💵 Currency: {currency}\n"
            "💰 Amount: {amount} {currency}\n"
            "🎁 NFT Quantity: 1\n\n"
            "📎 NFT Links:\n• {gift_link}\n\n"
            "🔗 Buyer link:\n{link}\n\n"
            "⏳ Waiting for buyer to connect."
        ),
        "deal_created_buyer": (
            "✅ Deal #<b>{deal_id}</b> successfully created!\n\n"
            "💵 Currency: {currency}\n"
            "💰 Amount: {amount} {currency}\n\n"
            "🔗 Seller link:\n{link}\n\n"
            "⏳ Waiting for seller to connect."
        ),
        "joined": "<tg-emoji emoji-id=\"5895514131896733546\"></tg-emoji> You joined deal #<b>{deal_id}</b>.",
        "confirm": "Confirm participation",
        "cancel_deal": "Cancel deal",
        "confirm_seller_notify": "<tg-emoji emoji-id=\"5895514131896733546\"></tg-emoji> You confirmed participation. Waiting for deal completion.",
        "buyer_notify": (
            "<tg-emoji emoji-id=\"5382357040008021292\"></tg-emoji> Seller confirmed participation in deal #<b>{deal_id}</b>.\n\n"
            "<tg-emoji emoji-id=\"5893473283696759404\"></tg-emoji> {amount} {currency}\n"
            "<tg-emoji emoji-id=\"5902056028513505203\"></tg-emoji> Seller requisites:\n{req}"
        ),
        "confirmed": (
            "<tg-emoji emoji-id=\"5902056028513505203\"></tg-emoji> Primary Payment confirmed\n\n"
            "Deal: #<b>{deal_id}</b>\n"
            "Seller: @{seller}\n"
            "Rating: {rating}/5\n"
            "Successful deals: {successful}\n"
            "Amount: {amount} {currency}\n"
            "Item: {description}\n\n"
            "Waiting for goods transfer to manager @GiftsForFunpay."
        ),
        "deal_active": "<tg-emoji emoji-id=\"5206607081334906820\"></tg-emoji> Active",
        "language_text": "🌐 Choose language:",
        "language_set": "✅ Language set: {lang}.",
        "req_menu": "✏️ Choose currency to change requisites",
        "req_prompt": "✏️ Enter your {currency} for {currency_name}\n\n📝 Example:\n{example}",
        "req_saved": "✅ Requisite saved.",
        "support_text": "🆘 Support: @FunPayHeIp\n\nFor any questions, contact the manager.",
        "about_text": (
            "<tg-emoji emoji-id=\"5766994197705921104\"></tg-emoji> Details:\n\n"
            "<tg-emoji emoji-id=\"6039486778597970865\"></tg-emoji> We are a guarantor service, our task is to help you conduct safe deals and process fast withdrawals!\n\n"
            "<tg-emoji emoji-id=\"6037421444789440735\"></tg-emoji> Frequently asked questions:\n\n"
            "• How long does a withdrawal take? Usually no more than 2 minutes, in rare cases up to 2 hours.\n\n"
            "• Why should the gift be transferred to the manager and not the buyer? The reason is simple: the buyer could lie that they didn't receive the gift, which delays the situation, but our manager automatically checks the presence of the NFT gift and it will not be possible to deceive.\n\n"
            "• How fast is the deposit? Deposit also takes no more than 2 minutes.\n\n"
            "• I saw a similar bot, should I trust it? If you see another bot besides @FunpayTrustly_robot, do not conduct deals with it under any circumstances!"
        ),
        "admin_done_ok": "✅ Deal #{deal_id} completed by admin.",
        "admin_cancel_ok": "❌ Deal #{deal_id} cancelled by admin.",
        "banned": "🚫 Your account is blocked.",
        "active_limit": "❌ Maximum 5 active deals.",
        "not_found": "🚫 Deal not found.",
        "not_allowed": "🚫 Action not allowed.",
        "invalid": "❌ Invalid value.",
        "cancelled": "❌ Deal #{deal_id} cancelled.",
        "self_deal": "❌ You cannot take the second role in your own deal.",
        "full": "ℹ️ Both roles are already taken.",
        "already_member": "ℹ️ You are already a participant.",
    }
}

# ============================================================
# ФУНКЦИИ ПЕРЕВОДА И БАЗЫ
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
        "waiting_buyer": "🟡 Ожидает покупателя",
        "waiting_seller": "🟡 Ожидает продавца",
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
                logger.warning(f"Ошибка отправки фото ({e}), отправляю просто текст.")
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
            await bot.send_message(admin_id, "⚠️ Ошибка бота:\n" + text)
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
# КЛАВИАТУРЫ (ИСПРАВЛЕННЫЙ СИНТАКСИС TG PREMIUM!)
# ============================================================
def kb_main(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("create", lang), callback_data="create_deal", emoji_id="5766994197705921104")],
        [InlineKeyboardButton(text=tr("my_deals", lang), callback_data="my_deals", emoji_id="6041730074376410123"),
         InlineKeyboardButton(text=tr("req", lang), callback_data="requisites", emoji_id="5902056028513505203")],
        [InlineKeyboardButton(text=tr("referral", lang), callback_data="referral", emoji_id="5778455936410588193"),
         InlineKeyboardButton(text=tr("profile", lang), callback_data="profile", emoji_id="6035084557378654059")],
        [InlineKeyboardButton(text=tr("language", lang), callback_data="lang", emoji_id="5776233299424843260"),
         InlineKeyboardButton(text=tr("support", lang), url="https://t.me/FunPayHeIp", emoji_id="6030400221232501136")],
        [InlineKeyboardButton(text=tr("about", lang), callback_data="about", emoji_id="6028435952299413210")],
    ])

def kb_back(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")]])

def kb_roles(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("seller", lang), callback_data="role_seller", emoji_id="5963103826075456248"),
         InlineKeyboardButton(text=tr("buyer", lang), callback_data="role_buyer", emoji_id="5963087934696459905")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")]
    ])

def kb_types(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("account", lang), callback_data="type_account", emoji_id="5836907383292436018"),
         InlineKeyboardButton(text=tr("gift", lang), callback_data="type_gift", emoji_id="5836907383292436018")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")]
    ])

def kb_currencies(lang, prefix):
    labels = [
        ("USDT", tr("curr_usdt", lang), "5427168083074628963"),
        ("RUB", tr("curr_rub", lang), "5231449120635370684"), ("UAH", tr("curr_uah", lang), "5290017777174722330"),
        ("BYN", tr("curr_byn", lang), "5231005931550030290"), ("TON", tr("curr_ton", lang), "5427168083074628963"),
        ("STARS", tr("curr_stars", lang), "5438496463044752972"), ("KZT", tr("curr_kzt", lang), "5402186569006210455"),
    ]
    rows = []
    # USDT сверху
    rows.append([InlineKeyboardButton(text=labels[0][1], callback_data=f"{prefix}{labels[0][0]}", emoji_id=labels[0][2])])
    for i in range(1, len(labels), 2):
        pair = labels[i:i+2]
        row = [InlineKeyboardButton(text=pair[0][1], callback_data=f"{prefix}{pair[0][0]}", emoji_id=pair[0][2])]
        if len(pair) > 1:
            row.append(InlineKeyboardButton(text=pair[1][1], callback_data=f"{prefix}{pair[1][0]}", emoji_id=pair[1][2]))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_balance(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("deposit", lang), callback_data="deposit")],
        [InlineKeyboardButton(text=tr("withdraw", lang), callback_data="withdraw")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")]
    ])

def kb_my_deals(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("clear_history", lang), callback_data="clear_history", emoji_id="5445267414562389170")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")]
    ])

# ============================================================
# FSM
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
# ОБРАБОТЧИКИ (СТАРТ, ОНБОРДИНГ, ПОЛИТИКА, МЕНЮ)
# ============================================================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user)
    uid = message.from_user.id
    username = message.from_user.username or ""
    
    row = fetchone("SELECT lang, accepted_policy FROM users WHERE user_id=?", (uid,))
    if not row or row["accepted_policy"] == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="onboard_ru")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="onboard_en")],
            [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="onboard_uk")],
            [InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="onboard_kk")],
            [InlineKeyboardButton(text="🇨🇳 中文", callback_data="onboard_zh")],
            [InlineKeyboardButton(text="🇮🇳 हिन्दी", callback_data="onboard_hi")]
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
            [InlineKeyboardButton(text=tr("policy_btn", lang), url="https://t.me/PrivatePoliceFunpay")],
            [InlineKeyboardButton(text=tr("accept_btn", lang), callback_data="accept_policy")]
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
        await message.answer("❌ Описание слишком короткое.")
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
        await message.answer("❌ Реквизиты слишком короткие.")
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
        logger.exception(f"Ошибка создания сделки: {e}")
        await message.answer("🚫 Ошибка при создании сделки. Попробуйте позже.")
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
        await message.answer("❌ Описание слишком короткое.")
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
        logger.exception(f"Ошибка создания сделки покупателем: {e}")
        await message.answer("🚫 Ошибка при создании сделки. Попробуйте позже.")
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
        await notify(seller_id, f"📦 Покупатель @{message.from_user.username or uid} создал сделку #{deal_id} и указал вас продавцом.\n🔗 {deal_link(deal_id)}\nОткройте ссылку для подтверждения роли.")

# ============================================================
# ПОДТВЕРЖДЕНИЕ, ОТМЕНА, МОИ СДЕЛКИ
# ============================================================
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
    await notify(deal["buyer_id"], tr("buyer_notify", buyer_lang).format(deal_id=deal_id, amount=deal["amount"], currency=deal["currency"], req=deal["seller_req"] or "не указаны"))
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
    text = f"📌 Сделка #{deal_id}\n\nТип: {deal['deal_type']}\nОписание: {deal['description']}\nСумма: {deal['amount']} {deal['currency']}\nПродавец: @{deal['seller_username'] or '-'}\nПокупатель: @{deal['buyer_username'] or '-'}\nСтатус: {status_text(deal['status'], lang)}\n"
    if deal["seller_req"] and uid == deal["seller_id"]:
        text += f"\nРеквизиты продавца: {deal['seller_req']}"
    rows = []
    if deal["status"] in ("waiting_buyer", "waiting_seller", "waiting"):
        rows.append([InlineKeyboardButton(text=tr("cancel_deal", lang), callback_data=f"cancel_{deal_id}")])
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="my_deals", emoji_id="5960671702059848143")])
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
        buttons.append([InlineKeyboardButton(text=f"🔎 #{d['deal_id']}", callback_data=f"dealview_{d['deal_id']}")])
    buttons.append([InlineKeyboardButton(text=tr("clear_history", lang), callback_data="clear_history", emoji_id="5445267414562389170")])
    buttons.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")])
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
# /novateam И ПОДТВЕРЖДЕНИЕ ОПЛАТЫ (СЕКРЕТНАЯ, ДЛЯ ВСЕХ)
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
    await message.answer(f"✅ Завершено последних сделок: {count}")

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
# ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (ПРОФИЛЬ, БАЛАНС, РЕКВИЗИТЫ, ЯЗЫК, О СЕРВИСЕ)
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
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="setlang_en")],
        [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="setlang_uk")],
        [InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="setlang_kk")],
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="setlang_zh")],
        [InlineKeyboardButton(text="🇮🇳 हिन्दी", callback_data="setlang_hi")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu", emoji_id="5960671702059848143")]
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
        "RUB": "Пример: +7 123 456 78 90\n2020 2020 2020 2020",
        "USDT": "Пример: UQ... или EQ...",
        "UAH": "Пример: +380 67 123 45 67\n2020 2020 2020 2020",
        "BYN": "Пример: +375 29 123 45 67\n2020 2020 2020 2020",
        "TON": "Пример: UQ... или EQ...",
        "STARS": "Пример: @username\nhttps://t.me/username",
        "KZT": "Пример: +7 707 123 45 67\n2020 2020 2020 2020",
    }
    currency_names = {
        "RUB": "телефона или карту для RUB",
        "USDT": "крипто кошелька для USDT",
        "UAH": "телефона или карту для UAH",
        "BYN": "телефона или карту для BYN",
        "TON": "крипто кошелька для TON",
        "STARS": "@Username для STARS",
        "KZT": "телефона или карту для KZT",
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
# АДМИН-ПАНЕЛЬ И ЗАПУСК ВЕБХУКА
# ============================================================
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only", user_lang(message.from_user.id)))
        return
    await message.answer("🛠 Админ-панель\n\n/stats — статистика\n/sendnews — рассылка\n/novateam [DEAL_ID] — завершить\n/ban USER_ID — блокировка\n/unban USER_ID — разблокировка")

@dp.errors()
async def global_error_handler(event):
    logger.exception("Unhandled aiogram error: %s", event)
    try:
        await admin_error(str(event))
    except Exception:
        pass
    return True

async def root(request):
    return web.Response(text="FUNPAY is running")

async def health(request):
    return web.json_response({"status": "ok"})

async def webhook(request):
    try:
        data = await request.json()
        update = types.Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        logger.exception("Webhook error")
        return web.Response(status=500, text=str(e))

async def run_webhook():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/health", health)
    app.router.add_post("/", webhook)
    app.router.add_post("/webhook", webhook)

    webhook_url = WEBHOOK_URL
    if not webhook_url:
        logger.warning("WEBHOOK_URL is empty; using polling.")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
        return

    full_url = webhook_url
    if not full_url.endswith("/webhook"):
        full_url = full_url + "/webhook"

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    try:
        await bot.set_webhook(url=full_url, drop_pending_updates=True, allowed_updates=dp.resolve_used_update_types())
        info = await bot.get_webhook_info()
        if info.url != full_url:
            raise RuntimeError(f"Webhook verification failed: expected {full_url}, got {info.url}")
        logger.info("Webhook enabled: %s", full_url)
        await asyncio.Future()
    except Exception:
        logger.exception("Webhook failed; switching to polling.")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()

async def archive_loop():
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=ARCHIVE_AFTER_HOURS)
            rows = fetchall("SELECT * FROM deals WHERE status='completed' AND completed_at IS NOT NULL")
            for row in rows:
                completed_at = datetime.fromisoformat(row["completed_at"])
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=timezone.utc)
                if completed_at <= cutoff:
                    execute("INSERT OR REPLACE INTO archived_deals (deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, gift_link, status, seller_username, buyer_username, created_at, completed_at, confirmed_at, commission, archived_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row["deal_id"], row["seller_id"], row["buyer_id"], row["deal_type"], row["description"], row["amount"], row["currency"], row["seller_req"], row["buyer_req"], row["gift_link"], row["status"], row["seller_username"], row["buyer_username"], row["created_at"], row["completed_at"], row["confirmed_at"], row["commission"], datetime.now(timezone.utc).isoformat()))
                    execute("DELETE FROM deals WHERE deal_id=?", (row["deal_id"],))
        except Exception:
            logger.exception("Archive loop error")
        await asyncio.sleep(3600)

async def main():
    init_db()
    asyncio.create_task(archive_loop())
    await run_webhook()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--reset":
        asyncio.run(reset_webhook())
        sys.exit(0)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
