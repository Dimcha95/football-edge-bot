import asyncio
import aiosqlite
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

from config import BOT_TOKEN, ADMIN_IDS, CHECK_INTERVAL
from database import (
    init_db, add_user, set_user_active, get_stats, 
    get_users_count, set_premium, get_user_status
)
from services.sender import check_and_send, update_results

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def set_commands():
    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="Запуск бота")
        ],
        scope=BotCommandScopeDefault()
    )
    for admin_id in ADMIN_IDS:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Запуск бота"),
                BotCommand(command="admin", description="Админ-панель"),
                BotCommand(command="premium", description="Выдать премиум"),
                BotCommand(command="unpremium", description="Снять премиум")
            ],
            scope=BotCommandScopeChat(chat_id=admin_id)
        )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await add_user(message.from_user.id, message.from_user.username)
    
    status, days_left = await get_user_status(message.from_user.id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📅 Статистика по периодам", callback_data="period_stats")],
        [InlineKeyboardButton(text="💎 Мой статус", callback_data="my_status")],
        [InlineKeyboardButton(text="❓ Как получить премиум", callback_data="premium_info")]
    ])
    
    text = "⚽️ Football Edge Bot\n\n"
    if status == "premium":
        text += "✅ У вас Premium"
    elif status == "trial":
        text += f"🆓 Пробный период: осталось {days_left} дн."
    else:
        text += "❌ Пробный период закончился"
    
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    stats = await get_stats()
    
    async with aiosqlite.connect("edge_bot.db") as db:
        cursor = await db.execute("""
            SELECT match_name, market, odds, edge, result 
            FROM signals 
            ORDER BY created_at DESC 
            LIMIT 10
        """)
        signals = await cursor.fetchall()
    
    text = (
        f"📊 <b>Эффективность</b>\n"
        f"Проходимость {stats['winrate']:.0f}% · сыграно {stats['wins'] + stats['losses']}\n"
        f"✅ {stats['wins']}  ❌ {stats['losses']}  ⏳ {stats['pending']}\n\n"
        f"<b>Результаты:</b>\n"
    )
    
    for match_name, market, odds, edge, result in signals:
        icon = "⏳" if result == "pending" else ("✅" if result == "win" else "❌")
        text += f"{icon} {match_name}\n{market} @ {odds:.2f} · edge +{edge*100:.1f}%\n\n"
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_status")
async def my_status(callback: types.CallbackQuery):
    status, days_left = await get_user_status(callback.from_user.id)
    if status == "premium":
        text = "✅ У вас активен Premium"
    elif status == "trial":
        text = f"🆓 Пробный период\nОсталось дней: {days_left}"
    else:
        text = "❌ Пробный период закончился\nНужен Premium"
    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "premium_info")
async def premium_info(callback: types.CallbackQuery):
    text = (
        "💎 <b>Как получить Premium</b>\n\n"
        "Premium даёт доступ к сигналам после пробного периода.\n\n"
        "Для получения напишите администратору."
    )
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    count = await get_users_count()
    await message.answer(f"👥 Пользователей: {count}")

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
    scheduler.add_job(update_results, "interval", minutes=15)
    scheduler.start()
    print("Football Edge Bot запущен")

async def health_check(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()
    print("Web server started on port 10000")

async def main():
    await on_startup()
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
