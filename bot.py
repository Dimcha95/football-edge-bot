import asyncio
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, ADMIN_IDS, CHECK_INTERVAL
from database import init_db, add_user, set_user_active, get_stats, get_users_count, set_premium
from services.sender import check_and_send

asyncio.set_event_loop(asyncio.new_event_loop())

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

def is_admin(user_id):
    return user_id in ADMIN_IDS

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await add_user(message.from_user.id, message.from_user.username)

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✅ Включить сигналы", callback_data="signals_on"),
        InlineKeyboardButton("❌ Выключить", callback_data="signals_off"),
        InlineKeyboardButton("📊 Статистика", callback_data="stats")
    )

    await message.answer(
        "⚽️ <b>Football Edge Bot</b>\n\n"
        "Бот автоматически ищет value-ставки на футбол.\n\n"
        "• Сканирует коэффициенты\n"
        "• Считает вероятность\n"
        "• Присылает только сигналы с edge\n\n"
        "Нажми кнопку, чтобы начать.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query_handler(lambda c: c.data == "signals_on")
async def signals_on(callback: types.CallbackQuery):
    await set_user_active(callback.from_user.id, True)
    await callback.answer("Сигналы включены")
    await bot.send_message(callback.from_user.id, "✅ Теперь ты будешь получать сигналы.")

@dp.callback_query_handler(lambda c: c.data == "signals_off")
async def signals_off(callback: types.CallbackQuery):
    await set_user_active(callback.from_user.id, False)
    await callback.answer("Сигналы выключены")
    await bot.send_message(callback.from_user.id, "⏸ Рассылка остановлена.")

@dp.callback_query_handler(lambda c: c.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    total, wins, loses, pending = await get_stats()
    winrate = 0
    if wins + loses > 0:
        winrate = wins / (wins + loses) * 100

    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"Всего сигналов: <b>{total}</b>\n"
        f"✅ Побед: <b>{wins}</b>\n"
        f"❌ Поражений: <b>{loses}</b>\n"
        f"⏳ В ожидании: <b>{pending}</b>\n\n"
        f"Winrate: <b>{winrate:.1f}%</b>"
    )
    await bot.send_message(callback.from_user.id, text, parse_mode="HTML")
    await callback.answer()

@dp.message_handler(commands=["admin"])
async def admin_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    total, active, premium = await get_users_count()
    total_s, wins, loses, pending = await get_stats()

    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователи: {total}\n"
        f"🟢 Активных: {active}\n"
        f"⭐ Premium: {premium}\n\n"
        f"📊 Сигналов: {total_s}\n"
        f"✅ {wins} | ❌ {loses} | ⏳ {pending}\n\n"
        "Команды:\n"
        "/premium ID — выдать премиум\n"
        "/unpremium ID — забрать премиум"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message_handler(commands=["premium"])
async def give_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, True)
        await message.answer(f"✅ Premium выдан пользователю {uid}")
    except:
        await message.answer("Использование: /premium 123456789")

@dp.message_handler(commands=["unpremium"])
async def remove_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, False)
        await message.answer(f"Premium снят с {uid}")
    except:
        await message.answer("Использование: /unpremium 123456789")

async def on_startup(dp):
    await init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send, "interval", minutes=CHECK_INTERVAL, args=[bot])
    scheduler.start()
    print("Football Edge Bot запущен")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)