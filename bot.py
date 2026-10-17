import asyncio
import logging
import os
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
    get_stats_for_period,
    get_over_stats,
    get_users_count,
    get_users_ending_trial,
    set_premium,
    get_user_status,
    get_today_signals_count,
    get_recent_results,
)
from services.sender import check_and_send, test_signal
from services.results import check_results


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

PORT = int(os.getenv("PORT", 10000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

PERIODS = {
    "1": ("Сегодня", 1),
    "7": ("7 дней", 7),
    "30": ("30 дней", 30),
    "all": ("Всё время", None),
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Включить сигналы", callback_data="on")],
            [InlineKeyboardButton(text="❌ Выключить", callback_data="off")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton(text="📅 Статистика по периодам", callback_data="periods")],
            [InlineKeyboardButton(text="📈 Статистика по рынкам", callback_data="markets")],
            [InlineKeyboardButton(text="📋 Сводка за сегодня", callback_data="today")],
        ]
    )


def periods_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="period:1"),
                InlineKeyboardButton(text="7 дней", callback_data="period:7"),
            ],
            [
                InlineKeyboardButton(text="30 дней", callback_data="period:30"),
                InlineKeyboardButton(text="Всё время", callback_data="period:all"),
            ],
            [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")]
        ]
    )


async def set_commands():
    await bot.set_my_commands(
        [BotCommand(command="start", description="Запуск бота")],
        scope=BotCommandScopeDefault(),
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Запуск"),
                    BotCommand(command="admin", description="Админ-панель"),
                    BotCommand(command="premium", description="Выдать Premium"),
                    BotCommand(command="unpremium", description="Снять Premium"),
                    BotCommand(command="today", description="Сигналы сегодня"),
                    BotCommand(command="testsignal", description="Тестовый сигнал"),
                ],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logger.exception(
                "Не удалось установить команды администратора %s", admin_id
            )


async def has_access(user_id: int) -> bool:
    status, days_left = await get_user_status(user_id)

    if status == "premium":
        return True

    if status == "trial" and days_left is not None:
        return days_left > 0

    return False


def start_text(status_text: str) -> str:
    return (
        "⚽️ <b>Football Edge Bot</b>\n\n"
        "Бот анализирует футбольные коэффициенты "
        "и ищет потенциальные value-ставки.\n\n"
        f"Статус: <b>{status_text}</b>\n\n"
        "Используй кнопки ниже для управления."
    )


async def status_text_for(user_id: int) -> str:
    status, days_left = await get_user_status(user_id)

    if status == "premium":
        return "💎 Premium"
    if status == "trial" and days_left is not None and days_left > 0:
        return f"🎁 Пробный период: {days_left} дн."
    return "⛔ Пробный период закончился"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not message.from_user:
        return

    user_id = message.from_user.id

    await add_user(user_id, message.from_user.username)

    status_text = await status_text_for(user_id)

    await set_user_active(user_id, await has_access(user_id))

    await message.answer(
        start_text(status_text),
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "menu")
async def show_menu(callback: types.CallbackQuery):
    if callback.message and callback.from_user:
        await callback.message.answer(
            start_text(await status_text_for(callback.from_user.id)),
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )
    await callback.answer()


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
            "✅ Сигналы включены.\nБот будет присылать подходящие сигналы."
        )


@dp.callback_query(F.data == "off")
async def turn_off(callback: types.CallbackQuery):
    if not callback.from_user:
        return

    await set_user_active(callback.from_user.id, False)
    await callback.answer("Сигналы выключены")

    if callback.message:
        await callback.message.answer("❌ Сигналы выключены.")


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
            icon = {"win": "✅", "loss": "❌", "push": "↩️"}.get(result, "⏳")

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
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    await callback.answer()


@dp.callback_query(F.data == "today")
async def show_today(callback: types.CallbackQuery):
    count = await get_today_signals_count()

    if callback.message:
        await callback.message.answer(
            f"📋 Сигналов сегодня: {count}", reply_markup=back_keyboard()
        )

    await callback.answer()


@dp.callback_query(F.data == "periods")
async def show_periods(callback: types.CallbackQuery):
    if callback.message:
        await callback.message.answer(
            "📅 Выбери период:", reply_markup=periods_keyboard()
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("period:"))
async def show_period_stats(callback: types.CallbackQuery):
    key = (callback.data or "").split(":", 1)[-1]
    if key not in PERIODS:
        await callback.answer()
        return

    title, days = PERIODS[key]
    s = await get_stats_for_period(days)

    settled = s["wins"] + s["losses"]
    winrate = s["wins"] / settled * 100 if settled else 0.0
    roi = s["profit"] / s["roi_bets"] * 100 if s["roi_bets"] else 0.0

    text = (
        f"📅 <b>Статистика: {escape(title)}</b>\n\n"
        f"Всего сигналов: {s['total']}\n"
        f"✅ Побед: {s['wins']}\n"
        f"❌ Поражений: {s['losses']}\n"
        f"↩️ Возвратов: {s['pushes']}\n"
        f"⏳ Ожидают: {s['pending']}\n\n"
        f"Проходимость: <b>{winrate:.1f}%</b>\n"
        f"Прибыль (ставка 1 ед.): <b>{s['profit']:+.2f} ед.</b>\n"
        f"ROI: <b>{roi:+.1f}%</b>"
    )

    if callback.message:
        await callback.message.answer(
            text, parse_mode="HTML", reply_markup=periods_keyboard()
        )
    await callback.answer()


@dp.callback_query(F.data == "markets")
async def show_markets(callback: types.CallbackQuery):
    stats = await get_over_stats()

    text = "📈 <b>Статистика по рынкам</b>\n"
    for line, s in stats.items():
        text += (
            f"\n<b>ТБ {line}</b>\n"
            f"Сигналов: {s['total']} "
            f"(✅ {s['wins']} / ❌ {s['losses']} / ↩️ {s['pushes']} / ⏳ {s['pending']})\n"
            f"Проходимость: {s['winrate']:.1f}%\n"
            f"Прибыль: {s['profit']:+.2f} ед. | ROI: {s['roi']:+.1f}%\n"
        )

    if callback.message:
        await callback.message.answer(
            text, parse_mode="HTML", reply_markup=back_keyboard()
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
        await message.answer("Использование: /premium 123456789")
        return

    ok = await set_premium(user_id, True)
    if not ok:
        await message.answer(
            "Пользователь не найден. Пусть сначала нажмёт /start."
        )
        return

    await message.answer(f"✅ Premium выдан пользователю {user_id}.")

    try:
        await bot.send_message(user_id, "💎 Вам выдан Premium. Спасибо!")
    except Exception:
        logger.warning("Не удалось уведомить пользователя %s", user_id)


@dp.message(Command("unpremium"))
async def remove_premium(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    try:
        user_id = int(message.text.split()[1])
    except (IndexError, ValueError, AttributeError):
        await message.answer("Использование: /unpremium 123456789")
        return

    ok = await set_premium(user_id, False)
    if not ok:
        await message.answer("Пользователь не найден в базе.")
        return

    await message.answer(f"Premium снят с пользователя {user_id}.")


@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    count = await get_today_signals_count()
    await message.answer(f"📋 Сигналов сегодня: {count}")


@dp.message(Command("testsignal"))
async def cmd_testsignal(message: types.Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return

    ok = await test_signal(bot, message.from_user.id)
    await message.answer(
        "🧪 Тестовый сигнал отправлен."
        if ok
        else "Подходящих сигналов сейчас нет (см. логи)."
    )


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


async def notify_trial_ending():
    try:
        user_ids = await get_users_ending_trial()
    except Exception:
        logger.exception("Ошибка выборки пользователей с оканчивающимся триалом")
        return

    for user_id in user_ids:
        try:
            await bot.send_message(
                user_id,
                "⏰ Пробный период заканчивается завтра. "
                "Чтобы продолжить получать сигналы, нужен Premium.",
            )
        except Exception:
            logger.warning("Не удалось отправить напоминание %s", user_id)
        await asyncio.sleep(0.05)


async def health_check(request):
    return web.Response(text="OK", content_type="text/plain")


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    logger.info("Healthcheck server запущен на порту %s", PORT)

    return runner


async def on_startup():
    await init_db()
    await set_commands()

    if not scheduler.running:
        # Проверка сигналов каждый час с 08:00 до 23:00 МСК.
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

        # Напоминание об окончании пробного периода (раз в сутки).
        scheduler.add_job(
            notify_trial_ending,
            "cron",
            hour=12,
            minute=0,
            timezone="Europe/Moscow",
            id="notify_trial_ending",
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
