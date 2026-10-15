```python
import asyncio
import logging

from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

from config import BOT_TOKEN, ADMIN_IDS, CHECK_INTERVAL
from database import (
    init_db,
    add_user,
    set_user_active,
    get_stats,
    get_users_count,
    set_premium,
    get_user_status,
    get_today_signals_count,
    get_recent_results,
)
from services.sender import check_and_send
from services.results import check_results


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="UTC")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Включить сигналы", callback_data="on")],
        [InlineKeyboardButton(text="❌ Выключить", callback_data="off")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📅 Статистика по периодам", callback_data="periods")],
        [InlineKeyboardButton(text="📋 Сводка за сегодня", callback_data="today")],
    ])


async def set_commands():
    await bot.set_my_commands(
        [BotCommand(command="start", description="Запуск")],
        scope=BotCommandScopeDefault(),
    )

    for admin_id in ADMIN_IDS:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Запуск"),
                BotCommand(command="admin", description="Админ"),
                BotCommand(command="premium", description="Премиум"),
                BotCommand(command="unpremium", description="Снять премиум"),
                BotCommand(command="today", description="Сегодня"),
            ],
            scope=BotCommandScopeChat(chat_id=admin_id),
        )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await add_user(message.from_user.id, message.from_user.username)
    await set_user_active(message.from_user.id, True)

    status, days_left = await get_user_status(message.from_user.id)

    if status == "premium":
        status_text = "Премиум"
    else:
        status_text = (
            f"Пробный период ({days_left} дн.)"
            if days_left is not None
            else "Пробный период"
        )

    await message.answer(
        f"⚽️ <b>Football Edge Bot</b>\n\n"
        f"Бот автоматически ищет value-ставки на футбол.\n"
        f"Статус: {status_text}\n\n"
        f"Нажми кнопку, чтобы начать.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "on")
async def turn_on(callback: types.CallbackQuery):
    await set_user_active(callback.from_user.id, True)
    await callback.answer("Сигналы включены")
    await callback.message.answer("Сигналы включены ✅")


@dp.callback_query(F.data == "off")
async def turn_off(callback: types.CallbackQuery):
    await set_user_active(callback.from_user.id, False)
    await callback.answer("Сигналы выключены")
    await callback.message.answer("Сигналы выключены ❌")


@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    stats = await get_stats()
    results = await get_recent_results(15)

    text = (
        f"📊 <b>Эффективность</b>\n"
        f"Проходимость <b>{stats['winrate']:.0f}%</b> · "
        f"рассчитано {stats['wins'] + stats['losses']}\n"
        f"✅ {stats['wins']}  ❌ {stats['losses']}  "
        f"↩️ {stats.get('pushes', 0)}  ⏳ {stats['pending']}\n\n"
        f"<b>Результаты:</b>\n"
    )

    if results:
        for match_name, market, odds, edge, result in results:
            icon = {
                "win": "✅",
                "loss": "❌",
                "push": "↩️",
            }.get(result, "⏳")
            text += (
                f"{icon} {match_name}\n"
                f"{market} @ {odds:.2f} · edge {edge * 100:+.1f}%\n\n"
            )
    else:
        text += "Пока нет завершённых сигналов."

    await callback.message.answer(text, parse_mode="HTML")


@dp.callback_query(F.data == "today")
async def show_today(callback: types.CallbackQuery):
    count = await get_today_signals_count()
    await callback.message.answer(f"📋 Сегодня сигналов: {count}")


@dp.callback_query(F.data == "periods")
async def show_periods(callback: types.CallbackQuery):
    await callback.message.answer(
        "📅 Статистика по периодам пока в разработке."
    )


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    users = await get_users_count()
    stats = await get_stats()
    today = await get_today_signals_count()

    await message.answer(
        f"Пользователей: {users}\n"
        f"Сигналов сегодня: {today}\n"
        f"Статистика: {stats}"
    )


@dp.message(Command("premium"))
async def give_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, True)
        await message.answer(f"✅ Premium выдан пользователю {uid}")
    except (IndexError, ValueError):
        await message.answer("Использование: /premium 123456789")


@dp.message(Command("unpremium"))
async def remove_premium(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        uid = int(message.text.split()[1])
        await set_premium(uid, False)
        await message.answer(f"Premium снят с пользователя {uid}")
    except (IndexError, ValueError):
        await message.answer("Использование: /unpremium 123456789")


@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    count = await get_today_signals_count()
    await message.answer(f"Сигналов сегодня: {count}")


async def scheduled_check_and_send():
    try:
        await check_and_send(bot)
    except Exception:
        logger.exception("Ошибка при проверке коэффициентов")


async def scheduled_check_results():
    try:
        await check_results(bot)
    except Exception:
        logger.exception("Ошибка при обновлении результатов")


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
    logger.info("Web server started")


async def on_startup():
    await init_db()
    await set_commands()

    if not scheduler.running:
        scheduler.add_job(
            scheduled_check_and_send,
            "interval",
            minutes=max(1, CHECK_INTERVAL),
            id="send_signals",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            scheduled_check_results,
            "interval",
            minutes=15,
            id="check_results",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()

    logger.info("Football Edge Bot запущен")


async def main():
    await on_startup()
    await start_web_server()

    try:
        await dp.start_polling(bot)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```
