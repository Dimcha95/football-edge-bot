
import asyncio
import logging
from html import escape

from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, ADMIN_IDS
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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Включить сигналы",
                    callback_data="on",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Выключить",
                    callback_data="off",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Статистика по периодам",
                    callback_data="periods",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Сводка за сегодня",
                    callback_data="today",
                )
            ],
        ]
    )


async def set_commands():
    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Запуск бота",
            ),
        ],
        scope=BotCommandScopeDefault(),
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(
                        command="start",
                        description="Запуск",
                    ),
                    BotCommand(
                        command="admin",
                        description="Админ-панель",
                    ),
                    BotCommand(
                        command="premium",
                        description="Выдать Premium",
                    ),
                    BotCommand(
                        command="unpremium",
                        description="Снять Premium",
                    ),
                    BotCommand(
                        command="today",
                        description="Сигналы сегодня",
                    ),
                ],
                scope=BotCommandScopeChat(
                    chat_id=admin_id
                ),
            )
        except Exception:
            logger.exception(
                "Не удалось установить команды администратора %s",
                admin_id,
            )


async def has_access(user_id: int) -> bool:
    status, days_left = await get_user_status(user_id)

    if status == "premium":
        return True

    if status == "trial" and days_left is not None:
        return days_left > 0

    return False


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not message.from_user:
        return

    user_id = message.from_user.id

    await add_user(
        user_id,
        message.from_user.username,
    )

    status, days_left = await get_user_status(user_id)

    if status == "premium":
        status_text = "💎 Premium"
    elif status == "trial" and days_left is not None and days_left > 0:
        status_text = f"🎁 Пробный период: {days_left} дн."
    else:
        status_text = "⛔ Пробный период закончился"

    if await has_access(user_id):
        await set_user_active(user_id, True)
    else:
        await set_user_active(user_id, False)

    await message.answer(
        "⚽️ <b>Football Edge Bot</b>\n\n"
        "Бот анализирует футбольные коэффициенты "
        "и ищет потенциальные value-ставки.\n\n"
        f"Статус: <b>{status_text}</b>\n\n"
        "Используй кнопки ниже для управления.",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "on")
async def turn_on(callback: types.CallbackQuery):
    if not callback.from_user:
        return

    user_id = callback.from_user.id

    if not await has_access(user_id):
        await set_user_active(user_id, False)

        await callback.answer(
            "Пробный период закончился. Требуется Premium.",
            show_alert=True,
        )
        return

    await set_user_active(user_id, True)

    await callback.answer("Сигналы включены")

    if callback.message:
        await callback.message.answer(
            "✅ Сигналы включены.\n"
            "Бот будет присылать подходящие сигналы."
        )


@dp.callback_query(F.data == "off")
async def turn_off(callback: types.CallbackQuery):
    if not callback.from_user:
        return

    await set_user_active(
        callback.from_user.id,
        False,
    )

    await callback.answer("Сигналы выключены")

    if callback.message:
        await callback.message.answer(
            "❌ Сигналы выключены."
        )


@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    stats = await get_stats()
    results = await get_recent_results(15)

    settled = stats["wins"] + stats["losses"]

    text = (
        "📊 <b>Эффективность сигналов</b>\n\n"
        f"Проходимость: <b>{stats['winrate']:.1f}%</b>\n"
        f"Рассчитано: {settled}\n"
        f"Всего сигналов: {stats['total']}\n\n"
        f"✅ Побед: {stats['wins']}\n"
        f"❌ Поражений: {stats['losses']}\n"
        f"↩️ Возвратов: {stats.get('pushes', 0)}\n"
        f"⏳ Ожидают результата: {stats['pending']}\n\n"
        "<b>Последние результаты:</b>\n"
    )

    if results:
        for match_name, market, odds, edge, result in results:
            icon = {
                "win": "✅",
                "loss": "❌",
                "push": "↩️",
            }.get(result, "⏳")

            safe_match_name = escape(str(match_name))
            safe_market = escape(str(market))

            text += (
                f"\n{icon} {safe_match_name}\n"
                f"{safe_market} @ {odds:.2f}\n"
                f"Edge: {edge * 100:+.1f}%\n"
            )
    else:
        text += "\nПока нет завершённых сигналов."

    if callback.message:
        await callback.message.answer(
            text,
            parse_mode="HTML",
        )

    await callback.answer()


@dp.callback_query(F.data == "today")
async def show_today(callback: types.CallbackQuery):
    count = await get_today_signals_count()

    if callback.message:
        await callback.message.answer(
            f"📋 Сигналов сегодня: {count}"
        )

    await callback.answer()


@dp.callback_query(F.data == "periods")
async def show_periods(callback: types.CallbackQuery):
    if callback.message:
        await callback.message.answer(
            "📅 Статистика по периодам пока в разработке."
        )

    await callback.answer()


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    users = await get_users_count()
    stats = await get_stats()
    today = await get_today_signals_count()

    await message.answer(
        "🛠 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"📋 Сигналов сегодня: {today}\n"
        f"📊 Всего сигналов: {stats['total']}\n"
        f"✅ Побед: {stats['wins']}\n"
        f"❌ Поражений: {stats['losses']}\n"
        f"⏳ Ожидают: {stats['pending']}\n"
        f"📈 Проходимость: {stats['winrate']:.1f}%",
        parse_mode="HTML",
    )


@dp.message(Command("premium"))
async def give_premium(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError, AttributeError):
        await message.answer(
            "Использование: /premium 123456789"
        )
        return

    await set_premium(user_id, True)

    await message.answer(
        f"✅ Premium выдан пользователю {user_id}.\n"
        "Пользователь должен быть зарегистрирован через /start."
    )


@dp.message(Command("unpremium"))
async def remove_premium(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError, AttributeError):
        await message.answer(
            "Использование: /unpremium 123456789"
        )
        return

    await set_premium(user_id, False)

    await message.answer(
        f"Premium снят с пользователя {user_id}."
    )


@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    count = await get_today_signals_count()

    await message.answer(
        f"📋 Сигналов сегодня: {count}"
    )


async def scheduled_check_and_send():
    try:
        await check_and_send(bot)
    except Exception:
        logger.exception(
            "Ошибка при проверке коэффициентов"
        )


async def scheduled_check_results():
    try:
        await check_results(bot)
    except Exception:
        logger.exception(
            "Ошибка при обновлении результатов"
        )


async def health_check(request):
    return web.Response(
        text="OK",
        content_type="text/plain",
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(
            runner,
            "0.0.0.0",
            10000,
        )
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    logger.info(
        "Healthcheck server запущен на порту 10000"
    )

    return runner


async def on_startup():
    await init_db()
    await set_commands()

    if not scheduler.running:
        # Проверка сигналов каждый час с 10:00 до 22:00 МСК.
        scheduler.add_job(
            scheduled_check_and_send,
            "cron",
            hour="10-22",
            minute=0,
            timezone="Europe/Moscow",
            id="scheduled_check_and_send",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # Результаты: четыре раза в сутки по Москве.
        scheduler.add_job(
            scheduled_check_results,
            "cron",
            hour="3,9,15,21",
            minute=30,
            timezone="Europe/Moscow",
            id="scheduled_check_results",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        scheduler.start()

    logger.info(
        "Football Edge Bot запущен. Поиск сигналов: каждый час "
        "с 10:00 до 22:00 МСК; результаты: 03:30, 09:30, 15:30, 21:30 МСК."
    )


async def main():
    runner = None

    try:
        await on_startup()

        runner = await start_web_server()

        logger.info("Запуск Telegram polling")

        await dp.start_polling(bot)

    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)

        if runner is not None:
            await runner.cleanup()

        await bot.session.close()

        logger.info("Football Edge Bot остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
