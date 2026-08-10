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
# FUNPAY OTC DEMO — Telegram bot
# Только демонстрационная версия: деньги и сделки виртуальные.
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
# НАСТРОЙКИ БОТА (ИЗМЕНЕНА ЛОГИКА ТОКЕНА)
# ============================================================

# Если Render не подставит токен, бот возьмёт этот запасной токен (впиши свой ниже):
BOT_TOKEN = os.getenv("BOT_TOKEN", "8497462129:AAEC2hO1pZVwXA2eATQp4uk3YdSX63K0hAs")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан ни в переменных окружения, ни в коде!")

BOT_USERNAME = os.getenv("BOT_USERNAME", "FunpayTrustly_robot")
PHOTO_URL = os.getenv("PHOTO_URL", "https://ibb.co/ycJNGhRQ")
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8625870625").split(",")
    if x.strip().isdigit()
}
TEST_MODE = os.getenv("TEST_MODE", "0").lower() in {"1", "true", "yes"}
TEST_CHAT_ID = os.getenv("TEST_CHAT_ID", "")

DB_NAME = os.getenv("DB_NAME", "database.db")
COMMISSION_BPS = 100  # 1%
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


def table_columns(conn, table):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def add_column_if_missing(conn, table, column, definition):
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with db() as conn:
        conn.executescript(
            """
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
                created_at TEXT
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
            """
        )

        # Backward-compatible migrations for the original database.
        user_migrations = {
            "balance": "INTEGER DEFAULT 0",
            "frozen_balance": "INTEGER DEFAULT 0",
            "rating": "REAL DEFAULT 0",
            "reviews_count": "INTEGER DEFAULT 0",
            "banned": "INTEGER DEFAULT 0",
            "created_at": "TEXT",
        }
        deal_migrations = {
            "buyer_id": "INTEGER",
            "seller_id": "INTEGER",
            "seller_username": "TEXT",
            "buyer_username": "TEXT",
            "seller_req": "TEXT",
            "buyer_req": "TEXT",
            "gift_link": "TEXT",
            "status": "TEXT DEFAULT 'waiting_buyer'",
            "completed_at": "TEXT",
            "confirmed_at": "TEXT",
            "commission": "INTEGER DEFAULT 0",
        }

        for col, definition in user_migrations.items():
            add_column_if_missing(conn, "users", col, definition)

        for col, definition in deal_migrations.items():
            add_column_if_missing(conn, "deals", col, definition)

        conn.execute(
            "INSERT OR IGNORE INTO service_balance(id, balance) VALUES (1, 0)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO admin_settings(id, last_news_id) VALUES (1, 0)"
        )

        # Normalize old status values from the original version.
        conn.execute(
            "UPDATE deals SET status='waiting_buyer' "
            "WHERE status='waiting' AND seller_id IS NOT NULL AND buyer_id IS NULL"
        )
        conn.execute(
            "UPDATE deals SET status='active' "
            "WHERE status='waiting' AND seller_id IS NOT NULL AND buyer_id IS NOT NULL"
        )

        conn.commit()


init_db()


# ============================================================
# LOCALIZATION
# ============================================================

LANG_NAMES = {
    "ru": "Русский",
    "en": "English",
    "uk": "Українська",
    "kk": "Қазақша",
    "zh": "中文",
    "hi": "हिन्दी",
}

T = {
    "ru": {
        "main": (
            "🛡️ <b>FUNPAY OTC — DEMO</b>\n\n"
            "Демонстрационный сервис сделок в Telegram.\n"
            "Все балансы и операции виртуальные.\n\n"
            "Выберите действие:"
        ),
        "create": "📝 Создать сделку",
        "funds": "💰 Баланс",
        "my_deals": "📋 Мои сделки",
        "req": "💳 Реквизиты",
        "gifts": "🎁 Мои подарки",
        "profile": "👤 Профиль",
        "news": "📢 Новостник",
        "language": "🌐 Язык",
        "support": "🆘 Поддержка",
        "about": "ℹ️ О сервисе",
        "back": "🔙 Назад",
        "seller": "👤 Я продавец",
        "buyer": "🛒 Я покупатель",
        "account": "📦 Товар / аккаунт",
        "gift": "🎁 NFT Gift",
        "choose_role": "Выберите вашу роль:",
        "choose_type": "Выберите тип сделки:",
        "description": "📝 Введите описание сделки:",
        "currency": "💱 Выберите валюту:",
        "amount": "💰 Введите сумму целым числом:",
        "requisites": "💳 Введите реквизиты для получения оплаты:",
        "seller_username": "👤 Введите @username продавца:",
        "deal_created": (
            "✅ Сделка <b>#{deal_id}</b> создана.\n\n"
            "🔗 Ссылка для контрагента:\n"
            "{link}\n\n"
            "Статус: ожидает второго участника."
        ),
        "deal_created_buyer": (
            "✅ Сделка <b>#{deal_id}</b> создана.\n\n"
            "Ожидается подключение продавца.\n"
            "🔗 Ссылка:\n{link}"
        ),
        "joined": "✅ Вы подключились к сделке #{deal_id}.",
        "already_member": "ℹ️ Вы уже являетесь участником этой сделки.",
        "full": "ℹ️ У сделки уже заняты обе роли.",
        "self_deal": "❌ Нельзя занять вторую роль в собственной сделке.",
        "confirm": "✅ Подтвердить участие",
        "cancel_deal": "❌ Отменить сделку",
        "details": "🔎 Детали",
        "cancelled": "❌ Сделка #{deal_id} отменена.",
        "not_found": "🚫 Сделка не найдена.",
        "not_allowed": "🚫 Действие недоступно.",
        "confirmed": "✅ Вы подтвердили участие. Ожидайте оплаты от покупателя.",
        "buyer_notify": (
            "📩 Продавец подтвердил участие в сделке #{deal_id}.\n\n"
            "💰 {amount} {currency}\n"
            "💳 Реквизиты продавца:\n{req}\n\n"
            "Это демонстрационная операция."
        ),
        "deal_active": "🟢 Активна",
        "waiting_buyer": "🟡 Ожидает покупателя",
        "waiting_seller": "🟡 Ожидает продавца",
        "completed": "✅ Завершена",
        "cancelled_status": "❌ Отменена",
        "balance": (
            "💰 <b>Баланс</b>\n\n"
            "Доступно: <b>{balance}</b>\n"
            "Заморожено: <b>{frozen}</b>\n"
            "Сервисный баланс: виртуальный\n"
        ),
        "deposit": "➕ Пополнить",
        "withdraw": "➖ Вывести",
        "deposit_amount": "Введите виртуальную сумму пополнения:",
        "withdraw_amount": "Введите виртуальную сумму вывода:",
        "deposit_ok": "✅ Виртуальный баланс пополнен на {amount}.",
        "withdraw_ok": "✅ Виртуально выведено {amount}.",
        "not_enough": "❌ Недостаточно виртуальных средств.",
        "positive": "❌ Сумма должна быть больше нуля.",
        "my_deals_empty": "📭 У вас нет сделок.",
        "my_deals_title": "📋 <b>Мои сделки</b>\n\n",
        "profile_text": (
            "👤 <b>Профиль</b>\n\n"
            "ID: <code>{id}</code>\n"
            "Username: @{username}\n"
            "Сделок: {deals}\n"
            "Успешных: {successful}\n"
            "Рейтинг: {rating} ({reviews})\n"
            "Рефералов: {refs}\n"
        ),
        "news_empty": "📢 Новостей пока нет.",
        "support_text": "🆘 Поддержка: @GiftsforFunpay\n\nДля тестовой версии используйте виртуальные операции.",
        "about_text": "ℹ️ Это учебная демонстрация P2P-сделок. Реальных платежей нет.",
        "language_text": "🌐 Выберите язык:",
        "language_set": "✅ Язык установлен: {lang}",
        "req_menu": "💳 Выберите реквизит для изменения:",
        "card_prompt": "Введите номер карты (демо-данные):",
        "crypto_prompt": "Введите адрес криптокошелька (демо-данные):",
        "stars_prompt": "Введите @username для Stars (демо-данные):",
        "req_saved": "✅ Реквизит сохранён.",
        "gifts_empty": "🎁 Сохранённых подарков нет.",
        "gift_add": "➕ Добавить подарок",
        "gift_link_prompt": "Введите ссылку на подарок:",
        "gift_desc_prompt": "Введите описание подарка:",
        "gift_saved": "✅ Подарок сохранён.",
        "admin_only": "🚫 Только для администратора.",
        "banned": "🚫 Ваш аккаунт заблокирован для операций.",
        "active_limit": "❌ Максимум 5 незавершённых сделок.",
        "seller_not_found": "❌ Пользователь с таким username не найден в базе бота.",
        "cancelled_fsm": "✅ Текущее действие отменено.",
        "stats": (
            "📊 <b>Статистика</b>\n\n"
            "Пользователей: {users}\n"
            "Активных: {active}\n"
            "Завершённых: {completed}\n"
            "Отменённых: {cancelled}\n"
            "Всего сделок: {total}\n"
            "Логов админов: {logs}\n"
            "Баланс сервиса: {service}\n"
        ),
        "review_prompt": "⭐ Оцените контрагента от 1 до 5:",
        "review_comment": "Напишите короткий комментарий или отправьте '-'",
        "review_saved": "✅ Отзыв сохранён. Спасибо!",
        "admin_deals": "🛠 Управление сделками",
        "admin_done": "✅ Завершить",
        "admin_cancel": "❌ Отменить",
        "admin_req": "💳 Изменить реквизиты",
        "admin_req_prompt": "Введите новые реквизиты продавца:",
        "admin_done_ok": "✅ Сделка #{deal_id} завершена администратором.",
        "admin_cancel_ok": "❌ Сделка #{deal_id} отменена администратором.",
        "admin_req_ok": "✅ Реквизиты сделки #{deal_id} изменены.",
        "ban_ok": "🚫 Пользователь {id} заблокирован.",
        "unban_ok": "✅ Пользователь {id} разблокирован.",
        "invalid": "❌ Некорректное значение.",
    },
    "en": {
        "main": "🛡️ <b>FUNPAY OTC — DEMO</b>\n\nDemo P2P deal service.\nAll balances and operations are virtual.\n\nChoose an action:",
        "create": "📝 Create deal", "funds": "💰 Balance", "my_deals": "📋 My deals",
        "req": "💳 Requisites", "gifts": "🎁 My gifts", "profile": "👤 Profile",
        "news": "📢 News", "language": "🌐 Language", "support": "🆘 Support",
        "about": "ℹ️ About", "back": "🔙 Back", "seller": "👤 I am seller",
        "buyer": "🛒 I am buyer", "choose_role": "Choose your role:",
        "choose_type": "Choose deal type:", "description": "📝 Enter deal description:",
        "currency": "💱 Choose currency:", "amount": "💰 Enter integer amount:",
        "requisites": "💳 Enter receiving requisites:",
        "seller_username": "👤 Enter seller @username:",
        "deposit": "➕ Deposit", "withdraw": "➖ Withdraw",
        "deposit_amount": "Enter virtual deposit amount:",
        "withdraw_amount": "Enter virtual withdrawal amount:",
        "positive": "❌ Amount must be positive.", "not_enough": "❌ Not enough virtual funds.",
        "my_deals_empty": "📭 You have no deals.", "profile_text": "👤 <b>Profile</b>\n\nID: <code>{id}</code>\nUsername: @{username}\nDeals: {deals}\nSuccessful: {successful}\nRating: {rating} ({reviews})\nReferrals: {refs}\n",
        "language_text": "🌐 Choose language:", "language_set": "✅ Language: {lang}",
        "support_text": "🆘 Support: @GiftsforFunpay\n\nThis demo uses virtual operations only.",
        "about_text": "ℹ️ Educational P2P deal demonstration. No real payments.",
        "invalid": "❌ Invalid value.",
        "not_found": "❌ Not found.",
        "admin_only": "🚫 Admins only.",
        "cancelled": "❌ Cancelled.",
        "cancelled_fsm": "✅ Current action cancelled.",
        "banned": "🚫 Your account is banned.",
        "deal_created": "✅ Deal <b>#{deal_id}</b> created.\n\n🔗 Link for counterparty:\n{link}\n\nStatus: waiting for second participant.",
        "deal_created_buyer": "✅ Deal <b>#{deal_id}</b> created.\n\nWaiting for seller.\n🔗 Link:\n{link}",
        "joined": "✅ You joined deal #{deal_id}.",
        "already_member": "ℹ️ You are already a participant.",
        "full": "ℹ️ Both roles are already taken.",
        "self_deal": "❌ You cannot take the second role in your own deal.",
        "confirm": "✅ Confirm participation",
        "cancel_deal": "❌ Cancel deal",
        "details": "🔎 Details",
        "confirmed": "✅ You confirmed participation. Waiting for buyer payment.",
        "buyer_notify": "📩 Seller confirmed participation in deal #{deal_id}.\n\n💰 {amount} {currency}\n💳 Seller requisites:\n{req}\n\nThis is a demo operation.",
        "deal_active": "🟢 Active",
        "waiting_buyer": "🟡 Waiting for buyer",
        "waiting_seller": "🟡 Waiting for seller",
        "completed": "✅ Completed",
        "cancelled_status": "❌ Cancelled",
        "balance": "💰 <b>Balance</b>\n\nAvailable: <b>{balance}</b>\nFrozen: <b>{frozen}</b>\nService balance: virtual\n",
        "deposit": "➕ Deposit",
        "withdraw": "➖ Withdraw",
        "deposit_ok": "✅ Virtual balance increased by {amount}.",
        "withdraw_ok": "✅ Virtual withdrawal of {amount}.",
        "not_enough": "❌ Not enough virtual funds.",
        "my_deals_empty": "📭 You have no deals.",
        "my_deals_title": "📋 <b>My deals</b>\n\n",
        "profile_text": "👤 <b>Profile</b>\n\nID: <code>{id}</code>\nUsername: @{username}\nDeals: {deals}\nSuccessful: {successful}\nRating: {rating} ({reviews})\nReferrals: {refs}\n",
        "news_empty": "📢 No news yet.",
        "language_text": "🌐 Choose language:",
        "language_set": "✅ Language set: {lang}",
        "req_menu": "💳 Choose a requisites type to change:",
        "card_prompt": "Enter card number (demo):",
        "crypto_prompt": "Enter crypto wallet address (demo):",
        "stars_prompt": "Enter @username for Stars (demo):",
        "req_saved": "✅ Requisites saved.",
        "gifts_empty": "🎁 No saved gifts.",
        "gift_add": "➕ Add gift",
        "gift_link_prompt": "Enter gift link:",
        "gift_desc_prompt": "Enter gift description:",
        "gift_saved": "✅ Gift saved.",
        "active_limit": "❌ Maximum 5 active deals allowed.",
        "seller_not_found": "❌ User with this username not found.",
        "review_prompt": "⭐ Rate the counterparty from 1 to 5:",
        "review_comment": "Write a short comment or '-'",
        "review_saved": "✅ Review saved. Thanks!",
        "admin_deals": "🛠 Manage deals",
        "admin_done": "✅ Complete",
        "admin_cancel": "❌ Cancel",
        "admin_req": "💳 Change requisites",
        "admin_req_prompt": "Enter new seller requisites:",
        "admin_done_ok": "✅ Deal #{deal_id} completed by admin.",
        "admin_cancel_ok": "❌ Deal #{deal_id} cancelled by admin.",
        "admin_req_ok": "✅ Requisites of deal #{deal_id} changed.",
        "ban_ok": "🚫 User {id} banned.",
        "unban_ok": "✅ User {id} unbanned.",
        "stats": "📊 <b>Statistics</b>\n\nUsers: {users}\nActive: {active}\nCompleted: {completed}\nCancelled: {cancelled}\nTotal deals: {total}\nAdmin logs: {logs}\nService balance: {service}\n",
    },
    "uk": {
        "main": "🛡️ <b>FUNPAY OTC — ДЕМО</b>\n\nДемонстраційний сервіс угод у Telegram.\nУсі баланси та операції віртуальні.\n\nОберіть дію:",
        "create": "📝 Створити угоду",
        "funds": "💰 Баланс",
        "my_deals": "📋 Мої угоди",
        "req": "💳 Реквізити",
        "gifts": "🎁 Мої подарунки",
        "profile": "👤 Профіль",
        "news": "📢 Новини",
        "language": "🌐 Мова",
        "support": "🆘 Підтримка",
        "about": "ℹ️ Про сервіс",
        "back": "🔙 Назад",
        "seller": "👤 Я продавець",
        "buyer": "🛒 Я покупець",
        "account": "📦 Товар / акаунт",
        "gift": "🎁 NFT Gift",
        "choose_role": "Оберіть вашу роль:",
        "choose_type": "Оберіть тип угоди:",
        "description": "📝 Введіть опис угоди:",
        "currency": "💱 Оберіть валюту:",
        "amount": "💰 Введіть суму цілим числом:",
        "requisites": "💳 Введіть реквізити для отримання оплати:",
        "seller_username": "👤 Введіть @username продавця:",
        "invalid": "❌ Некоректне значення.",
        "not_found": "❌ Не знайдено.",
        "admin_only": "🚫 Доступ дозволено лише адміністраторам.",
        "cancelled": "❌ Скасовано.",
        "language_text": "🌐 Оберіть мову:",
        "saved": "✅ Збережено.",
        "success": "✅ Успішно виконано.",
        "no_deals": "📋 У вас поки немає угод.",
        "no_gifts": "🎁 У вас немає збережених подарунків.",
        "add_gift": "➕ Додати подарунок",
        "balance": "💰 Баланс",
        "deposit": "➕ Поповнити",
        "withdraw": "➖ Вивести",
        "balance_text": "💰 Ваш баланс: <b>{balance}</b>",
        "profile_text": "👤 <b>Профіль</b>\n\nID: <code>{user_id}</code>\nUsername: @{username}\nРейтинг: ⭐ {rating}\nВідгуків: {reviews}",
        "deal_created": "✅ Угоду <b>#{deal_id}</b> створено.\n\n🔗 Посилання для контрагента:\n{link}\n\nСтатус: очікується другий учасник.",
        "deal_created_buyer": "✅ Угоду <b>#{deal_id}</b> створено.\n\nОчікується підключення продавця.\n🔗 Посилання:\n{link}",
        "waiting": "⏳ Очікування другого учасника.",
        "joined": "✅ Ви приєдналися до угоди.",
        "already_member": "ℹ️ Ви вже є учасником цієї угоди.",
        "deal_full": "❌ Угода вже має двох учасників.",
        "cannot_join": "❌ Ви не можете приєднатися до цієї угоди.",
        "confirm": "✅ Підтвердити участь",
        "confirm_text": "Підтвердіть участь в угоді.",
        "confirmed": "✅ Участь підтверджено.",
        "buyer_notify": "✅ Продавець підтвердив участь.\n\n💳 Реквізити продавця:\n{req}\n\nОчікуйте оплату.",
        "seller_notify": "✅ Ви підтвердили участь.\n\nОчікуйте оплату від покупця.",
        "cancel_deal": "❌ Скасувати угоду",
        "deal_cancelled": "❌ Угоду #{deal_id} скасовано.",
        "deal_details": "📋 <b>Угода #{deal_id}</b>\n\nТип: {deal_type}\nОпис: {description}\nСума: {amount} {currency}\nПродавець: @{seller}\nПокупець: @{buyer}\nСтатус: {status}",
        "active": "активна",
        "completed": "завершена",
        "cancelled_status": "скасована",
        "waiting_buyer": "очікування покупця",
        "waiting_seller": "очікування продавця",
        "complete": "Завершити",
        "admin_done_ok": "✅ Угоду #{deal_id} завершено.",
        "admin_cancel_ok": "❌ Угоду #{deal_id} скасовано адміністратором.",
        "admin_req_prompt": "Введіть нові реквізити продавця:",
        "admin_req_ok": "✅ Реквізити угоди #{deal_id} змінено.",
        "review_prompt": "⭐ Залишити відгук",
        "review_saved": "✅ Відгук збережено.",
        "review_choose": "Оцініть користувача від 1 до 5:",
        "news_empty": "📢 Новин поки немає.",
        "stats": "📊 <b>Статистика</b>\n\nКористувачів: {users}\nУгод: {deals}\nАктивних: {active}\nЗавершених: {completed}\nСкасованих: {cancelled}",
        "banned": "🚫 Ваш акаунт заблоковано.",
        "unbanned": "✅ Користувача розблоковано.",
        "already_banned": "ℹ️ Користувач уже заблокований.",
        "not_banned": "ℹ️ Користувач не заблокований.",
        "cancel_fsm": "✅ Поточну операцію скасовано.",
    },
    "kk": {
        "main": "🛡️ <b>FUNPAY OTC — ДЕМО</b>\n\nTelegram-дегі мәмілелердің демонстрациялық сервисі.\nБарлық баланс пен операциялар виртуалды.\n\nӘрекетті таңдаңыз:",
        "create": "📝 Мәміле жасау",
        "funds": "💰 Баланс",
        "my_deals": "📋 Менің мәмілелерім",
        "req": "💳 Реквизиттер",
        "gifts": "🎁 Менің сыйлықтарым",
        "profile": "👤 Профиль",
        "news": "📢 Жаңалықтар",
        "language": "🌐 Тіл",
        "support": "🆘 Қолдау",
        "about": "ℹ️ Сервис туралы",
        "back": "🔙 Артқа",
        "seller": "👤 Мен сатушымын",
        "buyer": "🛒 Мен сатып алушымын",
        "account": "📦 Тауар / аккаунт",
        "gift": "🎁 NFT Gift",
        "choose_role": "Рөліңізді таңдаңыз:",
        "choose_type": "Мәміле түрін таңдаңыз:",
        "description": "📝 Мәміле сипаттамасын енгізіңіз:",
        "currency": "💱 Валютаны таңдаңыз:",
        "amount": "💰 Соманы бүтін санмен енгізіңіз:",
        "requisites": "💳 Төлемді алу реквизиттерін енгізіңіз:",
        "seller_username": "👤 Сатушының @username енгізіңіз:",
        "invalid": "❌ Қате мән.",
        "not_found": "❌ Табылмады.",
        "admin_only": "🚫 Бұл бөлімге тек әкімшілер кіре алады.",
        "cancelled": "❌ Бас тартылды.",
        "language_text": "🌐 Тілді таңдаңыз:",
        "saved": "✅ Сақталды.",
        "success": "✅ Сәтті орындалды.",
        "no_deals": "📋 Сізде әзірге мәмілелер жоқ.",
        "no_gifts": "🎁 Сізде сақталған сыйлықтар жоқ.",
        "add_gift": "➕ Сыйлық қосу",
        "balance": "💰 Баланс",
        "deposit": "➕ Толықтыру",
        "withdraw": "➖ Шығару",
        "balance_text": "💰 Балансыңыз: <b>{balance}</b>",
        "profile_text": "👤 <b>Профиль</b>\n\nID: <code>{user_id}</code>\nUsername: @{username}\nРейтинг: ⭐ {rating}\nПікірлер: {reviews}",
        "deal_created": "✅ <b>#{deal_id}</b> мәмілесі жасалды.\n\n🔗 Контрагентке арналған сілтеме:\n{link}\n\nКүйі: екінші қатысушы күтілуде.",
        "deal_created_buyer": "✅ <b>#{deal_id}</b> мәмілесі жасалды.\n\nСатушының қосылуы күтілуде.\n🔗 Сілтеме:\n{link}",
        "waiting": "⏳ Екінші қатысушы күтілуде.",
        "joined": "✅ Сіз мәмілеге қосылдыңыз.",
        "already_member": "ℹ️ Сіз бұл мәміленің қатысушысыз.",
        "deal_full": "❌ Мәміледе екі қатысушы да бар.",
        "cannot_join": "❌ Сіз бұл мәмілеге қосыла алмайсыз.",
        "confirm": "✅ Қатысуды растау",
        "confirm_text": "Мәмілеге қатысуды растаңыз.",
        "confirmed": "✅ Қатысу расталды.",
        "buyer_notify": "✅ Сатушы қатысуын растады.\n\n💳 Сатушы реквизиттері:\n{req}\n\nТөлемді күтіңіз.",
        "seller_notify": "✅ Қатысуыңызды растадыңыз.\n\nСатып алушыдан төлемді күтіңіз.",
        "cancel_deal": "❌ Мәмілені тоқтату",
        "deal_cancelled": "❌ #{deal_id} мәмілесі тоқтатылды.",
        "deal_details": "📋 <b>Мәміле #{deal_id}</b>\n\nТүрі: {deal_type}\nСипаттамасы: {description}\nСома: {amount} {currency}\nСатушы: @{seller}\nСатып алушы: @{buyer}\nКүйі: {status}",
        "active": "белсенді",
        "completed": "аяқталған",
        "cancelled_status": "тоқтатылған",
        "waiting_buyer": "сатып алушы күтілуде",
        "waiting_seller": "сатушы күтілуде",
        "complete": "Аяқтау",
        "admin_done_ok": "✅ #{deal_id} мәмілесі аяқталды.",
        "admin_cancel_ok": "❌ #{deal_id} мәмілесін әкімші тоқтатты.",
        "admin_req_prompt": "Сатушының жаңа реквизиттерін енгізіңіз:",
        "admin_req_ok": "✅ #{deal_id} мәмілесінің реквизиттері өзгертілді.",
        "review_prompt": "⭐ Пікір қалдыру",
        "review_saved": "✅ Пікір сақталды.",
        "review_choose": "1-ден 5-ке дейін баға беріңіз:",
        "news_empty": "📢 Әзірге жаңалықтар жоқ.",
        "stats": "📊 <b>Статистика</b>\n\nПайдаланушылар: {users}\nМәмілелер: {deals}\nБелсенді: {active}\nАяқталған: {completed}\nТоқтатылған: {cancelled}",
        "banned": "🚫 Сіздің аккаунтыңыз бұғатталған.",
        "unbanned": "✅ Пайдаланушы бұғаттан шығарылды.",
        "already_banned": "ℹ️ Пайдаланушы бұрыннан бұғатталған.",
        "not_banned": "ℹ️ Пайдаланушы бұғатталмаған.",
        "cancel_fsm": "✅ Ағымдағы операция тоқтатылды.",
    },
    "zh": {
        "main": "🛡️ <b>FUNPAY OTC — 演示版</b>\n\nTelegram 交易演示服务。\n所有余额和操作均为虚拟数据。\n\n请选择操作：",
        "create": "📝 创建交易",
        "funds": "💰 余额",
        "my_deals": "📋 我的交易",
        "req": "💳 收款信息",
        "gifts": "🎁 我的礼物",
        "profile": "👤 个人资料",
        "news": "📢 新闻",
        "language": "🌐 语言",
        "support": "🆘 客服",
        "about": "ℹ️ 关于服务",
        "back": "🔙 返回",
        "seller": "👤 我是卖家",
        "buyer": "🛒 我是买家",
        "account": "📦 商品 / 账号",
        "gift": "🎁 NFT Gift",
        "choose_role": "请选择您的角色：",
        "choose_type": "请选择交易类型：",
        "description": "📝 输入交易描述：",
        "currency": "💱 选择货币：",
        "amount": "💰 输入整数金额：",
        "requisites": "💳 输入收款信息：",
        "seller_username": "👤 输入卖家的 @username：",
        "invalid": "❌ 输入无效。",
        "not_found": "❌ 未找到。",
        "admin_only": "🚫 只有管理员可以访问此功能。",
        "cancelled": "❌ 已取消。",
        "language_text": "🌐 请选择语言：",
        "saved": "✅ 已保存。",
        "success": "✅ 操作成功。",
        "no_deals": "📋 您目前没有交易。",
        "no_gifts": "🎁 您没有保存的礼物。",
        "add_gift": "➕ 添加礼物",
        "balance": "💰 余额",
        "deposit": "➕ 充值",
        "withdraw": "➖ 提现",
        "balance_text": "💰 您的余额：<b>{balance}</b>",
        "profile_text": "👤 <b>个人资料</b>\n\nID：<code>{user_id}</code>\n用户名：@{username}\n评分：⭐ {rating}\n评价数量：{reviews}",
        "deal_created": "✅ 交易 <b>#{deal_id}</b> 已创建。\n\n🔗 给交易对方的链接：\n{link}\n\n状态：等待另一位参与者。",
        "deal_created_buyer": "✅ 交易 <b>#{deal_id}</b> 已创建。\n\n等待卖家加入。\n🔗 链接：\n{link}",
        "waiting": "⏳ 等待另一位参与者。",
        "joined": "✅ 您已加入交易。",
        "already_member": "ℹ️ 您已经是该交易的参与者。",
        "deal_full": "❌ 该交易已经有两名参与者。",
        "cannot_join": "❌ 您无法加入该交易。",
        "confirm": "✅ 确认参与",
        "confirm_text": "请确认参与此次交易。",
        "confirmed": "✅ 参与已确认。",
        "buyer_notify": "✅ 卖家已确认参与。\n\n💳 卖家收款信息：\n{req}\n\n请等待付款。",
        "seller_notify": "✅ 您已确认参与。\n\n请等待买家付款。",
        "cancel_deal": "❌ 取消交易",
        "deal_cancelled": "❌ 交易 #{deal_id} 已取消。",
        "deal_details": "📋 <b>交易 #{deal_id}</b>\n\n类型：{deal_type}\n描述：{description}\n金额：{amount} {currency}\n卖家：@{seller}\n买家：@{buyer}\n状态：{status}",
        "active": "进行中",
        "completed": "已完成",
        "cancelled_status": "已取消",
        "waiting_buyer": "等待买家",
        "waiting_seller": "等待卖家",
        "complete": "完成交易",
        "admin_done_ok": "✅ 交易 #{deal_id} 已完成。",
        "admin_cancel_ok": "❌ 管理员已取消交易 #{deal_id}。",
        "admin_req_prompt": "请输入新的卖家收款信息：",
        "admin_req_ok": "✅ 交易 #{deal_id} 的收款信息已修改。",
        "review_prompt": "⭐ 留下评价",
        "review_saved": "✅ 评价已保存。",
        "review_choose": "请给用户评分（1-5）：",
        "news_empty": "📢 暂无新闻。",
        "stats": "📊 <b>统计</b>\n\n用户：{users}\n交易：{deals}\n进行中：{active}\n已完成：{completed}\n已取消：{cancelled}",
        "banned": "🚫 您的账号已被封禁。",
        "unbanned": "✅ 用户已解除封禁。",
        "already_banned": "ℹ️ 用户已经被封禁。",
        "not_banned": "ℹ️ 用户没有被封禁。",
        "cancel_fsm": "✅ 当前操作已取消。",
    },
    "hi": {
        "main": "🛡️ <b>FUNPAY OTC — डेमो</b>\n\nTelegram पर लेन-देन की डेमो सेवा।\nसभी बैलेंस और ऑपरेशन वर्चुअल हैं।\n\nकृपया कार्रवाई चुनें:",
        "create": "📝 डील बनाएं",
        "funds": "💰 बैलेंस",
        "my_deals": "📋 मेरी डील्स",
        "req": "💳 भुगतान विवरण",
        "gifts": "🎁 मेरे गिफ्ट",
        "profile": "👤 प्रोफ़ाइल",
        "news": "📢 समाचार",
        "language": "🌐 भाषा",
        "support": "🆘 सहायता",
        "about": "ℹ️ सेवा के बारे में",
        "back": "🔙 वापस",
        "seller": "👤 मैं विक्रेता हूँ",
        "buyer": "🛒 मैं खरीदार हूँ",
        "account": "📦 वस्तु / अकाउंट",
        "gift": "🎁 NFT Gift",
        "choose_role": "अपनी भूमिका चुनें:",
        "choose_type": "डील का प्रकार चुनें:",
        "description": "📝 डील का विवरण दर्ज करें:",
        "currency": "💱 मुद्रा चुनें:",
        "amount": "💰 पूरी संख्या में राशि दर्ज करें:",
        "requisites": "💳 भुगतान प्राप्त करने का विवरण दर्ज करें:",
        "seller_username": "👤 विक्रेता का @username दर्ज करें:",
        "invalid": "❌ अमान्य मान।",
        "not_found": "❌ नहीं मिला।",
        "admin_only": "🚫 यह सुविधा केवल एडमिन के लिए है।",
        "cancelled": "❌ रद्द किया गया।",
        "language_text": "🌐 भाषा चुनें:",
        "saved": "✅ सेव किया गया।",
        "success": "✅ सफलतापूर्वक पूरा हुआ।",
        "no_deals": "📋 आपके पास अभी कोई डील नहीं है।",
        "no_gifts": "🎁 आपके पास कोई सेव किया हुआ गिफ्ट नहीं है।",
        "add_gift": "➕ गिफ्ट जोड़ें",
        "balance": "💰 बैलेंस",
        "deposit": "➕ जमा करें",
        "withdraw": "➖ निकालें",
        "balance_text": "💰 आपका बैलेंस: <b>{balance}</b>",
        "profile_text": "👤 <b>प्रोफ़ाइल</b>\n\nID: <code>{user_id}</code>\nUsername: @{username}\nरेटिंग: ⭐ {rating}\nरिव्यू: {reviews}",
        "deal_created": "✅ डील <b>#{deal_id}</b> बनाई गई है।\n\n🔗 दूसरे पक्ष के लिए लिंक:\n{link}\n\nस्थिति: दूसरे प्रतिभागी की प्रतीक्षा।",
        "deal_created_buyer": "✅ डील <b>#{deal_id}</b> बनाई गई है।\n\nविक्रेता के जुड़ने की प्रतीक्षा है।\n🔗 लिंक:\n{link}",
        "waiting": "⏳ दूसरे प्रतिभागी की प्रतीक्षा है।",
        "joined": "✅ आप डील में शामिल हो गए हैं।",
        "already_member": "ℹ️ आप पहले से इस डील के प्रतिभागी हैं।",
        "deal_full": "❌ इस डील में दोनों प्रतिभागी पहले से मौजूद हैं।",
        "cannot_join": "❌ आप इस डील में शामिल नहीं हो सकते।",
        "confirm": "✅ भागीदारी की पुष्टि करें",
        "confirm_text": "डील में अपनी भागीदारी की पुष्टि करें।",
        "confirmed": "✅ भागीदारी की पुष्टि हो गई।",
        "buyer_notify": "✅ विक्रेता ने भागीदारी की पुष्टि कर दी है।\n\n💳 विक्रेता के भुगतान विवरण:\n{req}\n\nभुगतान की प्रतीक्षा करें।",
        "seller_notify": "✅ आपने भागीदारी की पुष्टि कर दी है।\n\nखरीदार के भुगतान की प्रतीक्षा करें।",
        "cancel_deal": "❌ डील रद्द करें",
        "deal_cancelled": "❌ डील #{deal_id} रद्द कर दी गई।",
        "deal_details": "📋 <b>डील #{deal_id}</b>\n\nप्रकार: {deal_type}\nविवरण: {description}\nराशि: {amount} {currency}\nविक्रेता: @{seller}\nखरीदार: @{buyer}\nस्थिति: {status}",
        "active": "सक्रिय",
        "completed": "पूरी हुई",
        "cancelled_status": "रद्द",
        "waiting_buyer": "खरीदार की प्रतीक्षा",
        "waiting_seller": "विक्रेता की प्रतीक्षा",
        "complete": "पूरा करें",
        "admin_done_ok": "✅ डील #{deal_id} पूरी हो गई।",
        "admin_cancel_ok": "❌ एडमिन ने डील #{deal_id} रद्द कर दी।",
        "admin_req_prompt": "विक्रेता के नए भुगतान विवरण दर्ज करें:",
        "admin_req_ok": "✅ डील #{deal_id} के भुगतान विवरण बदल दिए गए।",
        "review_prompt": "⭐ रिव्यू दें",
        "review_saved": "✅ रिव्यू सेव हो गया।",
        "review_choose": "यूज़र को 1 से 5 तक रेटिंग दें:",
        "news_empty": "📢 अभी कोई समाचार नहीं है।",
        "stats": "📊 <b>आंकड़े</b>\n\nयूज़र: {users}\nडील्स: {deals}\nसक्रिय: {active}\nपूरी हुई: {completed}\nरद्द: {cancelled}",
        "banned": "🚫 आपका अकाउंट ब्लॉक कर दिया गया है।",
        "unbanned": "✅ यूज़र को अनब्लॉक कर दिया गया।",
        "already_banned": "ℹ️ यूज़र पहले से ब्लॉक है।",
        "not_banned": "ℹ️ यूज़र ब्लॉक नहीं है।",
        "cancel_fsm": "✅ वर्तमान ऑपरेशन रद्द कर दिया गया।",
    },
}


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
    execute(
        """
        INSERT INTO users(user_id, username, created_at)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
        """,
        (user.id, username, now),
    )


def is_banned(user_id):
    row = fetchone("SELECT banned FROM users WHERE user_id=?", (user_id,))
    return bool(row and row["banned"])


def is_admin(user_id):
    return user_id in ADMIN_IDS


def admin_log(admin_id, action, details=""):
    execute(
        "INSERT INTO admin_logs(admin_id, action, details, created_at) VALUES(?,?,?,?)",
        (admin_id, action, details, datetime.now(timezone.utc).isoformat()),
    )


def active_count(user_id):
    row = fetchone(
        """
        SELECT COUNT(*) AS c FROM deals
        WHERE (seller_id=? OR buyer_id=?)
          AND status NOT IN ('completed','cancelled')
        """,
        (user_id, user_id),
    )
    return int(row["c"]) if row else 0


def status_text(status, lang):
    return {
        "active": tr("deal_active", lang),
        "waiting_buyer": tr("waiting_buyer", lang),
        "waiting_seller": tr("waiting_seller", lang),
        "completed": tr("completed", lang),
        "cancelled": tr("cancelled_status", lang),
    }.get(status, status)


# ============================================================
# KEYBOARDS
# ============================================================

def kb_main(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("create", lang), callback_data="create_deal")],
        [InlineKeyboardButton(text=tr("funds", lang), callback_data="funds"),
         InlineKeyboardButton(text=tr("my_deals", lang), callback_data="my_deals")],
        [InlineKeyboardButton(text=tr("req", lang), callback_data="requisites"),
         InlineKeyboardButton(text=tr("gifts", lang), callback_data="gifts")],
        [InlineKeyboardButton(text=tr("profile", lang), callback_data="profile"),
         InlineKeyboardButton(text=tr("news", lang), callback_data="news")],
        [InlineKeyboardButton(text=tr("language", lang), callback_data="lang"),
         InlineKeyboardButton(text=tr("support", lang), callback_data="support")],
        [InlineKeyboardButton(text=tr("about", lang), callback_data="about")],
    ])


def kb_back(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ])


def kb_roles(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("seller", lang), callback_data="role_seller"),
         InlineKeyboardButton(text=tr("buyer", lang), callback_data="role_buyer")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ])


def kb_types(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("account", lang), callback_data="type_account"),
         InlineKeyboardButton(text=tr("gift", lang), callback_data="type_gift")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ])


def kb_currencies(lang, prefix):
    labels = [
        ("RUB", "🇷🇺 RUB"), ("UAH", "🇺🇦 UAH"), ("BYN", "🇧🇾 BYN"),
        ("USDT", "💎 USDT"), ("TON", "💎 TON"), ("STARS", "⭐ Stars"),
    ]
    rows = []
    for i in range(0, len(labels), 2):
        rows.append([
            InlineKeyboardButton(text=labels[i][1], callback_data=f"{prefix}{labels[i][0]}"),
            InlineKeyboardButton(text=labels[i+1][1], callback_data=f"{prefix}{labels[i+1][0]}"),
        ])
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_balance(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("deposit", lang), callback_data="deposit")],
        [InlineKeyboardButton(text=tr("withdraw", lang), callback_data="withdraw")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
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

    gift_link = State()
    gift_description = State()

    review_rating = State()
    review_comment = State()

    admin_news = State()
    admin_req = State()


# ============================================================
# UTILITIES
# ============================================================

async def safe_send(chat_id, text, markup=None):
    try:
        if PHOTO_URL:
            try:
                await bot.send_photo(chat_id, PHOTO_URL)
            except Exception:
                pass
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
    await safe_send(chat_id, tr("main", lang), kb_main(lang))


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
# START / DEEP LINKS
# ============================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user)
    uid = message.from_user.id
    username = message.from_user.username or ""
    lang = user_lang(uid)

    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        param = args[1].strip()

        if param.startswith("ref"):
            ref = param[3:]
            if ref.isdigit() and int(ref) != uid:
                ref_id = int(ref)
                if fetchone("SELECT user_id FROM users WHERE user_id=?", (ref_id,)):
                    execute(
                        "INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)",
                        (ref_id, uid, datetime.now(timezone.utc).isoformat()),
                    )
                    execute(
                        "UPDATE users SET ref_count=(SELECT COUNT(*) FROM referrals WHERE referrer_id=?) WHERE user_id=?",
                        (ref_id, ref_id),
                    )

        elif param.startswith("deal_"):
            await join_deal(message, state, param[5:])
            return

    await show_main(message.chat.id, uid)


async def join_deal(message, state, deal_id):
    uid = message.from_user.id
    username = message.from_user.username or ""

    if is_banned(uid):
        await message.answer(tr("banned", user_lang(uid)))
        return

    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await message.answer(tr("not_found", user_lang(uid)))
        return

    if deal["status"] in ("completed", "cancelled"):
        await message.answer(tr("not_allowed", user_lang(uid)))
        return

    if uid in (deal["seller_id"], deal["buyer_id"]):
        await message.answer(tr("already_member", user_lang(uid)))
        return

    if deal["seller_id"] is None:
        role = "seller"
    elif deal["buyer_id"] is None:
        role = "buyer"
    else:
        await message.answer(tr("full", user_lang(uid)))
        return

    if active_count(uid) >= MAX_ACTIVE_DEALS:
        await message.answer(tr("active_limit", user_lang(uid)))
        return

    if role == "seller":
        execute(
            "UPDATE deals SET seller_id=?,seller_username=? WHERE deal_id=?",
            (uid, username, deal_id),
        )
    else:
        execute(
            "UPDATE deals SET buyer_id=?,buyer_username=? WHERE deal_id=?",
            (uid, username, deal_id),
        )

    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    now = datetime.now(timezone.utc).isoformat()

    if deal["seller_id"] and deal["buyer_id"]:
        execute(
            "UPDATE deals SET status='active' WHERE deal_id=?",
            (deal_id,),
        )
        status = "active"
    else:
        status = deal["status"]

    lang = user_lang(uid)
    await message.answer(tr("joined", lang).format(deal_id=deal_id))

    other_id = deal["buyer_id"] if role == "seller" else deal["seller_id"]
    if other_id:
        other_lang = user_lang(other_id)
        await notify(
            other_id,
            f"👤 @{username or uid} подключился к сделке #{deal_id}.\n"
            f"Статус: {status_text(status, other_lang)}"
        )

    if status == "active":
        fresh = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
        seller = fresh["seller_id"]
        buyer = fresh["buyer_id"]
        seller_lang = user_lang(seller)
        buyer_lang = user_lang(buyer)
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=tr("confirm", seller_lang),
                callback_data=f"confirm_{deal_id}"
            )],
            [InlineKeyboardButton(
                text=tr("cancel_deal", seller_lang),
                callback_data=f"cancel_{deal_id}"
            )]
        ])
        await notify(
            seller,
            f"👥 Оба участника подключены к сделке #{deal_id}.\n"
            f"💰 {fresh['amount']} {fresh['currency']}\n"
            f"📝 {fresh['description']}",
            confirm_kb
        )
        await notify(
            buyer,
            f"👥 Оба участника подключены к сделке #{deal_id}.\n"
            f"Ожидается подтверждение продавца."
        )
    else:
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=tr("cancel_deal", lang),
                callback_data=f"cancel_{deal_id}"
            )],
            [InlineKeyboardButton(
                text=tr("back", lang), callback_data="main_menu"
            )]
        ])
        await message.answer(
            f"📌 #{deal_id}\nСтатус: {status_text(status, lang)}",
            reply_markup=cancel_kb
        )


# ============================================================
# MAIN MENU
# ============================================================

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


# ============================================================
# SELLER CREATION
# ============================================================

@dp.callback_query(F.data == "role_seller")
async def seller_role(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.seller_type)
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("choose_type", lang), reply_markup=kb_types(lang))
    await call.answer()


@dp.callback_query(F.data.startswith("type_"), States.seller_type)
async def seller_type(call: CallbackQuery, state: FSMContext):
    await state.update_data(deal_type=call.data.replace("type_", ""))
    await state.set_state(States.seller_description)
    await call.message.answer(tr("description", user_lang(call.from_user.id)))
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
    await message.answer(
        tr("currency", user_lang(message.from_user.id)),
        reply_markup=kb_currencies(user_lang(message.from_user.id), "sellcurr_")
    )


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

    execute(
        """
        INSERT INTO deals(
            deal_id,seller_id,deal_type,description,amount,currency,
            seller_req,status,seller_username,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            deal_id, uid, data["deal_type"], data["description"],
            data["amount"], data["currency"], req,
            "waiting_buyer", username, now
        )
    )
    execute(
        "UPDATE users SET deals_count=deals_count+1 WHERE user_id=?",
        (uid,)
    )
    await state.clear()

    lang = user_lang(uid)
    await message.answer(
        tr("deal_created", lang).format(
            deal_id=deal_id, link=deal_link(deal_id)
        )
    )


# ============================================================
# BUYER CREATION
# ============================================================

@dp.callback_query(F.data == "role_buyer")
async def buyer_role(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.buyer_type)
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("choose_type", lang), reply_markup=kb_types(lang))
    await call.answer()


@dp.callback_query(F.data.startswith("type_"), States.buyer_type)
async def buyer_type(call: CallbackQuery, state: FSMContext):
    await state.update_data(deal_type=call.data.replace("type_", ""))
    await state.set_state(States.buyer_description)
    await call.message.answer(tr("description", user_lang(call.from_user.id)))
    await call.answer()


@dp.message(States.buyer_description)
async def buyer_description(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("❌ Описание слишком короткое.")
        return
    await state.update_data(description=text)
    await state.set_state(States.buyer_currency)
    await message.answer(
        tr("currency", user_lang(message.from_user.id)),
        reply_markup=kb_currencies(user_lang(message.from_user.id), "buycurr_")
    )


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

    seller = fetchone(
        "SELECT user_id, username FROM users WHERE lower(username)=lower(?)",
        (username,)
    )
    if not seller:
        await message.answer(tr("seller_not_found", user_lang(message.from_user.id)))
        return

    uid = message.from_user.id
    if seller["user_id"] == uid:
        await message.answer(tr("self_deal", user_lang(uid)))
        return

    if active_count(uid) >= MAX_ACTIVE_DEALS:
        await message.answer(tr("active_limit", user_lang(uid)))
        return

    data = await state.get_data()
    deal_id = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc).isoformat()

    execute(
        """
        INSERT INTO deals(
            deal_id,seller_id,buyer_id,deal_type,description,amount,currency,
            status,seller_username,buyer_username,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            deal_id, seller["user_id"], uid, data["deal_type"],
            data["description"], data["amount"], data["currency"],
            "active", seller["username"], message.from_user.username or "", now
        )
    )
    execute(
        "UPDATE users SET deals_count=deals_count+1 WHERE user_id=?",
        (uid,)
    )
    await state.clear()

    lang = user_lang(uid)
    await message.answer(
        tr("deal_created_buyer", lang).format(
            deal_id=deal_id, link=deal_link(deal_id)
        )
    )
    seller_lang = user_lang(seller["user_id"])
    await notify(
        seller["user_id"],
        f"📦 Пользователь @{message.from_user.username or uid} создал "
        f"сделку #{deal_id} и указал вас продавцом.\n"
        f"🔗 {deal_link(deal_id)}\n"
        f"Откройте ссылку для подтверждения роли."
    )


# ============================================================
# DEAL CONFIRM / CANCEL / DETAILS
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
    execute(
        "UPDATE deals SET confirmed_at=? WHERE deal_id=?",
        (now, deal_id)
    )

    seller_lang = user_lang(uid)
    await call.message.edit_text(
        tr("confirmed", seller_lang),
        parse_mode="HTML"
    )

    buyer_lang = user_lang(deal["buyer_id"])
    await notify(
        deal["buyer_id"],
        tr("buyer_notify", buyer_lang).format(
            deal_id=deal_id,
            amount=deal["amount"],
            currency=deal["currency"],
            req=deal["seller_req"] or "не указаны"
        )
    )
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

    execute(
        "UPDATE deals SET status='cancelled' WHERE deal_id=?",
        (deal_id,)
    )
    lang = user_lang(uid)
    await call.message.answer(
        tr("cancelled", lang).format(deal_id=deal_id),
        reply_markup=kb_back(lang)
    )

    other = deal["buyer_id"] if uid == deal["seller_id"] else deal["seller_id"]
    if other:
        await notify(
            other,
            tr("cancelled", user_lang(other)).format(deal_id=deal_id)
        )
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

    text = (
        f"📌 <b>Сделка #{deal_id}</b>\n\n"
        f"Тип: {deal['deal_type']}\n"
        f"Описание: {deal['description']}\n"
        f"Сумма: {deal['amount']} {deal['currency']}\n"
        f"Продавец: @{deal['seller_username'] or '-'}\n"
        f"Покупатель: @{deal['buyer_username'] or '-'}\n"
        f"Статус: {status_text(deal['status'], lang)}\n"
    )
    if deal["seller_req"] and uid == deal["seller_id"]:
        text += f"\nРеквизиты продавца: {deal['seller_req']}"

    rows = []
    if deal["status"] in ("waiting_buyer", "waiting_seller", "waiting"):
        rows.append([
            InlineKeyboardButton(
                text=tr("cancel_deal", lang),
                callback_data=f"cancel_{deal_id}"
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=tr("back", lang), callback_data="my_deals"
        )
    ])
    await call.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await call.answer()


# ============================================================
# MY DEALS
# ============================================================

@dp.callback_query(F.data == "my_deals")
async def my_deals(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    rows = fetchall(
        """
        SELECT deal_id,deal_type,amount,currency,status
        FROM deals
        WHERE seller_id=? OR buyer_id=?
        ORDER BY created_at DESC LIMIT 30
        """,
        (uid, uid)
    )

    if not rows:
        await call.message.answer(tr("my_deals_empty", lang), reply_markup=kb_back(lang))
        await call.answer()
        return

    text = tr("my_deals_title", lang)
    buttons = []
    for d in rows:
        text += (
            f"#{d['deal_id']} | {d['deal_type']} | "
            f"{d['amount']} {d['currency']} | "
            f"{status_text(d['status'], lang)}\n"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"🔎 #{d['deal_id']}",
                callback_data=f"dealview_{d['deal_id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")
    ])
    await call.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


# ============================================================
# BALANCE
# ============================================================

@dp.callback_query(F.data == "funds")
async def funds(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    row = fetchone(
        "SELECT balance,frozen_balance FROM users WHERE user_id=?",
        (uid,)
    )
    await call.message.answer(
        tr("balance", lang).format(
            balance=row["balance"] if row else 0,
            frozen=row["frozen_balance"] if row else 0
        ),
        reply_markup=kb_balance(lang)
    )
    await call.answer()


@dp.callback_query(F.data == "deposit")
async def deposit(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.deposit)
    await call.message.answer(tr("deposit_amount", user_lang(call.from_user.id)))
    await call.answer()


@dp.message(States.deposit)
async def deposit_value(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(tr("invalid", user_lang(message.from_user.id)))
        return
    execute(
        "UPDATE users SET balance=balance+? WHERE user_id=?",
        (amount, message.from_user.id)
    )
    await state.clear()
    await message.answer(
        tr("deposit_ok", user_lang(message.from_user.id)).format(amount=amount),
        reply_markup=kb_back(user_lang(message.from_user.id))
    )


@dp.callback_query(F.data == "withdraw")
async def withdraw(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.withdraw)
    await call.message.answer(tr("withdraw_amount", user_lang(call.from_user.id)))
    await call.answer()


@dp.message(States.withdraw)
async def withdraw_value(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(tr("invalid", user_lang(message.from_user.id)))
        return
    row = fetchone(
        "SELECT balance FROM users WHERE user_id=?",
        (message.from_user.id,)
    )
    if not row or row["balance"] < amount:
        await message.answer(tr("not_enough", user_lang(message.from_user.id)))
        return

    execute(
        "UPDATE users SET balance=balance-? WHERE user_id=?",
        (amount, message.from_user.id)
    )
    await state.clear()
    await message.answer(
        tr("withdraw_ok", user_lang(message.from_user.id)).format(amount=amount),
        reply_markup=kb_back(user_lang(message.from_user.id))
    )


# ============================================================
# REQUISITES
# ============================================================

@dp.callback_query(F.data == "requisites")
async def requisites_menu(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(
        tr("req_menu", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Card", callback_data="req_card")],
            [InlineKeyboardButton(text="🪙 Crypto", callback_data="req_crypto")],
            [InlineKeyboardButton(text="⭐ Stars", callback_data="req_stars")],
            [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data.in_({"req_card", "req_crypto", "req_stars"}))
async def req_choose(call: CallbackQuery, state: FSMContext):
    typ = call.data.replace("req_", "")
    await state.update_data(req_type=typ)
    await state.set_state(States.req_input)
    lang = user_lang(call.from_user.id)
    prompts = {
        "card": tr("card_prompt", lang),
        "crypto": tr("crypto_prompt", lang),
        "stars": tr("stars_prompt", lang),
    }
    await call.message.answer(prompts[typ])
    await call.answer()


@dp.message(States.req_input)
async def req_input(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer(tr("invalid", user_lang(message.from_user.id)))
        return

    data = await state.get_data()
    typ = data.get("req_type")
    col = {"card": "card", "crypto": "crypto", "stars": "stars_username"}.get(typ)
    if not col:
        await state.clear()
        return

    execute(
        f"UPDATE users SET {col}=? WHERE user_id=?",
        (value, message.from_user.id)
    )
    await state.clear()
    await message.answer(
        tr("req_saved", user_lang(message.from_user.id)),
        reply_markup=kb_back(user_lang(message.from_user.id))
    )


# ============================================================
# GIFTS
# ============================================================

@dp.callback_query(F.data == "gifts")
async def gifts(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    rows = fetchall(
        "SELECT id,gift_link,description FROM gifts WHERE user_id=? ORDER BY id DESC",
        (uid,)
    )
    if not rows:
        text = tr("gifts_empty", lang)
    else:
        text = "🎁 <b>Мои подарки</b>\n\n"
        for row in rows:
            text += f"#{row['id']} — {row['gift_link']}\n{row['description']}\n\n"

    await call.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("gift_add", lang), callback_data="gift_add")],
            [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "gift_add")
async def gift_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(States.gift_link)
    await call.message.answer(tr("gift_link_prompt", user_lang(call.from_user.id)))
    await call.answer()


@dp.message(States.gift_link)
async def gift_link(message: Message, state: FSMContext):
    link = (message.text or "").strip()
    if not link.startswith(("http://", "https://", "tg://")):
        await message.answer("❌ Нужна ссылка.")
        return
    await state.update_data(gift_link=link)
    await state.set_state(States.gift_description)
    await message.answer(tr("gift_desc_prompt", user_lang(message.from_user.id)))


@dp.message(States.gift_description)
async def gift_description(message: Message, state: FSMContext):
    data = await state.get_data()
    execute(
        """
        INSERT INTO gifts(user_id,gift_link,description,created_at)
        VALUES(?,?,?,?)
        """,
        (
            message.from_user.id,
            data["gift_link"],
            (message.text or "").strip(),
            datetime.now(timezone.utc).isoformat()
        )
    )
    await state.clear()
    await message.answer(
        tr("gift_saved", user_lang(message.from_user.id)),
        reply_markup=kb_back(user_lang(message.from_user.id))
    )


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    row = fetchone("SELECT * FROM users WHERE user_id=?", (uid,))
    rating = row["rating"] if row else 0
    await call.message.answer(
        tr("profile_text", lang).format(
            id=uid,
            username=row["username"] if row else "",
            deals=row["deals_count"] if row else 0,
            successful=row["successful_deals"] if row else 0,
            rating=f"{rating:.2f}",
            reviews=row["reviews_count"] if row else 0,
            refs=row["ref_count"] if row else 0,
        ),
        reply_markup=kb_back(lang)
    )
    await call.answer()


# ============================================================
# LANGUAGE
# ============================================================

@dp.callback_query(F.data == "lang")
async def lang_menu(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)

    await call.message.answer(
        tr("language_text", lang),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🇷🇺 Русский",
                        callback_data="setlang_ru"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇬🇧 English",
                        callback_data="setlang_en"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇺🇦 Українська",
                        callback_data="setlang_uk"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇰🇿 Қазақша",
                        callback_data="setlang_kk"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇨🇳 中文",
                        callback_data="setlang_zh"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇮🇳 हिन्दी",
                        callback_data="setlang_hi"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=tr("back", lang),
                        callback_data="main_menu"
                    )
                ],
            ]
        )
    )
    await call.answer()


@dp.callback_query(F.data == "setlang_ru")
async def set_lang_ru(call: CallbackQuery):
    uid = call.from_user.id
    ensure_user(call.from_user)
    execute(
        "UPDATE users SET lang=? WHERE user_id=?",
        ("ru", uid)
    )
    await call.answer("Язык изменён на Русский.")
    await call.message.answer(
        tr("main", "ru"),
        reply_markup=kb_main("ru"),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "setlang_en")
async def set_lang_en(call: CallbackQuery):
    uid = call.from_user.id
    ensure_user(call.from_user)
    execute(
        "UPDATE users SET lang=? WHERE user_id=?",
        ("en", uid)
    )
    await call.answer("Language changed to English.")
    await call.message.answer(
        tr("main", "en"),
        reply_markup=kb_main("en"),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "setlang_uk")
async def set_lang_uk(call: CallbackQuery):
    uid = call.from_user.id
    ensure_user(call.from_user)
    execute(
        "UPDATE users SET lang=? WHERE user_id=?",
        ("uk", uid)
    )
    await call.answer("Мову змінено на Українську.")
    await call.message.answer(
        tr("main", "uk"),
        reply_markup=kb_main("uk"),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "setlang_kk")
async def set_lang_kk(call: CallbackQuery):
    uid = call.from_user.id
    ensure_user(call.from_user)
    execute(
        "UPDATE users SET lang=? WHERE user_id=?",
        ("kk", uid)
    )
    await call.answer("Тіл Қазақшаға өзгертілді.")
    await call.message.answer(
        tr("main", "kk"),
        reply_markup=kb_main("kk"),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "setlang_zh")
async def set_lang_zh(call: CallbackQuery):
    uid = call.from_user.id
    ensure_user(call.from_user)
    execute(
        "UPDATE users SET lang=? WHERE user_id=?",
        ("zh", uid)
    )
    await call.answer("语言已设置为中文。")
    await call.message.answer(
        tr("main", "zh"),
        reply_markup=kb_main("zh"),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "setlang_hi")
async def set_lang_hi(call: CallbackQuery):
    uid = call.from_user.id
    ensure_user(call.from_user)
    execute(
        "UPDATE users SET lang=? WHERE user_id=?",
        ("hi", uid)
    )
    await call.answer("भाषा हिन्दी पर सेट है।")
    await call.message.answer(
        tr("main", "hi"),
        reply_markup=kb_main("hi"),
        parse_mode="HTML"
    )


# ============================================================
# SUPPORT / ABOUT / NEWS
# ============================================================

@dp.callback_query(F.data == "support")
async def support(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(
        tr("support_text", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📩 @GiftsforFunpay", url="https://t.me/GiftsforFunpay")],
            [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("about_text", lang), reply_markup=kb_back(lang))
    await call.answer()


@dp.callback_query(F.data == "news")
async def news(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    rows = fetchall(
        "SELECT id,content,created_at FROM news ORDER BY id DESC LIMIT 5"
    )
    if not rows:
        text = tr("news_empty", lang)
    else:
        text = "📢 <b>Последние новости</b>\n\n"
        for row in rows:
            text += f"#{row['id']} • {row['content']}\n<i>{row['created_at']}</i>\n\n"

    buttons = []
    if is_admin(uid):
        buttons.append([
            InlineKeyboardButton(text=tr("admin_deals", lang), callback_data="admin_deals"),
            InlineKeyboardButton(text="📤 Отправить", callback_data="admin_news")
        ])
    buttons.append([
        InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")
    ])
    await call.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


# ============================================================
# REVIEWS
# ============================================================

@dp.callback_query(F.data.startswith("review_"))
async def review_start(call: CallbackQuery, state: FSMContext):
    # review_<deal_id>_<target_id>
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Invalid", show_alert=True)
        return
    deal_id, target = parts[1], parts[2]
    try:
        target_id = int(target)
    except ValueError:
        await call.answer("Invalid", show_alert=True)
        return

    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    uid = call.from_user.id
    if not deal or deal["status"] != "completed":
        await call.answer(tr("not_allowed", user_lang(uid)), show_alert=True)
        return
    if uid not in (deal["seller_id"], deal["buyer_id"]) or target_id == uid:
        await call.answer(tr("not_allowed", user_lang(uid)), show_alert=True)
        return

    exists = fetchone(
        "SELECT review_id FROM reviews WHERE from_user_id=? AND to_user_id=? AND deal_id=?",
        (uid, target_id, deal_id)
    )
    if exists:
        await call.answer("Already reviewed", show_alert=True)
        return

    await state.update_data(review_deal=deal_id, review_target=target_id)
    await state.set_state(States.review_rating)
    await call.message.answer(
        tr("review_prompt", user_lang(uid)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=str(i), callback_data=f"rating_{i}") for i in range(1, 6)]
        ])
    )
    await call.answer()


@dp.callback_query(F.data.startswith("rating_"), States.review_rating)
async def review_rating(call: CallbackQuery, state: FSMContext):
    rating = int(call.data.replace("rating_", ""))
    await state.update_data(rating=rating)
    await state.set_state(States.review_comment)
    await call.message.answer(tr("review_comment", user_lang(call.from_user.id)))
    await call.answer()


@dp.message(States.review_comment)
async def review_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = message.from_user.id
    target = int(data["review_target"])
    deal_id = data["review_deal"]
    rating = int(data["rating"])
    comment = (message.text or "").strip()
    if comment == "-":
        comment = ""

    try:
        execute(
            """
            INSERT INTO reviews(
                from_user_id,to_user_id,deal_id,rating,comment,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                uid, target, deal_id, rating, comment,
                datetime.now(timezone.utc).isoformat()
            )
        )
    except sqlite3.IntegrityError:
        await state.clear()
        await message.answer("ℹ️ Отзыв уже оставлен.")
        return

    with db() as conn:
        row = conn.execute(
            "SELECT rating,reviews_count FROM users WHERE user_id=?",
            (target,)
        ).fetchone()
        old_rating = float(row["rating"] or 0)
        count = int(row["reviews_count"] or 0)
        new_count = count + 1
        new_rating = ((old_rating * count) + rating) / new_count
        conn.execute(
            "UPDATE users SET rating=?,reviews_count=? WHERE user_id=?",
            (new_rating, new_count, target)
        )
        conn.commit()

    await state.clear()
    await message.answer(
        tr("review_saved", user_lang(uid)),
        reply_markup=kb_back(user_lang(uid))
    )


# ============================================================
# ADMIN
# ============================================================

def admin_required(func):
    return func


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    lang = user_lang(message.from_user.id)
    await message.answer(
        tr("cancelled_fsm", lang),
        reply_markup=kb_main(lang)
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only", user_lang(message.from_user.id)))
        return

    users = fetchone("SELECT COUNT(*) c FROM users")["c"]
    active = fetchone(
        "SELECT COUNT(*) c FROM deals WHERE status NOT IN ('completed','cancelled')"
    )["c"]
    completed = fetchone(
        "SELECT COUNT(*) c FROM deals WHERE status='completed'"
    )["c"]
    cancelled = fetchone(
        "SELECT COUNT(*) c FROM deals WHERE status='cancelled'"
    )["c"]
    total = fetchone("SELECT COUNT(*) c FROM deals")["c"]
    logs = fetchone("SELECT COUNT(*) c FROM admin_logs")["c"]
    service = fetchone("SELECT balance FROM service_balance WHERE id=1")["balance"]

    await message.answer(
        tr("stats", "ru").format(
            users=users, active=active, completed=completed,
            cancelled=cancelled, total=total, logs=logs, service=service
        )
    )


@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only"))
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /ban USER_ID")
        return
    target = int(args[1])
    execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
    admin_log(message.from_user.id, "ban", str(target))
    await message.answer(tr("ban_ok").format(id=target))


@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only"))
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /unban USER_ID")
        return
    target = int(args[1])
    execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
    admin_log(message.from_user.id, "unban", str(target))
    await message.answer(tr("unban_ok").format(id=target))


@dp.message(Command("sendnews"))
async def cmd_sendnews(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only"))
        return
    await state.set_state(States.admin_news)
    await message.answer("Введите текст новости:")


@dp.callback_query(F.data == "admin_news")
async def admin_news_callback(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer(tr("admin_only"), show_alert=True)
        return
    await state.set_state(States.admin_news)
    await call.message.answer("Введите текст новости:")
    await call.answer()


@dp.message(States.admin_news)
async def admin_news_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    content = (message.text or "").strip()
    if not content:
        await message.answer("❌ Пустая новость.")
        return

    now = datetime.now(timezone.utc).isoformat()
    news_id = execute(
        "INSERT INTO news(admin_id,content,created_at) VALUES(?,?,?)",
        (message.from_user.id, content, now)
    )

    users = fetchall("SELECT user_id FROM users WHERE banned=0")
    sent = 0
    for row in users:
        try:
            await bot.send_message(
                row["user_id"],
                f"📢 <b>Новость</b>\n\n{content}",
                parse_mode="HTML"
            )
            sent += 1
        except Exception:
            pass

    execute("UPDATE news SET sent_to=? WHERE id=?", (sent, news_id))
    execute("UPDATE admin_settings SET last_news_id=? WHERE id=1", (news_id,))
    admin_log(message.from_user.id, "sendnews", f"news={news_id},sent={sent}")
    await state.clear()
    await message.answer(f"✅ Новость отправлена: {sent}")


@dp.callback_query(F.data == "admin_deals")
async def admin_deals(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(tr("admin_only"), show_alert=True)
        return

    rows = fetchall(
        """
        SELECT deal_id,seller_username,buyer_username,amount,currency,status
        FROM deals
        WHERE status NOT IN ('completed','cancelled')
        ORDER BY created_at DESC LIMIT 30
        """
    )
    if not rows:
        await call.message.answer("Активных сделок нет.", reply_markup=kb_back("ru"))
        await call.answer()
        return

    for row in rows:
        text = (
            f"📌 <b>#{row['deal_id']}</b>\n"
            f"Продавец: @{row['seller_username'] or '-'}\n"
            f"Покупатель: @{row['buyer_username'] or '-'}\n"
            f"Сумма: {row['amount']} {row['currency']}\n"
            f"Статус: {row['status']}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Завершить",
                callback_data=f"adm_done_{row['deal_id']}"
            )],
            [InlineKeyboardButton(
                text="❌ Отменить",
                callback_data=f"adm_cancel_{row['deal_id']}"
            )],
            [InlineKeyboardButton(
                text="💳 Изменить реквизиты",
                callback_data=f"adm_req_{row['deal_id']}"
            )],
        ])
        await call.message.answer(text, reply_markup=kb)

    await call.answer()


def complete_deal(deal_id, admin_id):
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        return None

    if deal["status"] in ("completed", "cancelled"):
        return deal

    amount = int(deal["amount"] or 0)
    commission = amount * COMMISSION_BPS // 10000
    payout = max(0, amount - commission)

    execute(
        """
        UPDATE deals
        SET status='completed',completed_at=?,commission=?
        WHERE deal_id=?
        """,
        (datetime.now(timezone.utc).isoformat(), commission, deal_id)
    )

    if deal["seller_id"]:
        execute(
            """
            UPDATE users
            SET balance=balance+?, successful_deals=successful_deals+1
            WHERE user_id=?
            """,
            (payout, deal["seller_id"])
        )

    execute(
        "UPDATE service_balance SET balance=balance+? WHERE id=1",
        (commission,)
    )
    admin_log(
        admin_id,
        "complete_deal",
        f"deal={deal_id},commission={commission},payout={payout}"
    )
    return fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))


@dp.callback_query(F.data.startswith("adm_done_"))
async def admin_done(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(tr("admin_only"), show_alert=True)
        return
    deal_id = call.data.replace("adm_done_", "")
    deal = complete_deal(deal_id, call.from_user.id)
    if not deal:
        await call.answer(tr("not_found"), show_alert=True)
        return

    for uid in (deal["seller_id"], deal["buyer_id"]):
        if uid:
            await notify(
                uid,
                tr("admin_done_ok", user_lang(uid)).format(deal_id=deal_id)
            )

    # Offer reviews to both participants.
    if deal["seller_id"] and deal["buyer_id"]:
        for uid, target in (
            (deal["seller_id"], deal["buyer_id"]),
            (deal["buyer_id"], deal["seller_id"]),
        ):
            lang = user_lang(uid)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=tr("review_prompt", lang),
                    callback_data=f"review_{deal_id}_{target}"
                )]
            ])
            await notify(uid, "⭐ " + tr("review_prompt", lang), kb)

    await call.message.edit_text(
        tr("admin_done_ok", "ru").format(deal_id=deal_id)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("adm_cancel_"))
async def admin_cancel(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer(tr("admin_only"), show_alert=True)
        return

    deal_id = call.data.replace("adm_cancel_", "")
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if not deal:
        await call.answer(tr("not_found"), show_alert=True)
        return

    execute(
        "UPDATE deals SET status='cancelled' WHERE deal_id=?",
        (deal_id,)
    )
    admin_log(call.from_user.id, "cancel_deal", deal_id)

    for uid in (deal["seller_id"], deal["buyer_id"]):
        if uid:
            await notify(
                uid,
                tr("admin_cancel_ok", user_lang(uid)).format(deal_id=deal_id)
            )

    await call.message.edit_text(
        tr("admin_cancel_ok", "ru").format(deal_id=deal_id)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("adm_req_"))
async def admin_req(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer(tr("admin_only"), show_alert=True)
        return
    deal_id = call.data.replace("adm_req_", "")
    if not fetchone("SELECT deal_id FROM deals WHERE deal_id=?", (deal_id,)):
        await call.answer(tr("not_found"), show_alert=True)
        return
    await state.update_data(admin_deal_id=deal_id)
    await state.set_state(States.admin_req)
    await call.message.answer(tr("admin_req_prompt", "ru"))
    await call.answer()


@dp.message(States.admin_req)
async def admin_req_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    deal_id = data.get("admin_deal_id")
    req = (message.text or "").strip()
    if len(req) < 3:
        await message.answer(tr("invalid"))
        return
    execute("UPDATE deals SET seller_req=? WHERE deal_id=?", (req, deal_id))
    admin_log(message.from_user.id, "change_requisites", f"deal={deal_id}")
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if deal:
        for uid in (deal["seller_id"], deal["buyer_id"]):
            if uid:
                await notify(
                    uid,
                    tr("admin_req_ok", user_lang(uid)).format(deal_id=deal_id)
                )
    await state.clear()
    await message.answer(tr("admin_req_ok").format(deal_id=deal_id))


# ============================================================
# /novateam — admin completion command
# ============================================================

@dp.message(Command("novateam"))
async def novateam(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only"))
        return

    args = message.text.split()
    if len(args) >= 2:
        deal_id = args[1].strip()
        deal = complete_deal(deal_id, message.from_user.id)
        if not deal:
            await message.answer(tr("not_found"))
            return
        for uid in (deal["seller_id"], deal["buyer_id"]):
            if uid:
                await notify(uid, tr("admin_done_ok", user_lang(uid)).format(deal_id=deal_id))
        await message.answer(tr("admin_done_ok").format(deal_id=deal_id))
        return

    rows = fetchall(
        """
        SELECT deal_id FROM deals
        WHERE status='active'
        """
    )
    count = 0
    for row in rows:
        deal = complete_deal(row["deal_id"], message.from_user.id)
        if deal:
            count += 1
            for uid in (deal["seller_id"], deal["buyer_id"]):
                if uid:
                    await notify(
                        uid,
                        tr("admin_done_ok", user_lang(uid)).format(
                            deal_id=row["deal_id"]
                        )
                    )
    await message.answer(f"✅ Завершено сделок: {count}")


# ============================================================
# /referral
# ============================================================

@dp.message(Command("referral"))
async def referral_command(message: Message):
    ensure_user(message.from_user)
    uid = message.from_user.id
    count = fetchone(
        "SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,)
    )["c"]
    await message.answer(
        f"🔗 Реферальная ссылка:\n"
        f"https://t.me/{BOT_USERNAME}?start=ref{uid}\n\n"
        f"Приглашено: {count}"
    )


# ============================================================
# /admin — compact panel
# ============================================================

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only"))
        return
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\n"
        "/stats — статистика\n"
        "/sendnews — рассылка\n"
        "/novateam [DEAL_ID] — завершить\n"
        "/ban USER_ID — блокировка\n"
        "/unban USER_ID — разблокировка",
        parse_mode="HTML"
    )


# ============================================================
# AUTO ARCHIVE
# ============================================================

def archive_old_deals():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ARCHIVE_AFTER_HOURS)
    rows = fetchall(
        """
        SELECT * FROM deals
        WHERE status='completed' AND completed_at IS NOT NULL
        """
    )
    for row in rows:
        try:
            completed_at = datetime.fromisoformat(row["completed_at"])
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            if completed_at <= cutoff:
                cols = [
                    "deal_id","seller_id","buyer_id","deal_type","description",
                    "amount","currency","seller_req","buyer_req","gift_link",
                    "status","seller_username","buyer_username","created_at",
                    "completed_at","confirmed_at","commission"
                ]
                vals = [row[c] for c in cols]
                placeholders = ",".join("?" for _ in cols)
                execute(
                    f"""
                    INSERT OR REPLACE INTO archived_deals
                    ({','.join(cols)},archived_at)
                    VALUES({placeholders},?)
                    """,
                    vals + [datetime.now(timezone.utc).isoformat()]
                )
                execute("DELETE FROM deals WHERE deal_id=?", (row["deal_id"],))
        except Exception:
            logger.exception("Archive failed for %s", row["deal_id"])


async def archive_loop():
    while True:
        try:
            archive_old_deals()
        except Exception:
            logger.exception("Archive loop error")
        await asyncio.sleep(3600)


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@dp.errors()
async def global_error_handler(event):
    logger.exception("Unhandled aiogram error: %s", event)
    try:
        await admin_error(str(event))
    except Exception:
        pass
    return True


# ============================================================
# WEBHOOK / POLLING
# ============================================================

async def root(request):
    return web.Response(text="FUNPAY OTC DEMO is running")


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
        await bot.set_webhook(
            url=full_url,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
        info = await bot.get_webhook_info()
        if info.url != full_url:
            raise RuntimeError(
                f"Webhook verification failed: expected {full_url}, got {info.url}"
            )
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


async def main():
    init_db()
    asyncio.create_task(archive_loop())
    await run_webhook()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
