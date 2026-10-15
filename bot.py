import asyncio
from dotenv import load_dotenv
load_dotenv()
from aiogram import Bot, Dispatcher, types
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web
from config import BOT_TOKEN, ADMIN_IDS, CHECK_INTERVAL
from database import (
    init_db, add_user, set_user_active, get_stats, get_users_count,
    set_premium, get_user_status, get_today_signals_count
)
from services.sender import check_and_send

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def set_commands():
    await bot.set_my_commands(
        commands=[BotCommand(command="start", description="Start")],
        scope=BotCommandScopeDefault()
    )
    for admin_id in ADMIN_IDS:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Start"),
                BotCommand(command="admin", description="Admin"),
                BotCommand(command="premium", description="Premium"),
                BotCommand(command="unpremium", description="Unpremium"),
                BotCommand(command="today", description="Today")
            ],
            scope=BotCommandScopeChat(chat_id=admin_id)
        )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await add_user(message.from_user.id, message.from_user.username)
    await set_user_active(message.from_user.id, True)
    await message.answer("Bot started. Waiting for signals.")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    users = await get_users_count()
    stats = await get_stats()
    today = await get_today_signals_count()
    await message.answer(f"Users: {users}\nToday signals: {today}\nStats: {stats}")

@dp.message(Command("premium"))
async def give_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, True)
        await message.answer(f"Premium given to {uid}")
    except:
        await message.answer("Usage: /premium 123456789")

@dp.message(Command("unpremium"))
async def remove_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, False)
        await message.answer(f"Premium removed from {uid}")
    except:
        await message.answer("Usage: /unpremium 123456789")

@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    count = await get_today_signals_count()
    await message.answer(f"Signals today: {count}")

async def on_startup():
    await init_db()
    await set_commands()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_send, "interval", minutes=CHECK_INTERVAL, args=[bot])
    scheduler.start()
    print("Bot started")

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
    print("Web server started")

async def main():
    await on_startup()
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
