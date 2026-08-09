import os
import re
import uuid
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message

# ==================================================
# 1. НАСТРОЙКИ И ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ==================================================
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
BOT_USERNAME = os.getenv("BOT_USERNAME", "FunpayTrustly_robot")
PHOTO_URL = os.getenv("PHOTO_URL", "https://i.imgur.com/your_logo.jpg") # Замени на прямую ссылку на лого (заканчивающуюся на .jpg/.png)
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://ваш-сервер.onrender.com")

DB_NAME = "database.db"

# ==================================================
# 2. ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (SQLite)
# ==================================================
def init_db():
    with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                lang TEXT DEFAULT 'ru',
                card TEXT,
                crypto TEXT,
                stars_username TEXT,
                ref_count INTEGER DEFAULT 0,
                deals_count INTEGER DEFAULT 0,
                successful_deals INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                deal_id TEXT PRIMARY KEY,
                seller_id INTEGER,
                buyer_id INTEGER,
                deal_type TEXT,
                description TEXT,
                amount INTEGER,
                currency TEXT,
                seller_req TEXT,
                buyer_req TEXT,
                status TEXT,
                seller_username TEXT,
                buyer_username TEXT,
                created_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referred_id INTEGER,
                PRIMARY KEY (referrer_id, referred_id)
            )
        """)
        conn.commit()

# Инициализация БД при запуске
init_db()

# ==================================================
# 3. ПОЛНЫЙ СЛОВАРЬ ПЕРЕВОДОВ (RU, EN, ZH) - ЗВЕЗДЫ ИСПРАВЛЕНЫ
# ==================================================
LOCALES = {
    'ru': {
        'main_menu': "🛡️ FUNPAY\n\nБезопасный гарант для сделок в Telegram.\n\n📌 Что внутри:\n• защита от мошенников\n• удержание средств до завершения сделки\n• история и статусы сделок\n• поддержка через @GiftsforFunpay\n\n⬇️ Выберите действие ниже.",
        'create_deal_btn': "Создать сделку",
        'funds_btn': "Средства",
        'my_deals_btn': "Мои сделки",
        'requisites_btn': "Реквизиты",
        'lang_btn': "Язык",
        'support_btn': "Поддержка",
        'verify_btn': "Верификация",
        'referral_btn': "Рефералы",
        'about_btn': "О сервисе",
        'back': "🔙 Назад",
        'seller': "Я продавец",
        'buyer': "Я покупатель",
        'account': "Аккаунт",
        'gift': "NFT Gift",
        'card': "Карта",
        'crypto': "Крипта",
        'stars': "⭐Stars",  # <-- ИСПРАВЛЕНО
        'rub': "🇷🇺 Рубли",
        'uah': "🇺🇦 Гривны",
        'byn': "🇧🇾 BYN",
        'usdt': "💎 USDT",
        'ton': "💎 TON",
        'create_deal_msg': "Выберите вашу роль в сделке:",
        'seller_role': "Выберите тип сделки для продажи:",
        'buyer_role': "Выберите тип сделки для покупки:",
        'deal_type_account': "📝 Опишите предмет сделки.\n\nУкажите важные детали, условия передачи и дополнительные договоренности.",
        'deal_type_gift': "🎁 Отправьте ссылку на NFT Gift.\n\nМожно указать одну или несколько ссылок.",
        'payment_method': "💳 Выберите способ оплаты:",
        'amount': "💰 Введите сумму сделки в {currency}.\n\nТолько целое число.",
        'enter_amount': "Введите сумму сделки (целое число):",
        'enter_description': "📝 Введите подробное описание сделки:",
        'enter_requisites': "💳 Введите ваши реквизиты для получения оплаты:\n\n(Номер карты, адрес криптокошелька или username для Stars)",
        'enter_username': "👤 Введите @username продавца или покупателя.\n\nНапример: @seller",
        'deal_created': "✅ Сделка #{deal_id} создана!\n\n🔗 Ссылка для вашего контрагента:\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n📌 Статус: ожидаем подключения.",
        'deal_created_buyer': "✅ Сделка создана! Ожидаем подтверждения продавца.",
        'deal_show_seller': "📌 Сделка #{deal_id}\n\n📂 Тип: {deal_type}\n📝 Описание: {description}\n💰 Сумма: {amount} {currency}\n\n✅ Вы указаны как продавец. Подтвердите участие.",
        'deal_show_buyer': "✅ Вы подключены к сделке #{deal_id}.",
        'buyer_notify': "📩 ✅ Продавец подтвердил участие в сделке #{deal_id}!\n\n💰 Сумма: {amount} {currency}\n💳 Реквизиты для оплаты: {seller_req}",
        'deal_confirm_seller': "✅ Вы успешно подтвердили участие. Ожидайте оплаты от покупателя.",
        'deal_status_active': "активна",
        'deal_status_waiting': "ожидает",
        'deal_status_completed': "завершена",
        'error_own_deal': "❌ Ошибка: Вы являетесь создателем этой сделки. Перейти по собственной ссылке нельзя!",
        'error_own_ref': "❌ Нельзя перейти по своей собственной реферальной ссылке.",
        'no_deals': "📭 У вас нет активных сделок.",
        'novateam_seller': "💸 Оплата подтверждена.\n\n🛡 Передайте Подарок Менеджеру @GiftsForFunpay.",
        'novateam_buyer': "✅ Продавец подтвердил отправку. Сделка завершена!",
        'novateam_summary': "✅ Подтверждено и завершено {count} сделок.",
        'requisites_menu': "💳 Ваши реквизиты:\n\nВыберите тип для просмотра или изменения.",
        'requisites_saved': "✅ Реквизиты успешно сохранены!",
        'requisites_card': "💳 Введите номер банковской карты:",
        'requisites_crypto': "🪙 Введите адрес криптокошелька:",
        'requisites_stars': "⭐ Введите юзернейм для получения Stars\n\nНапример: @username",
        'support': "📞 Поддержка: @GiftsforFunpay\n\nПо всем вопросам обращайтесь к менеджеру.",
        'verify': "Верификация доступна пользователям с 30+ успешными сделками и оборотом от 1500 USDT.\n\nПреимущества:\n• автовывод средств\n• приоритетная поддержка\n• ускоренное решение спорных ситуаций\nПодайте заявку, и администрация рассмотрит ее.",
        'referral': "ваша реферальная ссылка\nhttps://t.me/{bot_username}?start=ref{user_id}",
        'about': "Всего сделок: 107107\nУспешных сделок: 103835\nОбщий объем: $1105228\nРейтинг: 4.9/5.0\nОнлайн: 15756\n\n🛡 Гарант-сервис\n✅ Проверенные продавцы\n📢 Поддержка 24/7",
        'lang_menu': "🌐 Выберите язык / Choose language / 选择语言:",
        'lang_set': "🌐 Язык установлен: {lang}",
        'funds_menu': "💳 Выберите действие:",
        'funds_deposit': "Введите ID сделки для оплаты",
        'funds_deposit_error': "🚫 Сделка не найдена.",
        'funds_withdraw': "Вывести деньги можно только от 2 сделок.\nУ Вас 0/2.",
        'my_deals_list': "📋 Ваши сделки:\n\n{deals}",
        'confirm_seller_btn': "✅ Подтвердить участие"
    },
    'en': {
        'main_menu': "🛡️ FUNPAY\n\nSecure guarantor for deals in Telegram.\n\n📌 What inside:\n• protection from scammers\n• funds holding until deal completion\n• deal history and statuses\n• support via @GiftsforFunpay\n\n⬇️ Select action below.",
        'create_deal_btn': "Create deal",
        'funds_btn': "Funds",
        'my_deals_btn': "My deals",
        'requisites_btn': "Requisites",
        'lang_btn': "Language",
        'support_btn': "Support",
        'verify_btn': "Verification",
        'referral_btn': "Referrals",
        'about_btn': "About",
        'back': "🔙 Back",
        'seller': "I am seller",
        'buyer': "I am buyer",
        'account': "Account",
        'gift': "NFT Gift",
        'card': "Card",
        'crypto': "Crypto",
        'stars': "⭐Stars",  # <-- ИСПРАВЛЕНО
        'rub': "🇷🇺 RUB",
        'uah': "🇺🇦 UAH",
        'byn': "🇧🇾 BYN",
        'usdt': "💎 USDT",
        'ton': "💎 TON",
        'create_deal_msg': "Choose your role in the deal:",
        'seller_role': "Choose deal type:",
        'buyer_role': "Choose deal type:",
        'deal_type_account': "📝 Describe the deal item.\n\nSpecify important details, transfer conditions and additional agreements.",
        'deal_type_gift': "🎁 Send NFT Gift link.\n\nYou can specify one or more links.",
        'payment_method': "💳 Choose payment method:",
        'amount': "💰 Enter deal amount in {currency}.\n\nInteger only.",
        'enter_amount': "Enter deal amount (integer):",
        'enter_description': "📝 Enter detailed description of the deal:",
        'enter_requisites': "💳 Enter your requisites for payment:\n\n(Card number, crypto wallet address, or username for Stars)",
        'enter_username': "👤 Enter @username of the counterparty.\n\nExample: @seller",
        'deal_created': "✅ Deal #{deal_id} created!\n\n🔗 Link for your counterparty:\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n📌 Status: waiting for connection.",
        'deal_created_buyer': "✅ Deal created! Waiting for seller confirmation.",
        'deal_show_seller': "📌 Deal #{deal_id}\n\n📂 Type: {deal_type}\n📝 Description: {description}\n💰 Amount: {amount} {currency}\n\n✅ You are listed as seller. Confirm participation.",
        'deal_show_buyer': "✅ You are connected to deal #{deal_id}.",
        'buyer_notify': "📩 ✅ Seller confirmed participation in deal #{deal_id}!\n\n💰 Amount: {amount} {currency}\n💳 Requisites for payment: {seller_req}",
        'deal_confirm_seller': "✅ You successfully confirmed participation. Waiting for buyer payment.",
        'deal_status_active': "active",
        'deal_status_waiting': "waiting",
        'deal_status_completed': "completed",
        'error_own_deal': "❌ Error: You are the creator of this deal. Cannot use your own link!",
        'error_own_ref': "❌ You cannot use your own referral link.",
        'no_deals': "📭 You have no active deals.",
        'novateam_seller': "💸 Payment confirmed.\n\n🛡 Transfer the Gift to Manager @GiftsForFunpay.",
        'novateam_buyer': "✅ Seller confirmed shipping. Deal completed!",
        'novateam_summary': "✅ Confirmed and completed {count} deals.",
        'requisites_menu': "💳 Your requisites:\n\nSelect type to view or change.",
        'requisites_saved': "✅ Requisites saved successfully!",
        'requisites_card': "💳 Enter your bank card number:",
        'requisites_crypto': "🪙 Enter your crypto wallet address:",
        'requisites_stars': "⭐ Enter your username for Stars\n\nExample: @username",
        'support': "📞 Support: @GiftsforFunpay\n\nContact manager for any questions.",
        'verify': "Verification is available to users with 30+ successful deals and turnover from 1500 USDT.\n\nAdvantages:\n• auto withdrawal\n• priority support\n• faster dispute resolution\nSubmit a request and the administration will review it.",
        'referral': "your referral link\nhttps://t.me/{bot_username}?start=ref{user_id}",
        'about': "Total deals: 107107\nSuccessful deals: 103835\nTotal volume: $1105228\nRating: 4.9/5.0\nOnline: 15756\n\n🛡 Guarantor service\n✅ Verified sellers\n📢 Support 24/7",
        'lang_menu': "🌐 Choose language / Выберите язык / 选择语言:",
        'lang_set': "🌐 Language set: {lang}",
        'funds_menu': "💳 Select action:",
        'funds_deposit': "Enter deal ID for payment",
        'funds_deposit_error': "🚫 Deal not found.",
        'funds_withdraw': "Withdrawal available from 2 deals.\nYou have 0/2.",
        'my_deals_list': "📋 Your deals:\n\n{deals}",
        'confirm_seller_btn': "✅ Confirm participation"
    },
    'zh': {
        'main_menu': "🛡️ FUNPAY\n\nTelegram 交易安全担保人。\n\n📌 内容：\n• 防止诈骗\n• 资金托管直至交易完成\n• 交易历史和状态\n• 通过 @GiftsforFunpay 获得支持\n\n⬇️ 选择以下操作。",
        'create_deal_btn': "创建交易",
        'funds_btn': "资金",
        'my_deals_btn': "我的交易",
        'requisites_btn': "收款信息",
        'lang_btn': "语言",
        'support_btn': "支持",
        'verify_btn': "认证",
        'referral_btn': "推荐",
        'about_btn': "关于服务",
        'back': "🔙 返回",
        'seller': "我是卖家",
        'buyer': "我是买家",
        'account': "账号",
        'gift': "NFT礼品",
        'card': "银行卡",
        'crypto': "加密货币",
        'stars': "⭐Stars",  # <-- ИСПРАВЛЕНО
        'rub': "🇷🇺 卢布",
        'uah': "🇺🇦 格里夫纳",
        'byn': "🇧🇾 白俄罗斯卢布",
        'usdt': "💎 USDT",
        'ton': "💎 TON",
        'create_deal_msg': "选择您在交易中的角色：",
        'seller_role': "选择交易类型：",
        'buyer_role': "选择交易类型：",
        'deal_type_account': "📝 描述交易物品。\n\n指明重要细节、转让条件和额外协议。",
        'deal_type_gift': "🎁 发送 NFT 礼品链接。\n\n您可以指定一个或多个链接。",
        'payment_method': "💳 选择支付方式：",
        'amount': "💰 输入交易金额（{currency}）。\n\n只能是整数。",
        'enter_amount': "输入交易金额（整数）：",
        'enter_description': "📝 输入交易的详细描述：",
        'enter_requisites': "💳 输入您的收款信息：\n\n（银行卡号、加密钱包地址或 Stars 用户名）",
        'enter_username': "👤 输入对方的 @username。\n\n例如：@seller",
        'deal_created': "✅ 交易 #{deal_id} 已创建！\n\n🔗 对方的链接：\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n📌 状态：等待连接。",
        'deal_created_buyer': "✅ 交易已创建！等待卖家确认。",
        'deal_show_seller': "📌 交易 #{deal_id}\n\n📂 类型：{deal_type}\n📝 描述：{description}\n💰 金额：{amount} {currency}\n\n✅ 您被列为卖家。请确认参与。",
        'deal_show_buyer': "✅ 您已连接到交易 #{deal_id}。",
        'buyer_notify': "📩 ✅ 卖家已确认参与交易 #{deal_id}！\n\n💰 金额：{amount} {currency}\n💳 收款信息：{seller_req}",
        'deal_confirm_seller': "✅ 您已成功确认参与。等待买家付款。",
        'deal_status_active': "活跃",
        'deal_status_waiting': "等待",
        'deal_status_completed': "已完成",
        'error_own_deal': "❌ 错误：您是该交易的创建者。不能使用您自己的链接！",
        'error_own_ref': "❌ 不能使用您自己的推荐链接。",
        'no_deals': "📭 您没有活跃的交易。",
        'novateam_seller': "💸 付款已确认。\n\n🛡 将礼品转交给管理员 @GiftsForFunpay。",
        'novateam_buyer': "✅ 卖家已确认发货。交易完成！",
        'novateam_summary': "✅ 已确认并完成 {count} 笔交易。",
        'requisites_menu': "💳 您的收款信息：\n\n选择类型查看或修改。",
        'requisites_saved': "✅ 收款信息保存成功！",
        'requisites_card': "💳 输入您的银行卡号：",
        'requisites_crypto': "🪙 输入您的加密钱包地址：",
        'requisites_stars': "⭐ 输入您的 Stars 用户名\n\n例如：@username",
        'support': "📞 支持：@GiftsforFunpay\n\n如有任何问题，请联系经理。",
        'verify': "拥有 30 笔以上成功交易且交易额超过 1500 USDT 的用户可获得认证。\n\n优势：\n• 自动提款\n• 优先支持\n• 加速解决争议\n提交申请，管理员将进行审核。",
        'referral': "您的推荐链接\nhttps://t.me/{bot_username}?start=ref{user_id}",
        'about': "总交易数：107107\n成功交易：103835\n总交易额：$1105228\n评分：4.9/5.0\n在线：15756\n\n🛡 担保服务\n✅ 已认证卖家\n📢 24/7 支持",
        'lang_menu': "🌐 选择语言 / Choose language / Выберите язык:",
        'lang_set': "🌐 语言已设置为：{lang}",
        'funds_menu': "💳 选择操作：",
        'funds_deposit': "输入要支付的交易 ID",
        'funds_deposit_error': "🚫 未找到交易。",
        'funds_withdraw': "提款需要至少 2 笔交易。\n您当前 0/2。",
        'my_deals_list': "📋 您的交易：\n\n{deals}",
        'confirm_seller_btn': "✅ 确认参与"
    }
}

# ==================================================
# 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БАЗЫ ДАННЫХ И ПЕРЕВОДОВ
# ==================================================
def tr(key, lang='ru', **kwargs):
    user_lang = lang if lang in LOCALES else 'ru'
    text = LOCALES[user_lang].get(key, LOCALES['ru'].get(key, key))
    try:
        return text.format(**kwargs)
    except Exception:
        return text

def get_user_lang(user_id):
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return row[0] if row else 'ru'
    except Exception:
        return 'ru'

# ==================================================
# 5. ГЕНЕРАЦИЯ КЛАВИАТУР (МЕНЮ)
# ==================================================
def get_main_keyboard(lang='ru'):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('create_deal_btn', lang), callback_data="create_deal"), InlineKeyboardButton(text=tr('funds_btn', lang), callback_data="funds")],
        [InlineKeyboardButton(text=tr('my_deals_btn', lang), callback_data="my_deals"), InlineKeyboardButton(text=tr('requisites_btn', lang), callback_data="requisites")],
        [InlineKeyboardButton(text=tr('lang_btn', lang), callback_data="lang"), InlineKeyboardButton(text=tr('support_btn', lang), callback_data="support")],
        [InlineKeyboardButton(text=tr('verify_btn', lang), callback_data="verify"), InlineKeyboardButton(text=tr('referral_btn', lang), callback_data="referral")],
        [InlineKeyboardButton(text=tr('about_btn', lang), callback_data="about")]
    ])

def get_back_button(lang='ru'):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])

def get_roles_keyboard(lang='ru'):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('seller', lang), callback_data="role_seller"), InlineKeyboardButton(text=tr('buyer', lang), callback_data="role_buyer")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])

def get_deal_types_keyboard(lang='ru'):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('account', lang), callback_data="sel_type_account"), InlineKeyboardButton(text=tr('gift', lang), callback_data="sel_type_gift")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])

def get_currencies_keyboard(prefix='sel_curr_', lang='ru'):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('rub', lang), callback_data=f"{prefix}rub"), InlineKeyboardButton(text=tr('uah', lang), callback_data=f"{prefix}uah")],
        [InlineKeyboardButton(text=tr('byn', lang), callback_data=f"{prefix}byn"), InlineKeyboardButton(text=tr('stars', lang), callback_data=f"{prefix}stars")],
        [InlineKeyboardButton(text=tr('usdt', lang), callback_data=f"{prefix}usdt"), InlineKeyboardButton(text=tr('ton', lang), callback_data=f"{prefix}ton")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])

# ==================================================
# 6. КРИТИЧНО ВАЖНАЯ ФУНКЦИЯ ОТПРАВКИ (ФОТО + ТЕКСТ РАЗДЕЛЬНО)
# ==================================================
async def send_safe_media(bot: Bot, chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    try:
        await bot.send_photo(chat_id=chat_id, photo=PHOTO_URL)
    except Exception as e:
        logging.warning(f"⚠️ Ошибка отправки фото (вероятно, блокировка региона): {e}")
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

# ==================================================
# 7. FSM (СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЯ)
# ==================================================
class DealStates(StatesGroup):
    seller_type = State()
    seller_description = State()
    seller_payment_method = State()
    seller_amount = State()
    seller_requisites = State()
    buyer_type = State()
    buyer_description = State()
    buyer_payment_method = State()
    buyer_amount = State()
    buyer_seller_username = State()
    profile_requisites_input = State()
    funds_deposit = State()

# ==================================================
# 8. ИНИЦИАЛИЗАЦИЯ БОТА И ДИСПЕТЧЕРА
# ==================================================
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ==================================================
# 9. ОБРАБОТЧИКИ КОМАНД И CALLBACK
# ==================================================

# --- Старт / Deep Link ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or ""
    
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
            cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка БД при старте: {e}")
        await message.answer("🚫 Внутренняя ошибка сервера.")
        return

    lang = get_user_lang(user_id)
    args = message.text.split()
    
    if len(args) > 1:
        param = args[1].strip()
        if param.startswith("deal_"):
            deal_id = param[5:].strip()
            try:
                with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT seller_id, buyer_id, seller_req, amount, currency, description, deal_type, status FROM deals WHERE deal_id = ?", (deal_id,))
                    deal = cursor.fetchone()
                    if not deal:
                        await message.answer("🚫 Сделка не найдена.")
                        return
                    seller_id, buyer_id, seller_req, amount, currency, description, deal_type, status = deal
                    if user_id == seller_id or user_id == buyer_id:
                        await message.answer(tr('error_own_deal', lang))
                        await send_safe_media(bot, message.chat.id, tr('main_menu', lang), get_main_keyboard(lang))
                        return
                    if buyer_id is None:
                        cursor.execute("UPDATE deals SET buyer_id = ?, buyer_username = ?, status = 'active' WHERE deal_id = ?", (user_id, username, deal_id))
                        conn.commit()
                        await message.answer(f"✅ Вы присоединились к сделке #{deal_id} как покупатель.")
                        if seller_id:
                            try:
                                await bot.send_message(seller_id, f"👤 Покупатель @{username} присоединился к сделке #{deal_id}.")
                            except Exception: pass
                    elif seller_id is None:
                        cursor.execute("UPDATE deals SET seller_id = ?, seller_username = ? WHERE deal_id = ?", (user_id, username, deal_id))
                        conn.commit()
                        await message.answer(f"✅ Вы стали продавцом в сделке #{deal_id}.")
                    elif user_id != seller_id and user_id != buyer_id:
                        await message.answer("ℹ️ У этой сделки уже есть покупатель и продавец.")
                        return
                    await show_deal(message, deal_id, user_id)
                    return
            except Exception as e:
                logging.error(f"Ошибка присоединения к сделке: {e}")
                await message.answer("🚫 Ошибка при присоединении.")
                return
        elif param.startswith("ref"):
            try:
                ref_id_str = param[3:].strip()
                if ref_id_str.isdigit():
                    ref_id = int(ref_id_str)
                    if ref_id == user_id:
                        await message.answer(tr('error_own_ref', lang))
                    else:
                        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
                            cursor = conn.cursor()
                            cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (ref_id, user_id))
                            cursor.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?", (ref_id,))
                            conn.commit()
                            await message.answer("✅ Вы были приглашены по реферальной ссылке!")
            except Exception as e:
                logging.error(f"Ошибка реферальной ссылки: {e}")

    await send_safe_media(bot, message.chat.id, tr('main_menu', lang), get_main_keyboard(lang))

# --- Главное меню (Назад) ---
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = get_user_lang(callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception: pass
    await send_safe_media(bot, callback.message.chat.id, tr('main_menu', lang), get_main_keyboard(lang))
    await callback.answer()

# --- Создание сделки ---
@dp.callback_query(F.data == "create_deal")
async def cb_create_deal(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('create_deal_msg', lang), reply_markup=get_roles_keyboard(lang))
    await callback.answer()

# --- Путь Продавца (FSM) ---
@dp.callback_query(F.data == "role_seller")
async def cb_role_seller(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.seller_type)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('seller_role', lang), reply_markup=get_deal_types_keyboard(lang))
    await callback.answer()

@dp.callback_query(F.data.startswith("sel_type_"), DealStates.seller_type)
async def cb_seller_type(callback: CallbackQuery, state: FSMContext):
    deal_type = callback.data.replace("sel_type_", "")
    await state.update_data(deal_type=deal_type)
    await state.set_state(DealStates.seller_description)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('enter_description', lang))
    await callback.answer()

@dp.message(DealStates.seller_description)
async def process_seller_desc(message: Message, state: FSMContext):
    if len(message.text) < 5:
        await message.answer("⚠️ Описание слишком короткое. Опишите сделку подробнее.")
        return
    await state.update_data(description=message.text)
    await state.set_state(DealStates.seller_payment_method)
    lang = get_user_lang(message.from_user.id)
    await message.answer(tr('payment_method', lang), reply_markup=get_currencies_keyboard('sel_curr_', lang))

@dp.callback_query(F.data.startswith("sel_curr_"), DealStates.seller_payment_method)
async def cb_seller_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.replace("sel_curr_", "")
    await state.update_data(currency=currency)
    await state.set_state(DealStates.seller_amount)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('enter_amount', lang))
    await callback.answer()

@dp.message(DealStates.seller_amount)
async def process_seller_amount(message: Message, state: FSMContext):
    if not re.fullmatch(r'\d+', message.text):
        lang = get_user_lang(message.from_user.id)
        await message.answer(tr('enter_amount', lang))
        return
    amount = int(message.text)
    if amount <= 0:
        await message.answer("⚠️ Сумма должна быть больше нуля.")
        return
    await state.update_data(amount=amount)
    await state.set_state(DealStates.seller_requisites)
    lang = get_user_lang(message.from_user.id)
    await message.answer(tr('enter_requisites', lang))

@dp.message(DealStates.seller_requisites)
async def process_seller_requisites(message: Message, state: FSMContext):
    if len(message.text.strip()) < 3:
        await message.answer("⚠️ Реквизиты не могут быть пустыми.")
        return
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or ""
    deal_id = str(uuid.uuid4())[:8]
    
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO deals (deal_id, seller_id, deal_type, description, amount, currency, seller_req, status, seller_username, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)
            """, (deal_id, user_id, data['deal_type'], data['description'], data['amount'], data['currency'], message.text, username, datetime.now(timezone.utc).isoformat()))
            cursor.execute("UPDATE users SET deals_count = deals_count + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка создания сделки: {e}")
        await message.answer("🚫 Ошибка при создании сделки. Попробуйте позже.")
        await state.clear()
        return

    await state.clear()
    lang = get_user_lang(user_id)
    await message.answer(tr('deal_created', lang, bot_username=BOT_USERNAME, deal_id=deal_id))

# --- Путь Покупателя (FSM) ---
@dp.callback_query(F.data == "role_buyer")
async def cb_role_buyer(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.buyer_type)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('buyer_role', lang), reply_markup=get_deal_types_keyboard(lang))
    await callback.answer()

@dp.callback_query(F.data.startswith("sel_type_"), DealStates.buyer_type)
async def cb_buyer_type(callback: CallbackQuery, state: FSMContext):
    deal_type = callback.data.replace("sel_type_", "")
    await state.update_data(deal_type=deal_type)
    await state.set_state(DealStates.buyer_description)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('enter_description', lang))
    await callback.answer()

@dp.message(DealStates.buyer_description)
async def process_buyer_desc(message: Message, state: FSMContext):
    if len(message.text) < 5:
        await message.answer("⚠️ Описание слишком короткое. Опишите сделку подробнее.")
        return
    await state.update_data(description=message.text)
    await state.set_state(DealStates.buyer_payment_method)
    lang = get_user_lang(message.from_user.id)
    await message.answer(tr('payment_method', lang), reply_markup=get_currencies_keyboard('buy_curr_', lang))

@dp.callback_query(F.data.startswith("buy_curr_"), DealStates.buyer_payment_method)
async def cb_buyer_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.replace("buy_curr_", "")
    await state.update_data(currency=currency)
    await state.set_state(DealStates.buyer_amount)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('enter_amount', lang))
    await callback.answer()

@dp.message(DealStates.buyer_amount)
async def process_buyer_amount(message: Message, state: FSMContext):
    if not re.fullmatch(r'\d+', message.text):
        lang = get_user_lang(message.from_user.id)
        await message.answer(tr('enter_amount', lang))
        return
    amount = int(message.text)
    if amount <= 0:
        await message.answer("⚠️ Сумма должна быть больше нуля.")
        return
    await state.update_data(amount=amount)
    await state.set_state(DealStates.buyer_seller_username)
    lang = get_user_lang(message.from_user.id)
    await message.answer(tr('enter_username', lang))

@dp.message(DealStates.buyer_seller_username)
async def process_buyer_username(message: Message, state: FSMContext):
    seller_username = message.text.strip()
    if not seller_username.startswith("@"):
        seller_username = "@" + seller_username
    
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or ""
    deal_id = str(uuid.uuid4())[:8]
    
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE username = ?", (seller_username[1:],))
            row = cursor.fetchone()
            seller_id = row[0] if row else None
            cursor.execute("""
                INSERT INTO deals (deal_id, buyer_id, seller_id, deal_type, description, amount, currency, status, buyer_username, seller_username, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?)
            """, (deal_id, user_id, seller_id, data['deal_type'], data['description'], data['amount'], data['currency'], username, seller_username, datetime.now(timezone.utc).isoformat()))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка создания сделки покупателем: {e}")
        await message.answer("🚫 Ошибка при создании сделки. Попробуйте позже.")
        await state.clear()
        return

    await state.clear()
    lang = get_user_lang(user_id)
    await message.answer(tr('deal_created_buyer', lang))
    if seller_id:
        try:
            await bot.send_message(seller_id, f"📦 Покупатель @{username} создал сделку #{deal_id}. Перейдите по ссылке для подтверждения:\nhttps://t.me/{BOT_USERNAME}?start=deal_{deal_id}")
        except Exception:
            pass

# ==================================================
# 10. ФУНКЦИЯ ОТОБРАЖЕНИЯ СДЕЛКИ
# ==================================================
async def show_deal(message: Message, deal_id: str, user_id: int):
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT deal_id, deal_type, description, amount, currency, status FROM deals WHERE deal_id = ?", (deal_id,))
            deal = cursor.fetchone()
            if not deal:
                await message.answer("🚫 Сделка не найдена.")
                return
            d_id, d_type, desc, amount, curr, status = deal
    except Exception as e:
        logging.error(f"Ошибка в show_deal: {e}")
        await message.answer("🚫 Ошибка при отображении сделки.")
        return

    lang = get_user_lang(user_id)
    text = tr('deal_show_seller', lang).format(deal_id=d_id, deal_type=d_type, description=desc, amount=amount, currency=curr)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('confirm_seller_btn', lang), callback_data=f"confirm_seller_{deal_id}")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await send_safe_media(bot, message.chat.id, text, keyboard)

# ==================================================
# 11. ПОДТВЕРЖДЕНИЕ ПРОДАВЦА (ЗАЩИТА ОТ ДУБЛЕЙ)
# ==================================================
@dp.callback_query(F.data.startswith("confirm_seller_"))
async def cb_confirm_seller(callback: CallbackQuery):
    deal_id = callback.data.replace("confirm_seller_", "")
    user_id = callback.from_user.id
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, buyer_id, seller_id, seller_req, amount, currency, description, deal_type FROM deals WHERE deal_id = ?", (deal_id,))
            deal = cursor.fetchone()
            if not deal:
                await callback.answer("🚫 Сделка не найдена.", show_alert=True)
                return
            status, buyer_id, seller_id, seller_req, amount, currency, description, deal_type = deal
            
            if status in ['active', 'completed']:
                await callback.answer("⛔ Вы уже подтвердили участие в этой сделке!", show_alert=True)
                return
            
            if user_id != seller_id:
                await callback.answer("⛔ Вы не являетесь продавцом в этой сделке.", show_alert=True)
                return
            
            cursor.execute("UPDATE deals SET status = 'active' WHERE deal_id = ?", (deal_id,))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка подтверждения сделки: {e}")
        await callback.answer("🚫 Ошибка.", show_alert=True)
        return

    # Уведомление покупателя
    try:
        lang = get_user_lang(buyer_id)
        await bot.send_message(buyer_id, tr('buyer_notify', lang).format(deal_id=deal_id, amount=amount, currency=currency, seller_req=seller_req or "Не указаны"), parse_mode="HTML")
    except Exception:
        pass

    seller_lang = get_user_lang(user_id)
    await callback.message.edit_text(tr('deal_confirm_seller', seller_lang), parse_mode="HTML")
    await callback.answer("✅ Успешно!", show_alert=False)

# ==================================================
# 12. СЕКРЕТНАЯ КОМАНДА /novateam
# ==================================================
@dp.message(Command("novateam"))
async def cmd_novateam(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT deal_id, seller_id, buyer_id, seller_username, buyer_username, amount, currency, description, deal_type FROM deals WHERE (seller_id = ? OR buyer_id = ?) AND status != 'completed'", (user_id, user_id))
            deals = cursor.fetchall()
            if not deals:
                await message.answer(tr('no_deals', get_user_lang(user_id)))
                return
            
            count = 0
            for deal_id, s_id, b_id, s_uname, b_uname, amount, currency, description, deal_type in deals:
                if user_id == b_id: # Нажал покупатель
                    if s_id:
                        s_lang = get_user_lang(s_id)
                        await bot.send_message(s_id, tr('novateam_seller', s_lang).format(deal_id=deal_id, buyer=username, amount=amount, currency=currency, description=description), parse_mode="HTML")
                elif user_id == s_id: # Нажал продавец
                    if b_id:
                        b_lang = get_user_lang(b_id)
                        await bot.send_message(b_id, tr('novateam_buyer', b_lang).format(deal_id=deal_id), parse_mode="HTML")
                
                cursor.execute("UPDATE deals SET status = 'completed' WHERE deal_id = ?", (deal_id,))
                cursor.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (user_id,))
                count += 1
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка в novateam: {e}")
        await message.answer("🚫 Ошибка при завершении сделок.")
        return
    
    lang = get_user_lang(user_id)
    await message.answer(tr('novateam_summary', lang).format(count=count))

# ==================================================
# 13. ОБРАБОТЧИКИ МЕНЮ (Поддержка, Верификация, О сервисе, Рефералы)
# ==================================================
@dp.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Написать в поддержку", url="https://t.me/GiftsforFunpay")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await callback.message.answer(tr('support', lang), reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "verify")
async def cb_verify(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Подать заявку", url="https://t.me/GiftsforFunpay")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await send_safe_media(bot, callback.message.chat.id, tr('verify', lang), kb)
    await callback.answer()

@dp.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await send_safe_media(bot, callback.message.chat.id, tr('about', lang), kb)
    await callback.answer()

@dp.callback_query(F.data == "referral")
async def cb_referral(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    text = tr('referral', lang, bot_username=BOT_USERNAME, user_id=user_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()

# ==================================================
# 14. ОБРАБОТЧИКИ МЕНЮ (Язык, Мои сделки, Реквизиты, Средства)
# ==================================================
@dp.callback_query(F.data == "lang")
async def cb_lang(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="setlang_en")],
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="setlang_zh")],
        [InlineKeyboardButton(text=tr('back', 'ru'), callback_data="main_menu")]
    ])
    await callback.message.answer("🌐 Выберите язык / Choose language / 选择语言:", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("setlang_"))
async def cb_set_language(callback: CallbackQuery):
    lang = callback.data.replace("setlang_", "")
    user_id = callback.from_user.id
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
            conn.commit()
    except Exception:
        await callback.answer("🚫 Ошибка смены языка.")
        return
    await callback.message.answer(tr('lang_set', lang).format(lang=lang))
    await send_safe_media(bot, callback.message.chat.id, tr('main_menu', lang), get_main_keyboard(lang))
    await callback.answer()

@dp.callback_query(F.data == "my_deals")
async def cb_my_deals(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT deal_id, deal_type, description, amount, currency, status FROM deals WHERE seller_id = ? OR buyer_id = ?", (user_id, user_id))
            deals = cursor.fetchall()
    except Exception as e:
        logging.error(f"Ошибка my_deals: {e}")
        await callback.message.answer("🚫 Ошибка загрузки сделок.")
        await callback.answer()
        return
    
    if not deals:
        await callback.message.answer(tr('no_deals', lang))
        await callback.answer()
        return
    
    text = "📋 Ваши сделки:\n\n"
    for d in deals:
        text += f"ID: {d[0]}\nТип: {d[1]}\nСумма: {d[3]} {d[4]}\nСтатус: {d[5]}\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "requisites")
async def cb_requisites(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('card', lang), callback_data="req_card"), InlineKeyboardButton(text=tr('crypto', lang), callback_data="req_crypto")],
        [InlineKeyboardButton(text=tr('stars', lang), callback_data="req_stars")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await callback.message.answer(tr('requisites_menu', lang), reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data.startswith("req_"))
async def cb_requisites_edit(callback: CallbackQuery, state: FSMContext):
    req_type = callback.data.replace("req_", "")
    await state.update_data(req_type=req_type)
    await state.set_state(DealStates.profile_requisites_input)
    lang = get_user_lang(callback.from_user.id)
    texts = {'card': 'requisites_card', 'crypto': 'requisites_crypto', 'stars': 'requisites_stars'}
    await callback.message.answer(tr(texts.get(req_type, 'requisites_card'), lang))
    await callback.answer()

@dp.message(DealStates.profile_requisites_input)
async def process_requisites_input(message: Message, state: FSMContext):
    data = await state.get_data()
    req_type = data.get('req_type')
    user_id = message.from_user.id
    val = message.text.strip()
    if len(val) < 3:
        await message.answer("⚠️ Введите корректные данные.")
        return
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            if req_type == 'card':
                cursor.execute("UPDATE users SET card = ? WHERE user_id = ?", (val, user_id))
            elif req_type == 'crypto':
                cursor.execute("UPDATE users SET crypto = ? WHERE user_id = ?", (val, user_id))
            elif req_type == 'stars':
                cursor.execute("UPDATE users SET stars_username = ? WHERE user_id = ?", (val, user_id))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка сохранения реквизитов: {e}")
        await message.answer("🚫 Ошибка сохранения.")
        await state.clear()
        return
    await state.clear()
    lang = get_user_lang(user_id)
    await message.answer(tr('requisites_saved', lang))

@dp.callback_query(F.data == "funds")
async def cb_funds(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Пополнить", callback_data="funds_deposit")],
        [InlineKeyboardButton(text="💸 Вывести", callback_data="funds_withdraw")],
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await callback.message.answer(tr('funds_menu', lang), reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "funds_deposit")
async def cb_funds_deposit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.funds_deposit)
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('funds_deposit', lang))
    await callback.answer()

@dp.message(DealStates.funds_deposit)
async def process_funds_deposit(message: Message, state: FSMContext):
    deal_id = message.text.strip()
    user_id = message.from_user.id
    try:
        with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT seller_id, buyer_id, status FROM deals WHERE deal_id = ?", (deal_id,))
            row = cursor.fetchone()
            if not row:
                await message.answer(tr('funds_deposit_error', 'ru'))
                await state.clear()
                return
            seller_id, buyer_id, status = row
            if user_id not in (seller_id, buyer_id):
                await message.answer("🚫 Вы не участник этой сделки.")
                await state.clear()
                return
            if status != 'active':
                await message.answer("🚫 Сделка не активна или уже завершена.")
                await state.clear()
                return
            await message.answer("✅ Оплата по сделке принята (имитация). Ожидайте подтверждения.")
            if user_id != seller_id:
                try:
                    await bot.send_message(seller_id, f"💳 Покупатель оплатил сделку #{deal_id}. Передайте товар.")
                except Exception:
                    pass
    except Exception as e:
        logging.error(f"Ошибка депозита: {e}")
        await message.answer("🚫 Ошибка обработки.")
    await state.clear()

@dp.callback_query(F.data == "funds_withdraw")
async def cb_funds_withdraw(callback: CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer(tr('funds_withdraw', lang))
    await callback.answer()

# ==================================================
# 15. WEBHOOK И ЗАПУСК (AIOHTTP)
# ==================================================
async def handle_root(request):
    return web.Response(text="Bot is running")

async def webhook_handler(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logging.error(f"Error handling webhook: {e}")
    return web.Response()

async def main():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_post("/", webhook_handler)
    
    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
