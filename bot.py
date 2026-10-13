import asyncio
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, ADMIN_IDS, CHECK_INTERVAL
from database import (
    init_db, add_user, set_user_active, get_stats, get_users_count,
    set_premium, get_today_signals_count, get_stats_period, get_today_signals
)
from services.sender import check_and_send

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def set_commands():
    await bot.set_my_commands(
        commands=[BotCommand(command="start", description="Запуск бота")],
        scope=BotCommandScopeDefault()
    )
    for admin_id in ADMIN_IDS:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Запуск бота"),
                BotCommand(command="admin", description="Админ-панель"),
                BotCommand(command="today", description="Сигналы за сегодня"),
                BotCommand(command="premium", description="Выдать премиум"),
                BotCommand(command="unpremium", description="Снять премиум")
            ],
            scope=BotCommandScopeChat(chat_id=admin_id)
        )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await add_user(message.from_user.id, message.from_user.username)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Включить сигналы", callback_data="signals_on")],
        [InlineKeyboardButton(text="❌ Выключить", callback_data="signals_off")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📅 Статистика по периодам", callback_data="stats_periods")],
        [InlineKeyboardButton(text="📅 Сводка за сегодня", callback_data="today_summary")]
    ])
    await message.answer(
        "⚽️ <b>Football Edge Bot</b>\n\n"
        "Бот автоматически ищет value-ставки на футбол.\n\n"
        "Нажми кнопку, чтобы начать.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "signals_on")
async def signals_on(callback: types.CallbackQuery):
    await set_user_active(callback.from_user.id, True)
    await callback.answer("Сигналы включены")
    await callback.message.answer("✅ Теперь ты будешь получать сигналы.")

@dp.callback_query(F.data == "signals_off")
async def signals_off(callback: types.CallbackQuery):
    await set_user_active(callback.from_user.id, False)
    await callback.answer("Сигналы выключены")
    await callback.message.answer("⏸ Рассылка остановлена.")

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    total, wins, loses, pending = await get_stats()
    winrate = (wins / (wins + loses) * 100) if (wins + loses) > 0 else 0
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"Всего сигналов: <b>{total}</b>\n"
        f"✅ Побед: <b>{wins}</b>\n"
        f"❌ Поражений: <b>{loses}</b>\n"
        f"⏳ В ожидании: <b>{pending}</b>\n\n"
        f"Winrate: <b>{winrate:.1f}%</b>"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "stats_periods")
async def stats_periods(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="7 дней", callback_data="period_7")],
        [InlineKeyboardButton(text="14 дней", callback_data="period_14")],
        [InlineKeyboardButton(text="30 дней", callback_data="period_30")]
    ])
    await callback.message.answer("📅 Выбери период:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("period_"))
async def show_period_stats(callback: types.CallbackQuery):
    days = int(callback.data.split("_")[1])
    total, wins, loses = await get_stats_period(days)
    winrate = (wins / (wins + loses) * 100) if (wins + loses) > 0 else 0
    text = (
        f"📊 <b>Статистика за {days} дней</b>\n\n"
        f"Всего: <b>{total}</b>\n"
        f"✅ Побед: <b>{wins}</b>\n"
        f"❌ Поражений: <b>{loses}</b>\n"
        f"Winrate: <b>{winrate:.1f}%</b>"
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "today_summary")
async def today_summary(callback: types.CallbackQuery):
    signals = await get_today_signals()
    if not signals:
        await callback.message.answer("Сегодня сигналов ещё не было.")
        await callback.answer()
        return
    
    text = "📊 <b>Сводка за сегодня</b>\n\n"
    profit = 0.0
    
    for match, market, odds, result in signals:
        if result == "win":
            text += f"✅ {market} @ {odds:.2f} — {match}\n"
            profit += odds - 1
        elif result == "lose":
            text += f"❌ {market} @ {odds:.2f} — {match}\n"
            profit -= 1
        else:
            text += f"⏳ {market} @ {odds:.2f} — {match}\n"
    
    text += f"\n💰 Флэт за день: <b>{profit:+.2f}</b> ед."
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.message(Command("today"))
async def today_stats(message: types.Message):
    count = await get_today_signals_count()
    await message.answer(f"📊 <b>Сигналов сегодня:</b> {count} / 30", parse_mode="HTML")

@dp.message(Command("admin"))
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
        f"✅ {wins} | ❌ {loses} | ⏳ {pending}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("premium"))
async def give_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, True)
        await message.answer(f"✅ Premium выдан пользователю {uid}")
    except:
        await message.answer("Использование: /premium 123456789")

@dp.message(Command("unpremium"))
async def remove_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, False)
        await message.answer(f"Premium снят с {uid}")
    except:
        await message.answer("Использование: /unpremium 123456789")

async def on_startup():
    await init_db()
    await set_commands()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send, "interval", minutes=CHECK_INTERVAL, args=[bot])
    scheduler.start()
    print(f"Планировщик запущен, интервал: {CHECK_INTERVAL} мин")
    print("Football Edge Bot запущен")

async def main():
    await on_startup()
    await asyncio.sleep(2)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
