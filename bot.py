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
PHOTO_URL = os.getenv("PHOTO_URL", "https://ibb.co/ycJNGhRQ")  # Заменить на прямую ссылку .jpg/.png
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "8625870625").split(",") if x.strip().isdigit()}
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
        """)
        conn.execute("INSERT OR IGNORE INTO service_balance(id, balance) VALUES (1, 0)")
        conn.execute("INSERT OR IGNORE INTO admin_settings(id, last_news_id) VALUES (1, 0)")
        conn.commit()

init_db()

# ============================================================
# ЛОКАЛИЗАЦИЯ (6 ПОЛНЫХ ЯЗЫКОВ)
# ============================================================
LANG_NAMES = {"ru": "Русский", "en": "English", "uk": "Українська", "kk": "Қазақша", "zh": "中文", "hi": "हिन्दी"}

T = {
    "ru": {
        "main": (
            "🛡️ Добро пожаловать\n\n"
            "<b>FunPay</b> - Мы специализированный сервис по обеспечению безопасности вне биржевых сделок.\n\n"
            "• Автоматизированный алгоритм исполнения.\n"
            "• Скорость и автоматизация.\n"
            "• Удобный и быстрый вывод средств.\n\n"
            "• Комиссия сервиса: <b>1%</b>\n"
            "• Режим работы: <b>24/7</b>\n"
            "• Техническая поддержка: @GiftsForFunpay\n\n"
            "Выберите нужный раздел ниже"
        ),
        "create": "📝 Создать сделку",
        "my_deals": "📋 Мои сделки",
        "req": "💳 Реквизиты",
        "referral": "💠 Рефералы",
        "profile": "👤 Профиль",
        "support": "🆘 Поддержка",
        "about": "ℹ️ О сервисе",
        "back": "🔙 Назад",
        "language": "🌐 Язык",
        "seller": "👤 Я продавец",
        "buyer": "🛒 Я покупатель",
        "account": "📦 Аккаунт / товар",
        "gift": "🎁 NFT Gift",
        "choose_role": "Выберите вашу роль:",
        "choose_type": "Выберите тип сделки:",
        "description": "✍️ Опишите предмет сделки:\n\nНапример: https://t.me/nft/PlushPepe-111\nили просто текстовое описание товара",
        "currency": "💱 Выберите валюту:",
        "amount": "💰 Введите сумму целым числом:",
        "requisites": "💳 Введите реквизиты для получения оплаты:",
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
        "confirmed": (
            "💳 Первичная Оплата подтверждена\n\n"
            "Сделка: #{deal_id}\n"
            "Продавец: @{seller}\n"
            "Рейтинг: {rating}/5\n"
            "Успешных сделок: {successful}\n"
            "Сумма: {amount} {currency}\n"
            "Предмет: {description}\n\n"
            "Ожидаем передачу товара менеджеру @GiftsForFunpay."
        ),
        "buyer_notify": "📩 Продавец подтвердил участие в сделке #{deal_id}.\n\n💰 {amount} {currency}\n💳 Реквизиты продавца:\n{req}",
        "deal_active": "🟢 Активна",
        "waiting_buyer": "🟡 Ожидает покупателя",
        "waiting_seller": "🟡 Ожидает продавца",
        "completed": "✅ Завершена",
        "cancelled_status": "❌ Отменена",
        "balance": "💰 <b>Баланс</b>\n\nДоступно: <b>{balance}</b>\nЗаморожено: <b>{frozen}</b>",
        "deposit": "➕ Пополнить",
        "withdraw": "➖ Вывести",
        "deposit_amount": "Введите сумму для пополнения:",
        "withdraw_amount": "Введите сумму для вывода:",
        "deposit_ok": "✅ Баланс пополнен на {amount}.",
        "withdraw_ok": "✅ Выведено {amount}.",
        "not_enough": "❌ Недостаточно средств.",
        "positive": "❌ Сумма должна быть больше нуля.",
        "my_deals_empty": "📭 У вас нет сделок.",
        "my_deals_title": "📋 Мои сделки\n\n",
        "profile_text": (
            "👤 Профиль\n\n"
            "ID: {id}\n"
            "Username: @{username}\n"
            "Сделок: {deals}\n"
            "Успешных: {successful}\n"
            "Рейтинг: {rating} ({reviews})\n"
            "Рефералов: {refs}\n"
        ),
        "referral_text": (
            "💠 РЕФЕРАЛЬНАЯ ПРОГРАММА\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🔗 Ваша ссылка:\n{link}\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📊 СТАТИСТИКА:\n\n"
            "• Всего приглашено: {total}\n"
            "• Активных рефералов: 0\n"
            "• Общий объем сделок: 0.00 ₽\n\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💰 ВАШИ БОНУСЫ:\n\n"
            "• За каждого активного реферала: +5% к балансу\n"
            "• При первой сделке реферала: +100 ₽"
        ),
        "req_menu": "✏️ Выберите валюту для изменения реквизитов",
        "req_prompt": "✏️ Введите ваш номер {currency} для {currency_name}\n\n📝 Пример:\n{example}",
        "req_saved": "✅ Реквизит сохранён.",
        "support_text": "🆘 Поддержка: @GiftsForFunpay\n\nПо всем вопросам обращайтесь к менеджеру.",
        "about_text": (
            "👋 Подробнее:\n\n"
            "Мы – гарант сервис, наша задача помочь вам провести безопасные сделки, и оформить быстрый вывод!\n\n"
            "Ответы на частые вопросы:\n\n"
            "• Как долго происходит вывод? Обычно не более 2-х минут, в редких случаях до 2-х часов.\n\n"
            "• Почему нужно передавать подарок менеджеру, но не покупателю? Причина проста: покупатель может наврать что ему не пришёл подарок, что затягивает ситуацию, но наш менеджер автоматически проверяет наличие NFT подарка и уже обмануть не получится.\n\n"
            "• Как быстро происходит пополнение? Пополнение также занимает не более 2-х минут.\n\n"
            "• Я увидел похожего бота, стоит ли мне доверять? Если вы увидели другого бота кроме @FunpayTrustly_robot, ни в коем случае не проводите с ним сделки!"
        ),
        "language_text": "🌐 Выберите язык:",
        "language_set": "✅ Язык установлен: {lang}",
        "admin_only": "🚫 Только для администратора.",
        "banned": "🚫 Ваш аккаунт заблокирован для операций.",
        "active_limit": "❌ Максимум 5 незавершённых сделок.",
        "seller_not_found": "❌ Пользователь с таким username не найден в базе бота.",
        "cancelled_fsm": "✅ Текущее действие отменено.",
        "stats": "📊 Статистика\n\nПользователей: {users}\nАктивных: {active}\nЗавершённых: {completed}\nОтменённых: {cancelled}\nВсего сделок: {total}\nЛогов админов: {logs}\nБаланс сервиса: {service}\n",
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
        "clear_history": "🗑️ Очистить историю",
        "history_cleared": "✅ История сделок очищена (завершённые сделки заархивированы)."
    },
    "en": {
        "main": "🛡️ Welcome\n\n<b>FunPay</b> - We are a specialized service for ensuring security in off-exchange transactions.\n\n• Automated execution algorithm.\n• Speed and automation.\n• Convenient and fast withdrawal of funds.\n\n• Service commission: <b>1%</b>\n• Operating mode: <b>24/7</b>\n• Technical support: @GiftsForFunpay\n\nSelect the section you need below",
        "create": "📝 Create deal",
        "my_deals": "📋 My deals",
        "req": "💳 Requisites",
        "referral": "💠 Referrals",
        "profile": "👤 Profile",
        "support": "🆘 Support",
        "about": "ℹ️ About",
        "back": "🔙 Back",
        "language": "🌐 Language",
        "seller": "👤 I am seller",
        "buyer": "🛒 I am buyer",
        "account": "📦 Account / goods",
        "gift": "🎁 NFT Gift",
        "choose_role": "Choose your role:",
        "choose_type": "Choose deal type:",
        "description": "✍️ Describe the subject of the deal:\n\nExample: https://t.me/nft/PlushPepe-111\nor just a text description of the product",
        "currency": "💱 Choose currency:",
        "amount": "💰 Enter integer amount:",
        "requisites": "💳 Enter receiving requisites:",
        "seller_username": "👤 Enter seller @username:",
        "deal_created": "✅ Deal #<b>{deal_id}</b> successfully created!\n\n💵 Currency: {currency}\n💰 Amount: {amount} {currency}\n🎁 NFT Quantity: 1\n\n📎 NFT Links:\n• {gift_link}\n\n🔗 Buyer link:\n{link}\n\n⏳ Waiting for buyer to connect.",
        "deal_created_buyer": "✅ Deal #<b>{deal_id}</b> successfully created!\n\n💵 Currency: {currency}\n💰 Amount: {amount} {currency}\n\n🔗 Seller link:\n{link}\n\n⏳ Waiting for seller to connect.",
        "joined": "✅ You joined deal #{deal_id}.",
        "already_member": "ℹ️ You are already a participant.",
        "full": "ℹ️ Both roles are already taken.",
        "self_deal": "❌ You cannot take the second role in your own deal.",
        "confirm": "✅ Confirm participation",
        "cancel_deal": "❌ Cancel deal",
        "details": "🔎 Details",
        "cancelled": "❌ Deal #{deal_id} cancelled.",
        "not_found": "🚫 Deal not found.",
        "not_allowed": "🚫 Action not allowed.",
        "confirmed": "💳 Primary Payment confirmed\n\nDeal: #{deal_id}\nSeller: @{seller}\nRating: {rating}/5\nSuccessful deals: {successful}\nAmount: {amount} {currency}\nItem: {description}\n\nWaiting for goods transfer to manager @GiftsForFunpay.",
        "buyer_notify": "📩 Seller confirmed participation in deal #{deal_id}.\n\n💰 {amount} {currency}\n💳 Seller requisites:\n{req}",
        "deal_active": "🟢 Active",
        "waiting_buyer": "🟡 Waiting for buyer",
        "waiting_seller": "🟡 Waiting for seller",
        "completed": "✅ Completed",
        "cancelled_status": "❌ Cancelled",
        "balance": "💰 <b>Balance</b>\n\nAvailable: <b>{balance}</b>\nFrozen: <b>{frozen}</b>",
        "deposit": "➕ Deposit",
        "withdraw": "➖ Withdraw",
        "deposit_amount": "Enter deposit amount:",
        "withdraw_amount": "Enter withdrawal amount:",
        "deposit_ok": "✅ Balance increased by {amount}.",
        "withdraw_ok": "✅ Withdrawn {amount}.",
        "not_enough": "❌ Not enough funds.",
        "positive": "❌ Amount must be greater than zero.",
        "my_deals_empty": "📭 You have no deals.",
        "my_deals_title": "📋 My deals\n\n",
        "profile_text": "👤 Profile\n\nID: {id}\nUsername: @{username}\nDeals: {deals}\nSuccessful: {successful}\nRating: {rating} ({reviews})\nReferrals: {refs}\n",
        "referral_text": "💠 REFERRAL PROGRAM\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Your link:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 STATISTICS:\n\n• Total invited: {total}\n• Active referrals: 0\n• Total deal volume: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 YOUR BONUSES:\n\n• For each active referral: +5% to balance\n• On referral's first deal: +100 ₽",
        "req_menu": "✏️ Choose currency to change requisites",
        "req_prompt": "✏️ Enter your {currency} for {currency_name}\n\n📝 Example:\n{example}",
        "req_saved": "✅ Requisite saved.",
        "support_text": "🆘 Support: @GiftsForFunpay\n\nFor any questions, contact the manager.",
        "about_text": "👋 Details:\n\nWe are a guarantor service, our task is to help you conduct safe deals and process fast withdrawals!\n\nFrequently asked questions:\n\n• How long does a withdrawal take? Usually no more than 2 minutes, in rare cases up to 2 hours.\n\n• Why should the gift be transferred to the manager and not the buyer? The reason is simple: the buyer could lie that they didn't receive the gift, which delays the situation, but our manager automatically checks the presence of the NFT gift and it will not be possible to deceive.\n\n• How fast is the deposit? Deposit also takes no more than 2 minutes.\n\n• I saw a similar bot, should I trust it? If you see another bot besides @FunpayTrustly_robot, do not conduct deals with it under any circumstances!",
        "language_text": "🌐 Choose language:",
        "language_set": "✅ Language set: {lang}",
        "admin_only": "🚫 Admin only.",
        "banned": "🚫 Your account is blocked.",
        "active_limit": "❌ Maximum 5 active deals.",
        "seller_not_found": "❌ User with this username not found in bot database.",
        "cancelled_fsm": "✅ Current action cancelled.",
        "stats": "📊 Statistics\n\nUsers: {users}\nActive: {active}\nCompleted: {completed}\nCancelled: {cancelled}\nTotal deals: {total}\nAdmin logs: {logs}\nService balance: {service}\n",
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
        "ban_ok": "🚫 User {id} blocked.",
        "unban_ok": "✅ User {id} unblocked.",
        "invalid": "❌ Invalid value.",
        "clear_history": "🗑️ Clear history",
        "history_cleared": "✅ Deal history cleared (completed deals archived)."
    },
    "uk": {
        "main": "🛡️ Ласкаво просимо\n\n<b>FunPay</b> - Ми спеціалізований сервіс із забезпечення безпеки позабіржових угод.\n\n• Автоматизований алгоритм виконання.\n• Швидкість та автоматизація.\n• Зручний та швидкий вивід коштів.\n\n• Комісія сервісу: <b>1%</b>\n• Режим роботи: <b>24/7</b>\n• Технічна підтримка: @GiftsForFunpay\n\nОберіть потрібний розділ нижче",
        "create": "📝 Створити угоду",
        "my_deals": "📋 Мої угоди",
        "req": "💳 Реквізити",
        "referral": "💠 Реферали",
        "profile": "👤 Профіль",
        "support": "🆘 Підтримка",
        "about": "ℹ️ Про сервіс",
        "back": "🔙 Назад",
        "language": "🌐 Мова",
        "seller": "👤 Я продавець",
        "buyer": "🛒 Я покупець",
        "account": "📦 Акаунт / товар",
        "gift": "🎁 NFT Gift",
        "choose_role": "Оберіть вашу роль:",
        "choose_type": "Оберіть тип угоди:",
        "description": "✍️ Опишіть предмет угоди:\n\nНаприклад: https://t.me/nft/PlushPepe-111\nабо просто текстовий опис товару",
        "currency": "💱 Оберіть валюту:",
        "amount": "💰 Введіть суму цілим числом:",
        "requisites": "💳 Введіть реквізити для отримання оплати:",
        "seller_username": "👤 Введіть @username продавця:",
        "deal_created": "✅ Угода #<b>{deal_id}</b> успішно створена!\n\n💵 Валюта: {currency}\n💰 Сума: {amount} {currency}\n🎁 Кількість NFT: 1\n\n📎 Посилання на NFT:\n• {gift_link}\n\n🔗 Посилання для покупця:\n{link}\n\n⏳ Очікуйте підключення покупця.",
        "deal_created_buyer": "✅ Угода #<b>{deal_id}</b> успішно створена!\n\n💵 Валюта: {currency}\n💰 Сума: {amount} {currency}\n\n🔗 Посилання для продавця:\n{link}\n\n⏳ Очікуйте підключення продавця.",
        "joined": "✅ Ви приєдналися до угоди #{deal_id}.",
        "already_member": "ℹ️ Ви вже є учасником цієї угоди.",
        "full": "ℹ️ Угода вже заповнена обома ролями.",
        "self_deal": "❌ Не можна зайняти другу роль у власній угоді.",
        "confirm": "✅ Підтвердити участь",
        "cancel_deal": "❌ Скасувати угоду",
        "details": "🔎 Деталі",
        "cancelled": "❌ Угода #{deal_id} скасована.",
        "not_found": "🚫 Угоду не знайдено.",
        "not_allowed": "🚫 Дія недоступна.",
        "confirmed": "💳 Первинну Оплату підтверджено\n\nУгода: #{deal_id}\nПродавець: @{seller}\nРейтинг: {rating}/5\nУспішних угод: {successful}\nСума: {amount} {currency}\nПредмет: {description}\n\nОчікуємо передачу товару менеджеру @GiftsForFunpay.",
        "buyer_notify": "📩 Продавець підтвердив участь в угоді #{deal_id}.\n\n💰 {amount} {currency}\n💳 Реквізити продавця:\n{req}",
        "deal_active": "🟢 Активна",
        "waiting_buyer": "🟡 Очікує покупця",
        "waiting_seller": "🟡 Очікує продавця",
        "completed": "✅ Завершена",
        "cancelled_status": "❌ Скасована",
        "balance": "💰 <b>Баланс</b>\n\nДоступно: <b>{balance}</b>\nЗаморожено: <b>{frozen}</b>",
        "deposit": "➕ Поповнити",
        "withdraw": "➖ Вивести",
        "deposit_amount": "Введіть суму для поповнення:",
        "withdraw_amount": "Введіть суму для виведення:",
        "deposit_ok": "✅ Баланс поповнено на {amount}.",
        "withdraw_ok": "✅ Виведено {amount}.",
        "not_enough": "❌ Недостатньо коштів.",
        "positive": "❌ Сума повинна бути більшою за нуль.",
        "my_deals_empty": "📭 У вас немає угод.",
        "my_deals_title": "📋 Мої угоди\n\n",
        "profile_text": "👤 Профіль\n\nID: {id}\nUsername: @{username}\nУгоди: {deals}\nУспішних: {successful}\nРейтинг: {rating} ({reviews})\nРефералів: {refs}\n",
        "referral_text": "💠 РЕФЕРАЛЬНА ПРОГРАМА\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Ваше посилання:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 СТАТИСТИКА:\n\n• Всього запрошено: {total}\n• Активних рефералів: 0\n• Загальний обсяг угод: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 ВАШІ БОНУСИ:\n\n• За кожного активного реферала: +5% до балансу\n• При першій угоді реферала: +100 ₽",
        "req_menu": "✏️ Виберіть валюту для зміни реквізитів",
        "req_prompt": "✏️ Введіть ваш {currency} для {currency_name}\n\n📝 Приклад:\n{example}",
        "req_saved": "✅ Реквізит збережено.",
        "support_text": "🆘 Підтримка: @GiftsForFunpay\n\nЗ будь-яких питань звертайтеся до менеджера.",
        "about_text": "👋 Детальніше:\n\nМи – гарант-сервіс, наше завдання допомогти вам провести безпечні угоди та оформити швидкий вивід!\n\nВідповіді на часті питання:\n\n• Як довго триває вивід? Зазвичай не більше 2-х хвилин, в рідкісних випадках до 2-х годин.\n\n• Чому потрібно передавати подарунок менеджеру, а не покупцю? Причина проста: покупець може набрехати, що йому не прийшов подарунок, що затягує ситуацію, але наш менеджер автоматично перевіряє наявність NFT подарунка і обманути не вийде.\n\n• Як швидко відбувається поповнення? Поповнення також займає не більше 2-х хвилин.\n\n• Я побачив схожого бота, чи варто мені довіряти? Якщо ви побачили іншого бота, крім @FunpayTrustly_robot, ні в якому разі не проводьте з ним угоди!",
        "language_text": "🌐 Оберіть мову:",
        "language_set": "✅ Мову встановлено: {lang}",
        "admin_only": "🚫 Тільки для адміністратора.",
        "banned": "🚫 Ваш акаунт заблоковано.",
        "active_limit": "❌ Максимум 5 активних угод.",
        "seller_not_found": "❌ Користувача з таким username не знайдено в базі бота.",
        "cancelled_fsm": "✅ Поточну дію скасовано.",
        "stats": "📊 Статистика\n\nКористувачів: {users}\nАктивних: {active}\nЗавершених: {completed}\nСкасованих: {cancelled}\nВсього угод: {total}\nЛогів адмінів: {logs}\nБаланс сервісу: {service}\n",
        "review_prompt": "⭐ Оцініть контрагента від 1 до 5:",
        "review_comment": "Напишіть короткий коментар або надішліть '-'",
        "review_saved": "✅ Відгук збережено. Дякуємо!",
        "admin_deals": "🛠 Керування угодами",
        "admin_done": "✅ Завершити",
        "admin_cancel": "❌ Скасувати",
        "admin_req": "💳 Змінити реквізити",
        "admin_req_prompt": "Введіть нові реквізити продавця:",
        "admin_done_ok": "✅ Угоду #{deal_id} завершено адміністратором.",
        "admin_cancel_ok": "❌ Угоду #{deal_id} скасовано адміністратором.",
        "admin_req_ok": "✅ Реквізити угоди #{deal_id} змінено.",
        "ban_ok": "🚫 Користувача {id} заблоковано.",
        "unban_ok": "✅ Користувача {id} розблоковано.",
        "invalid": "❌ Некоректне значення.",
        "clear_history": "🗑️ Очистити історію",
        "history_cleared": "✅ Історію угод очищено (завершені угоди заархівовано)."
    },
    "kk": {
        "main": "🛡️ Қош келдіңіз\n\n<b>FunPay</b> - Біз биржадан тыс мәмілелердің қауіпсіздігін қамтамасыз етуге мамандандырылған қызмет.\n\n• Орындаудың автоматтандырылған алгоритмі.\n• Жылдамдық және автоматтандыру.\n• Ақшаны ыңғайлы және жылдам шығару.\n\n• Қызмет комиссиясы: <b>1%</b>\n• Жұмыс режимі: <b>24/7</b>\n• Техникалық қолдау: @GiftsForFunpay\n\nТөменде қажетті бөлімді таңдаңыз",
        "create": "📝 Мәміле жасау",
        "my_deals": "📋 Менің мәмілелерім",
        "req": "💳 Реквизиттер",
        "referral": "💠 Рефералдар",
        "profile": "👤 Профиль",
        "support": "🆘 Қолдау",
        "about": "ℹ️ Сервис туралы",
        "back": "🔙 Артқа",
        "language": "🌐 Тіл",
        "seller": "👤 Мен сатушымын",
        "buyer": "🛒 Мен сатып алушымын",
        "account": "📦 Аккаунт / тауар",
        "gift": "🎁 NFT Gift",
        "choose_role": "Рөліңізді таңдаңыз:",
        "choose_type": "Мәміле түрін таңдаңыз:",
        "description": "✍️ Мәміле нысанын сипаттаңыз:\n\nМысалы: https://t.me/nft/PlushPepe-111\nнемесе өнімнің қарапайым мәтіндік сипаттамасы",
        "currency": "💱 Валютаны таңдаңыз:",
        "amount": "💰 Соманы бүтін санмен енгізіңіз:",
        "requisites": "💳 Төлем алу реквизиттерін енгізіңіз:",
        "seller_username": "👤 Сатушының @username енгізіңіз:",
        "deal_created": "✅ Мәміле #<b>{deal_id}</b> сәтті жасалды!\n\n💵 Валюта: {currency}\n💰 Сома: {amount} {currency}\n🎁 NFT саны: 1\n\n📎 NFT сілтемелері:\n• {gift_link}\n\n🔗 Сатып алушыға арналған сілтеме:\n{link}\n\n⏳ Сатып алушының қосылуын күтіңіз.",
        "deal_created_buyer": "✅ Мәміле #<b>{deal_id}</b> сәтті жасалды!\n\n💵 Валюта: {currency}\n💰 Сома: {amount} {currency}\n\n🔗 Сатушыға арналған сілтеме:\n{link}\n\n⏳ Сатушының қосылуын күтіңіз.",
        "joined": "✅ Сіз #{deal_id} мәмілесіне қосылдыңыз.",
        "already_member": "ℹ️ Сіз бұл мәміленің қатысушысыз.",
        "full": "ℹ️ Мәміледе екі рөл де бос емес.",
        "self_deal": "❌ Өз мәмілеңізде екінші рөлді ала алмайсыз.",
        "confirm": "✅ Қатысуды растау",
        "cancel_deal": "❌ Мәмілені болдырмау",
        "details": "🔎 Мәліметтер",
        "cancelled": "❌ #{deal_id} мәмілесі болдырылмады.",
        "not_found": "🚫 Мәміле табылмады.",
        "not_allowed": "🚫 Әрекетке рұқсат жоқ.",
        "confirmed": "💳 Негізгі төлем расталды\n\nМәміле: #{deal_id}\nСатушы: @{seller}\nРейтинг: {rating}/5\nСәтті мәмілелер: {successful}\nСома: {amount} {currency}\nЗат: {description}\n\nТауарды @GiftsForFunpay менеджеріне беруді күтеміз.",
        "buyer_notify": "📩 Сатушы #{deal_id} мәмілесіне қатысуды растады.\n\n💰 {amount} {currency}\n💳 Сатушы реквизиттері:\n{req}",
        "deal_active": "🟢 Белсенді",
        "waiting_buyer": "🟡 Сатып алушыны күту",
        "waiting_seller": "🟡 Сатушыны күту",
        "completed": "✅ Аяқталды",
        "cancelled_status": "❌ Болдырылмады",
        "balance": "💰 <b>Баланс</b>\n\nҚолжетімді: <b>{balance}</b>\nҚатырылған: <b>{frozen}</b>",
        "deposit": "➕ Толықтыру",
        "withdraw": "➖ Шығару",
        "deposit_amount": "Толықтыру сомасын енгізіңіз:",
        "withdraw_amount": "Шығару сомасын енгізіңіз:",
        "deposit_ok": "✅ Баланс {amount}-ға толтырылды.",
        "withdraw_ok": "✅ {amount} шығарылды.",
        "not_enough": "❌ Қаражат жеткіліксіз.",
        "positive": "❌ Сома нөлден үлкен болуы керек.",
        "my_deals_empty": "📭 Сізде мәмілелер жоқ.",
        "my_deals_title": "📋 Менің мәмілелерім\n\n",
        "profile_text": "👤 Профиль\n\nID: {id}\nUsername: @{username}\nМәмілелер: {deals}\nСәтті: {successful}\nРейтинг: {rating} ({reviews})\nРефералдар: {refs}\n",
        "referral_text": "💠 РЕФЕРАЛДЫҚ БАҒДАРЛАМА\n━━━━━━━━━━━━━━━━━━━\n\n🔗 Сіздің сілтеме:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 СТАТИСТИКА:\n\n• Барлығы шақырылған: {total}\n• Белсенді рефералдар: 0\n• Мәмілелердің жалпы көлемі: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 СІЗДІҢ БОНУСТАР:\n\n• Әрбір белсенді реферал үшін: балансқа +5%\n• Рефералдың алғашқы мәмілесінде: +100 ₽",
        "req_menu": "✏️ Реквизиттерді өзгерту үшін валютаны таңдаңыз",
        "req_prompt": "✏️ {currency_name} үшін {currency} нөмірін енгізіңіз\n\n📝 Мысалы:\n{example}",
        "req_saved": "✅ Реквизит сақталды.",
        "support_text": "🆘 Қолдау: @GiftsForFunpay\n\nКез келген сұрақ бойынша менеджерге хабарласыңыз.",
        "about_text": "👋 Толығырақ:\n\nБіз – кепілдік қызметі, біздің міндетіміз – сізге қауіпсіз мәмілелер жүргізуге және жылдам шығаруды ресімдеуге көмектесу!\n\nЖиі қойылатын сұрақтарға жауаптар:\n\n• Шығару қанша уақытқа созылады? Әдетте 2 минуттан аспайды, сирек жағдайларда 2 сағатқа дейін.\n\n• Неліктен сыйлықты сатып алушыға емес, менеджерге беру керек? Себебі қарапайым: сатып алушы сыйлық келмеді деп өтірік айтуы мүмкін, бұл жағдайды ұзартады, бірақ біздің менеджер NFT сыйлығының бар-жоғын автоматты түрде тексереді және алдау мүмкін емес.\n\n• Толықтыру қаншалықты жылдам? Толықтыру да 2 минуттан аспайды.\n\n• Мен ұқсас ботты көрдім, оған сену керек пе? Егер сіз @FunpayTrustly_robot-тан басқа ботты көрсеңіз, ешбір жағдайда онымен мәміле жүргізбеңіз!",
        "language_text": "🌐 Тілді таңдаңыз:",
        "language_set": "✅ Тіл орнатылды: {lang}",
        "admin_only": "🚫 Тек әкімшілер үшін.",
        "banned": "🚫 Сіздің аккаунтыңыз бұғатталған.",
        "active_limit": "❌ Максимум 5 белсенді мәміле.",
        "seller_not_found": "❌ Бұл username бар пайдаланушы бот дерекқорында табылмады.",
        "cancelled_fsm": "✅ Ағымдағы әрекет болдырылмады.",
        "stats": "📊 Статистика\n\nПайдаланушылар: {users}\nБелсенді: {active}\nАяқталған: {completed}\nБолдырылмаған: {cancelled}\nБарлығы мәмілелер: {total}\nӘкімші логтары: {logs}\nҚызмет балансы: {service}\n",
        "review_prompt": "⭐ Контрагентті 1-ден 5-ке дейін бағалаңыз:",
        "review_comment": "Қысқаша пікір жазыңыз немесе '-' жіберіңіз",
        "review_saved": "✅ Пікір сақталды. Рахмет!",
        "admin_deals": "🛠 Мәмілелерді басқару",
        "admin_done": "✅ Аяқтау",
        "admin_cancel": "❌ Болдырмау",
        "admin_req": "💳 Реквизиттерді өзгерту",
        "admin_req_prompt": "Сатушының жаңа реквизиттерін енгізіңіз:",
        "admin_done_ok": "✅ #{deal_id} мәмілесі әкімшімен аяқталды.",
        "admin_cancel_ok": "❌ #{deal_id} мәмілесі әкімшімен болдырылмады.",
        "admin_req_ok": "✅ #{deal_id} мәмілесінің реквизиттері өзгертілді.",
        "ban_ok": "🚫 {id} пайдаланушысы бұғатталды.",
        "unban_ok": "✅ {id} пайдаланушысы бұғаттан шығарылды.",
        "invalid": "❌ Қате мән.",
        "clear_history": "🗑️ Тарихты тазалау",
        "history_cleared": "✅ Мәмілелер тарихы тазартылды (аяқталған мәмілелер мұрағатталды)."
    },
    "zh": {
        "main": "🛡️ 欢迎\n\n<b>FunPay</b> - 我们是一家专门为场外交易提供安全保障的服务商。\n\n• 自动化执行算法。\n• 速度和自动化。\n• 方便快速的提现。\n\n• 服务佣金：<b>1%</b>\n• 工作时间：<b>24/7</b>\n• 技术支持：@GiftsForFunpay\n\n请选择下方所需部分",
        "create": "📝 创建交易",
        "my_deals": "📋 我的交易",
        "req": "💳 收款信息",
        "referral": "💠 推荐",
        "profile": "👤 个人资料",
        "support": "🆘 支持",
        "about": "ℹ️ 关于服务",
        "back": "🔙 返回",
        "language": "🌐 语言",
        "seller": "👤 我是卖家",
        "buyer": "🛒 我是买家",
        "account": "📦 账号 / 商品",
        "gift": "🎁 NFT Gift",
        "choose_role": "请选择您的角色：",
        "choose_type": "请选择交易类型：",
        "description": "✍️ 描述交易物品：\n\n例如：https://t.me/nft/PlushPepe-111\n或者简单的文本描述",
        "currency": "💱 选择货币：",
        "amount": "💰 输入整数金额：",
        "requisites": "💳 输入收款信息：",
        "seller_username": "👤 输入卖家 @username：",
        "deal_created": "✅ 交易 #<b>{deal_id}</b> 成功创建！\n\n💵 货币：{currency}\n💰 金额：{amount} {currency}\n🎁 NFT 数量：1\n\n📎 NFT 链接：\n• {gift_link}\n\n🔗 买家链接：\n{link}\n\n⏳ 请等待买家连接。",
        "deal_created_buyer": "✅ 交易 #<b>{deal_id}</b> 成功创建！\n\n💵 货币：{currency}\n💰 金额：{amount} {currency}\n\n🔗 卖家链接：\n{link}\n\n⏳ 请等待卖家连接。",
        "joined": "✅ 您已加入交易 #{deal_id}。",
        "already_member": "ℹ️ 您已经是该交易的参与者。",
        "full": "ℹ️ 交易已满员。",
        "self_deal": "❌ 不能在自己的交易中担任第二角色。",
        "confirm": "✅ 确认参与",
        "cancel_deal": "❌ 取消交易",
        "details": "🔎 详情",
        "cancelled": "❌ 交易 #{deal_id} 已取消。",
        "not_found": "🚫 未找到交易。",
        "not_allowed": "🚫 操作不允许。",
        "confirmed": "💳 首次付款已确认\n\n交易：#{deal_id}\n卖家：@{seller}\n评分：{rating}/5\n成功交易：{successful}\n金额：{amount} {currency}\n物品：{description}\n\n等待将商品转交给经理 @GiftsForFunpay。",
        "buyer_notify": "📩 卖家已确认参与交易 #{deal_id}。\n\n💰 {amount} {currency}\n💳 卖家收款信息：\n{req}",
        "deal_active": "🟢 进行中",
        "waiting_buyer": "🟡 等待买家",
        "waiting_seller": "🟡 等待卖家",
        "completed": "✅ 已完成",
        "cancelled_status": "❌ 已取消",
        "balance": "💰 <b>余额</b>\n\n可用：<b>{balance}</b>\n冻结：<b>{frozen}</b>",
        "deposit": "➕ 充值",
        "withdraw": "➖ 提现",
        "deposit_amount": "请输入充值金额：",
        "withdraw_amount": "请输入提现金额：",
        "deposit_ok": "✅ 余额增加了 {amount}。",
        "withdraw_ok": "✅ 提现了 {amount}。",
        "not_enough": "❌ 余额不足。",
        "positive": "❌ 金额必须大于零。",
        "my_deals_empty": "📭 您没有交易。",
        "my_deals_title": "📋 我的交易\n\n",
        "profile_text": "👤 个人资料\n\nID: {id}\n用户名：@{username}\n交易：{deals}\n成功：{successful}\n评分：{rating} ({reviews})\n推荐：{refs}\n",
        "referral_text": "💠 推荐计划\n━━━━━━━━━━━━━━━━━━━\n\n🔗 您的链接：\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 统计：\n\n• 总共邀请：{total}\n• 活跃推荐：0\n• 总交易额：0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 您的奖励：\n\n• 每个活跃推荐：余额 +5%\n• 推荐首次交易：+100 ₽",
        "req_menu": "✏️ 选择要更改收款信息的货币",
        "req_prompt": "✏️ 输入您的 {currency} 用于 {currency_name}\n\n📝 示例：\n{example}",
        "req_saved": "✅ 收款信息已保存。",
        "support_text": "🆘 支持：@GiftsForFunpay\n\n如有任何问题，请联系经理。",
        "about_text": "👋 详情：\n\n我们是担保服务，我们的任务是帮助您进行安全交易并快速提现！\n\n常见问题解答：\n\n• 提现需要多长时间？通常不超过2分钟，极少数情况下长达2小时。\n\n• 为什么礼物要交给经理而不是买家？原因很简单：买家可能会谎称没收到礼物，从而拖延时间，但我们的经理会自动检查 NFT 礼物是否存在，这样就无法欺骗了。\n\n• 充值速度有多快？充值也不超过2分钟。\n\n• 我看到一个类似的机器人，我能相信它吗？如果您看到除了 @FunpayTrustly_robot 之外的机器人，在任何情况下都不要与之进行交易！",
        "language_text": "🌐 选择语言：",
        "language_set": "✅ 语言已设置为：{lang}",
        "admin_only": "🚫 仅限管理员。",
        "banned": "🚫 您的账户已被冻结。",
        "active_limit": "❌ 最多 5 笔活跃交易。",
        "seller_not_found": "❌ 在机器人数据库中未找到此用户名的用户。",
        "cancelled_fsm": "✅ 当前操作已取消。",
        "stats": "📊 统计\n\n用户：{users}\n活跃：{active}\n已完成：{completed}\n已取消：{cancelled}\n总交易：{total}\n管理员日志：{logs}\n服务余额：{service}\n",
        "review_prompt": "⭐ 给交易对手评分 1 到 5：",
        "review_comment": "写一段简短评论或发送 '-'",
        "review_saved": "✅ 评价已保存。谢谢！",
        "admin_deals": "🛠 管理交易",
        "admin_done": "✅ 完成",
        "admin_cancel": "❌ 取消",
        "admin_req": "💳 更改收款信息",
        "admin_req_prompt": "输入新的卖家收款信息：",
        "admin_done_ok": "✅ 管理员已完成交易 #{deal_id}。",
        "admin_cancel_ok": "❌ 管理员已取消交易 #{deal_id}。",
        "admin_req_ok": "✅ 交易 #{deal_id} 的收款信息已更改。",
        "ban_ok": "🚫 用户 {id} 已被封禁。",
        "unban_ok": "✅ 用户 {id} 已被解封。",
        "invalid": "❌ 无效值。",
        "clear_history": "🗑️ 清除历史记录",
        "history_cleared": "✅ 交易历史已清除（已完成的交易已归档）。"
    },
    "hi": {
        "main": "🛡️ स्वागत है\n\n<b>FunPay</b> - हम ऑफ-एक्सचेंज लेन-देन में सुरक्षा सुनिश्चित करने के लिए विशेष सेवा हैं।\n\n• स्वचालित निष्पादन एल्गोरिदम।\n• गति और स्वचालन।\n• सुविधाजनक और तेज़ फंड निकासी।\n\n• सेवा कमीशन: <b>1%</b>\n• संचालन मोड: <b>24/7</b>\n• तकनीकी सहायता: @GiftsForFunpay\n\nनीचे आवश्यक अनुभाग चुनें",
        "create": "📝 डील बनाएं",
        "my_deals": "📋 मेरी डील्स",
        "req": "💳 भुगतान विवरण",
        "referral": "💠 रेफ़रल",
        "profile": "👤 प्रोफ़ाइल",
        "support": "🆘 सहायता",
        "about": "ℹ️ सेवा के बारे में",
        "back": "🔙 वापस",
        "language": "🌐 भाषा",
        "seller": "👤 मैं विक्रेता हूँ",
        "buyer": "🛒 मैं खरीदार हूँ",
        "account": "📦 खाता / सामान",
        "gift": "🎁 NFT Gift",
        "choose_role": "अपनी भूमिका चुनें:",
        "choose_type": "डील का प्रकार चुनें:",
        "description": "✍️ डील के विषय का वर्णन करें:\n\nउदाहरण: https://t.me/nft/PlushPepe-111\nया उत्पाद का सिर्फ टेक्स्ट विवरण",
        "currency": "💱 मुद्रा चुनें:",
        "amount": "💰 पूर्णांक राशि दर्ज करें:",
        "requisites": "💳 भुगतान प्राप्त करने के लिए विवरण दर्ज करें:",
        "seller_username": "👤 विक्रेता का @username दर्ज करें:",
        "deal_created": "✅ डील #<b>{deal_id}</b> सफलतापूर्वक बनाई गई!\n\n💵 मुद्रा: {currency}\n💰 राशि: {amount} {currency}\n🎁 NFT संख्या: 1\n\n📎 NFT लिंक:\n• {gift_link}\n\n🔗 खरीदार के लिए लिंक:\n{link}\n\n⏳ खरीदार के कनेक्ट होने की प्रतीक्षा करें।",
        "deal_created_buyer": "✅ डील #<b>{deal_id}</b> सफलतापूर्वक बनाई गई!\n\n💵 मुद्रा: {currency}\n💰 राशि: {amount} {currency}\n\n🔗 विक्रेता के लिए लिंक:\n{link}\n\n⏳ विक्रेता के कनेक्ट होने की प्रतीक्षा करें।",
        "joined": "✅ आप डील #{deal_id} में शामिल हो गए।",
        "already_member": "ℹ️ आप पहले से ही इस डील के सदस्य हैं।",
        "full": "ℹ️ दोनों भूमिकाएँ पहले से भरी हुई हैं।",
        "self_deal": "❌ आप अपनी खुद की डील में दूसरी भूमिका नहीं ले सकते।",
        "confirm": "✅ भागीदारी की पुष्टि करें",
        "cancel_deal": "❌ डील रद्द करें",
        "details": "🔎 विवरण",
        "cancelled": "❌ डील #{deal_id} रद्द कर दी गई।",
        "not_found": "🚫 डील नहीं मिली।",
        "not_allowed": "🚫 कार्रवाई की अनुमति नहीं है।",
        "confirmed": "💳 प्राथमिक भुगतान की पुष्टि हो गई\n\nडील: #{deal_id}\nविक्रेता: @{seller}\nरेटिंग: {rating}/5\nसफल डील्स: {successful}\nराशि: {amount} {currency}\nवस्तु: {description}\n\nमैनेजर @GiftsForFunpay को माल सौंपने की प्रतीक्षा करें।",
        "buyer_notify": "📩 विक्रेता ने डील #{deal_id} में भागीदारी की पुष्टि कर दी।\n\n💰 {amount} {currency}\n💳 विक्रेता का विवरण:\n{req}",
        "deal_active": "🟢 सक्रिय",
        "waiting_buyer": "🟡 खरीदार की प्रतीक्षा",
        "waiting_seller": "🟡 विक्रेता की प्रतीक्षा",
        "completed": "✅ पूर्ण",
        "cancelled_status": "❌ रद्द",
        "balance": "💰 <b>बैलेंस</b>\n\nउपलब्ध: <b>{balance}</b>\nफ्रोजन: <b>{frozen}</b>",
        "deposit": "➕ जमा करें",
        "withdraw": "➖ निकालें",
        "deposit_amount": "जमा राशि दर्ज करें:",
        "withdraw_amount": "निकासी राशि दर्ज करें:",
        "deposit_ok": "✅ बैलेंस {amount} बढ़ा दिया गया।",
        "withdraw_ok": "✅ {amount} निकाल लिया गया।",
        "not_enough": "❌ पर्याप्त फंड नहीं।",
        "positive": "❌ राशि शून्य से अधिक होनी चाहिए।",
        "my_deals_empty": "📭 आपके पास कोई डील नहीं है।",
        "my_deals_title": "📋 मेरी डील्स\n\n",
        "profile_text": "👤 प्रोफ़ाइल\n\nID: {id}\nUsername: @{username}\nडील्स: {deals}\nसफल: {successful}\nरेटिंग: {rating} ({reviews})\nरेफ़रल: {refs}\n",
        "referral_text": "💠 रेफ़रल प्रोग्राम\n━━━━━━━━━━━━━━━━━━━\n\n🔗 आपका लिंक:\n{link}\n\n━━━━━━━━━━━━━━━━━━━\n📊 सांख्यिकी:\n\n• कुल आमंत्रित: {total}\n• सक्रिय रेफ़रल: 0\n• कुल डील वॉल्यूम: 0.00 ₽\n\n━━━━━━━━━━━━━━━━━━━\n💰 आपके बोनस:\n\n• प्रत्येक सक्रिय रेफ़रल के लिए: बैलेंस में +5%\n• रेफ़रल की पहली डील पर: +100 ₽",
        "req_menu": "✏️ विवरण बदलने के लिए मुद्रा चुनें",
        "req_prompt": "✏️ {currency_name} के लिए अपना {currency} दर्ज करें\n\n📝 उदाहरण:\n{example}",
        "req_saved": "✅ विवरण सहेज लिया गया।",
        "support_text": "🆘 सहायता: @GiftsForFunpay\n\nकिसी भी प्रश्न के लिए प्रबंधक से संपर्क करें।",
        "about_text": "👋 विस्तार में:\n\nहम एक गारंटर सेवा हैं, हमारा काम आपको सुरक्षित डील करने और तेजी से निकासी करने में मदद करना है!\n\nअक्सर पूछे जाने वाले प्रश्न:\n\n• निकासी में कितना समय लगता है? आमतौर पर 2 मिनट से अधिक नहीं, दुर्लभ मामलों में 2 घंटे तक।\n\n• गिफ्ट मैनेजर को क्यों देना चाहिए, खरीदार को नहीं? कारण सरल है: खरीदार झूठ बोल सकता है कि उसे गिफ्ट नहीं मिला, जो स्थिति को लंबा खींचता है, लेकिन हमारा मैनेजर NFT गिफ्ट की उपस्थिति की स्वचालित रूप से जांच करता है और धोखा देना संभव नहीं होगा।\n\n• टॉप-अप कितनी तेजी से होता है? टॉप-अप भी 2 मिनट से अधिक नहीं लेता है।\n\n• मैंने एक समान बॉट देखा, क्या मुझे उस पर भरोसा करना चाहिए? यदि आप @FunpayTrustly_robot के अलावा कोई अन्य बॉट देखते हैं, तो किसी भी स्थिति में उसके साथ डील न करें!",
        "language_text": "🌐 भाषा चुनें:",
        "language_set": "✅ भाषा सेट की गई: {lang}",
        "admin_only": "🚫 केवल एडमिन।",
        "banned": "🚫 आपका खाता अवरुद्ध कर दिया गया है।",
        "active_limit": "❌ अधिकतम 5 सक्रिय डील।",
        "seller_not_found": "❌ इस username वाला उपयोगकर्ता बॉट डेटाबेस में नहीं मिला।",
        "cancelled_fsm": "✅ वर्तमान कार्रवाई रद्द कर दी गई।",
        "stats": "📊 सांख्यिकी\n\nउपयोगकर्ता: {users}\nसक्रिय: {active}\nपूर्ण: {completed}\nरद्द: {cancelled}\nकुल डील: {total}\nएडमिन लॉग्स: {logs}\nसेवा बैलेंस: {service}\n",
        "review_prompt": "⭐ समकक्ष को 1 से 5 तक रेट करें:",
        "review_comment": "एक छोटी टिप्पणी लिखें या '-' भेजें",
        "review_saved": "✅ समीक्षा सहेज ली गई। धन्यवाद!",
        "admin_deals": "🛠 डील प्रबंधित करें",
        "admin_done": "✅ पूर्ण करें",
        "admin_cancel": "❌ रद्द करें",
        "admin_req": "💳 विवरण बदलें",
        "admin_req_prompt": "विक्रेता का नया विवरण दर्ज करें:",
        "admin_done_ok": "✅ एडमिन द्वारा डील #{deal_id} पूर्ण की गई।",
        "admin_cancel_ok": "❌ एडमिन द्वारा डील #{deal_id} रद्द की गई।",
        "admin_req_ok": "✅ डील #{deal_id} का विवरण बदल दिया गया।",
        "ban_ok": "🚫 उपयोगकर्ता {id} अवरुद्ध कर दिया गया।",
        "unban_ok": "✅ उपयोगकर्ता {id} अनब्लॉक कर दिया गया।",
        "invalid": "❌ अमान्य मान।",
        "clear_history": "🗑️ इतिहास साफ़ करें",
        "history_cleared": "✅ डील इतिहास साफ़ कर दिया गया (पूर्ण की गई डील्स संग्रहीत कर दी गईं)।"
    }
}

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И КЛАВИАТУРЫ
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

async def safe_send(chat_id, text, markup=None):
    try:
        if PHOTO_URL:
            try:
                await bot.send_photo(chat_id, PHOTO_URL, caption=text, reply_markup=markup, parse_mode="HTML")
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
# KEYBOARDS
# ============================================================
def kb_main(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("create", lang), callback_data="create_deal")],
        [InlineKeyboardButton(text=tr("my_deals", lang), callback_data="my_deals"),
         InlineKeyboardButton(text=tr("req", lang), callback_data="requisites")],
        [InlineKeyboardButton(text=tr("referral", lang), callback_data="referral"),
         InlineKeyboardButton(text=tr("profile", lang), callback_data="profile")],
        [InlineKeyboardButton(text=tr("language", lang), callback_data="lang"),
         InlineKeyboardButton(text=tr("support", lang), callback_data="support")],
        [InlineKeyboardButton(text=tr("about", lang), callback_data="about")],
    ])

def kb_back(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]])

def kb_roles(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("seller", lang), callback_data="role_seller"), InlineKeyboardButton(text=tr("buyer", lang), callback_data="role_buyer")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ])

def kb_types(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("account", lang), callback_data="type_account"), InlineKeyboardButton(text=tr("gift", lang), callback_data="type_gift")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ])

def kb_currencies(lang, prefix):
    labels = [
        ("USDT", "💎 USDT"),
        ("RUB", "🇷🇺 RUB"), ("UAH", "🇺🇦 UAH"),
        ("BYN", "🇧🇾 BYN"), ("TON", "💎 TON"),
        ("STARS", "⭐ STARS"), ("KZT", "🇰🇿 KZT"),
    ]
    rows = []
    # USDT отдельно
    rows.append([InlineKeyboardButton(text=labels[0][1], callback_data=f"{prefix}{labels[0][0]}")])
    for i in range(1, len(labels), 2):
        pair = labels[i:i+2]
        row = [InlineKeyboardButton(text=pair[0][1], callback_data=f"{prefix}{pair[0][0]}")]
        if len(pair) > 1:
            row.append(InlineKeyboardButton(text=pair[1][1], callback_data=f"{prefix}{pair[1][0]}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_balance(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("deposit", lang), callback_data="deposit")],
        [InlineKeyboardButton(text=tr("withdraw", lang), callback_data="withdraw")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ])

def kb_my_deals(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr("clear_history", lang), callback_data="clear_history")],
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

    review_rating = State()
    review_comment = State()

    admin_news = State()
    admin_req = State()

# ============================================================
# ОБРАБОТЧИКИ
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
                    execute("INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)", (ref_id, uid, datetime.now(timezone.utc).isoformat()))
                    execute("UPDATE users SET ref_count=(SELECT COUNT(*) FROM referrals WHERE referrer_id=?) WHERE user_id=?", (ref_id, ref_id))
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
        execute("UPDATE deals SET seller_id=?,seller_username=? WHERE deal_id=?", (uid, username, deal_id))
    else:
        execute("UPDATE deals SET buyer_id=?,buyer_username=? WHERE deal_id=?", (uid, username, deal_id))
    deal = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
    if deal["seller_id"] and deal["buyer_id"]:
        execute("UPDATE deals SET status='active' WHERE deal_id=?", (deal_id,))
        status = "active"
    else:
        status = deal["status"]
    lang = user_lang(uid)
    await message.answer(tr("joined", lang).format(deal_id=deal_id))
    other_id = deal["buyer_id"] if role == "seller" else deal["seller_id"]
    if other_id:
        other_lang = user_lang(other_id)
        await notify(other_id, f"👤 @{username or uid} подключился к сделке #{deal_id}.\nСтатус: {status_text(status, other_lang)}")
    if status == "active":
        fresh = fetchone("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
        seller = fresh["seller_id"]
        buyer = fresh["buyer_id"]
        seller_lang = user_lang(seller)
        buyer_lang = user_lang(buyer)
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("confirm", seller_lang), callback_data=f"confirm_{deal_id}")],
            [InlineKeyboardButton(text=tr("cancel_deal", seller_lang), callback_data=f"cancel_{deal_id}")]
        ])
        await notify(seller, f"👥 Оба участника подключены к сделке #{deal_id}.\n💰 {fresh['amount']} {fresh['currency']}\n📝 {fresh['description']}", confirm_kb)
        await notify(buyer, f"👥 Оба участника подключены к сделке #{deal_id}.\nОжидается подтверждение продавца.")
    else:
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=tr("cancel_deal", lang), callback_data=f"cancel_{deal_id}")],
            [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
        ])
        await message.answer(f"📌 #{deal_id}\nСтатус: {status_text(status, lang)}", reply_markup=cancel_kb)

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
    seller_row = fetchone("SELECT rating, successful_deals FROM users WHERE user_id=?", (uid,))
    rating = seller_row["rating"] if seller_row else 0
    successful = seller_row["successful_deals"] if seller_row else 0
    await call.message.edit_text(
        tr("confirmed", seller_lang).format(
            deal_id=deal_id,
            seller=deal["seller_username"] or uid,
            rating=rating,
            successful=successful,
            amount=deal["amount"],
            currency=deal["currency"],
            description=deal["description"]
        ),
        parse_mode="HTML"
    )
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
    rows.append([InlineKeyboardButton(text=tr("back", lang), callback_data="my_deals")])
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
        text += f"#{d['deal_id']} | {d['deal_type']} | {d['amount']} {d['currency']} | {status_text(d['status'], lang)}\n"
        buttons.append([InlineKeyboardButton(text=f"🔎 #{d['deal_id']}", callback_data=f"dealview_{d['deal_id']}")])
    buttons.append([InlineKeyboardButton(text=tr("clear_history", lang), callback_data="clear_history")])
    buttons.append([InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")])
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

@dp.callback_query(F.data == "funds")
async def funds(call: CallbackQuery):
    uid = call.from_user.id
    lang = user_lang(uid)
    row = fetchone("SELECT balance,frozen_balance FROM users WHERE user_id=?", (uid,))
    await call.message.answer(tr("balance", lang).format(balance=row["balance"] if row else 0, frozen=row["frozen_balance"] if row else 0), reply_markup=kb_balance(lang))
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
    execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, message.from_user.id))
    await state.clear()
    await message.answer(tr("deposit_ok", user_lang(message.from_user.id)).format(amount=amount), reply_markup=kb_back(user_lang(message.from_user.id)))

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
    row = fetchone("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,))
    if not row or row["balance"] < amount:
        await message.answer(tr("not_enough", user_lang(message.from_user.id)))
        return
    execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, message.from_user.id))
    await state.clear()
    await message.answer(tr("withdraw_ok", user_lang(message.from_user.id)).format(amount=amount), reply_markup=kb_back(user_lang(message.from_user.id)))

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
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ]))
    await call.answer()

@dp.callback_query(F.data.startswith("setlang_"))
async def set_lang(call: CallbackQuery):
    lang = call.data.replace("setlang_", "")
    if lang not in T:
        await call.answer(tr("invalid", user_lang(call.from_user.id)), show_alert=True)
        return
    execute("UPDATE users SET lang=? WHERE user_id=?", (lang, call.from_user.id))
    await call.message.answer(tr("language_set", lang).format(lang=LANG_NAMES[lang]), reply_markup=kb_main(lang))
    await call.answer()

@dp.callback_query(F.data == "support")
async def support(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("support_text", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 @GiftsForFunpay", url="https://t.me/GiftsForFunpay")],
        [InlineKeyboardButton(text=tr("back", lang), callback_data="main_menu")]
    ]))
    await call.answer()

@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    lang = user_lang(call.from_user.id)
    await call.message.answer(tr("about_text", lang), reply_markup=kb_back(lang))
    await call.answer()

# ============================================================
# /novateam — теперь доступна всем (скрытая команда)
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
        for uid in (deal["seller_id"], deal["buyer_id"]):
            if uid:
                await notify(uid, tr("admin_done_ok", user_lang(uid)).format(deal_id=deal_id))
        await message.answer(tr("admin_done_ok", "ru").format(deal_id=deal_id))
        return
    rows = fetchall("SELECT deal_id FROM deals WHERE status='active' ORDER BY created_at DESC LIMIT 5")
    count = 0
    for row in rows:
        deal = complete_deal(row["deal_id"], message.from_user.id)
        if deal:
            count += 1
            for uid in (deal["seller_id"], deal["buyer_id"]):
                if uid:
                    await notify(uid, tr("admin_done_ok", user_lang(uid)).format(deal_id=row["deal_id"]))
    await message.answer(f"✅ Завершено последних сделок: {count}")

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
# ОСТАЛЬНЫЕ АДМИН-КОМАНДЫ (stats, ban, unban, admin панель) — остаются только для админов
# ============================================================
@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only", user_lang(message.from_user.id)))
        return
    users = fetchone("SELECT COUNT(*) c FROM users")["c"]
    active = fetchone("SELECT COUNT(*) c FROM deals WHERE status NOT IN ('completed','cancelled')")["c"]
    completed = fetchone("SELECT COUNT(*) c FROM deals WHERE status='completed'")["c"]
    cancelled = fetchone("SELECT COUNT(*) c FROM deals WHERE status='cancelled'")["c"]
    total = fetchone("SELECT COUNT(*) c FROM deals")["c"]
    logs = fetchone("SELECT COUNT(*) c FROM admin_logs")["c"]
    service = fetchone("SELECT balance FROM service_balance WHERE id=1")["balance"]
    await message.answer(tr("stats", "ru").format(users=users, active=active, completed=completed, cancelled=cancelled, total=total, logs=logs, service=service))

@dp.message(Command("ban"))
async def ban(message: Message):
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
async def unban(message: Message):
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
    news_id = execute("INSERT INTO news(admin_id,content,created_at) VALUES(?,?,?)", (message.from_user.id, content, now))
    users = fetchall("SELECT user_id FROM users WHERE banned=0")
    sent = 0
    for row in users:
        try:
            await bot.send_message(row["user_id"], f"📢 <b>Новость</b>\n\n{content}", parse_mode="HTML")
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
    rows = fetchall("SELECT deal_id,seller_username,buyer_username,amount,currency,status FROM deals WHERE status NOT IN ('completed','cancelled') ORDER BY created_at DESC LIMIT 30")
    if not rows:
        await call.message.answer("Активных сделок нет.", reply_markup=kb_back("ru"))
        await call.answer()
        return
    for row in rows:
        text = f"📌 #{row['deal_id']}\nПродавец: @{row['seller_username'] or '-'}\nПокупатель: @{row['buyer_username'] or '-'}\nСумма: {row['amount']} {row['currency']}\nСтатус: {row['status']}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Завершить", callback_data=f"adm_done_{row['deal_id']}")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data=f"adm_cancel_{row['deal_id']}")],
            [InlineKeyboardButton(text="💳 Изменить реквизиты", callback_data=f"adm_req_{row['deal_id']}")],
        ])
        await call.message.answer(text, reply_markup=kb)
    await call.answer()

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
            await notify(uid, tr("admin_done_ok", user_lang(uid)).format(deal_id=deal_id))
    if deal["seller_id"] and deal["buyer_id"]:
        for uid, target in ((deal["seller_id"], deal["buyer_id"]), (deal["buyer_id"], deal["seller_id"])):
            lang = user_lang(uid)
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr("review_prompt", lang), callback_data=f"review_{deal_id}_{target}")]])
            await notify(uid, "⭐ " + tr("review_prompt", lang), kb)
    await call.message.edit_text(tr("admin_done_ok", "ru").format(deal_id=deal_id))
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
    execute("UPDATE deals SET status='cancelled' WHERE deal_id=?", (deal_id,))
    admin_log(call.from_user.id, "cancel_deal", deal_id)
    for uid in (deal["seller_id"], deal["buyer_id"]):
        if uid:
            await notify(uid, tr("admin_cancel_ok", user_lang(uid)).format(deal_id=deal_id))
    await call.message.edit_text(tr("admin_cancel_ok", "ru").format(deal_id=deal_id))
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
                await notify(uid, tr("admin_req_ok", user_lang(uid)).format(deal_id=deal_id))
    await state.clear()
    await message.answer(tr("admin_req_ok").format(deal_id=deal_id))

@dp.message(Command("referral"))
async def referral_command(message: Message):
    ensure_user(message.from_user)
    uid = message.from_user.id
    count = fetchone("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,))["c"]
    await message.answer(f"🔗 Реферальная ссылка:\nhttps://t.me/{BOT_USERNAME}?start=ref{uid}\n\nПриглашено: {count}")

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(tr("admin_only"))
        return
    await message.answer("🛠 Админ-панель\n\n/stats — статистика\n/sendnews — рассылка\n/novateam [DEAL_ID] — завершить\n/ban USER_ID — блокировка\n/unban USER_ID — разблокировка")

# ============================================================
# GLOBAL ERROR HANDLER, WEBHOOK, MAIN LOOP
# ============================================================
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
