
import logging

from config import MIN_EDGE, MIN_PROBABILITY, MAX_SIGNALS_PER_DAY
from database import (
    get_active_users,
    get_user_status,
    save_signal,
    is_signal_sent,
    get_today_signals_count,
)
from services.odds import get_odds
from services.model import calculate_edge

logger = logging.getLogger(__name__)


def estimate_probability(
    line: float,
    home_strength: float = 1.5,
    away_strength: float = 1.4,
) -> float:
    """
    Предварительная эвристическая оценка вероятности.
    Не является обученной моделью и не гарантирует результат.
    """
    expected_total = (home_strength + away_strength) / 2

    base = {
        1.5: 0.68,
        2.5: 0.52,
        3.5: 0.38,
    }.get(float(line), 0.40)

    if expected_total > line + 0.4:
        base += 0.08
    elif expected_total < line - 0.4:
        base -= 0.08

    return max(0.15, min(0.85, base))


async def get_eligible_users():
    """Возвращает активных пользователей с действующим доступом."""
    active_users = await get_active_users()
    eligible_users = []

    for user_id in active_users:
        try:
            status, days_left = await get_user_status(user_id)

            if status == "premium":
                eligible_users.append(user_id)
            elif (
                status == "trial"
                and days_left is not None
                and days_left > 0
            ):
                eligible_users.append(user_id)

        except Exception:
            logger.exception(
                "Не удалось проверить доступ пользователя %s",
                user_id,
            )

    return eligible_users


async def check_and_send(bot):
    logger.info("Запущена проверка коэффициентов")

    matches = await get_odds()
    logger.info("Получено вариантов коэффициентов: %s", len(matches))

    today_count = await get_today_signals_count()

    if today_count >= MAX_SIGNALS_PER_DAY:
        logger.info("Достигнут дневной лимит: %s", MAX_SIGNALS_PER_DAY)
        return

    users = await get_eligible_users()

    if not users:
        logger.info("Нет пользователей с действующим доступом")
        return

    new_signals = 0

    for match in matches:
        if today_count + new_signals >= MAX_SIGNALS_PER_DAY:
            logger.info("Достигнут дневной лимит сигналов")
            break

        event_id = match.get("event_id")
        home = match.get("home")
        away = match.get("away")
        league = match.get("league", "Футбол")
        commence_time = match.get("commence_time")
        fixture_id = match.get("fixture_id")

        try:
            line = float(match["line"])
            odds = float(match["odds"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Пропущен матч с некорректными коэффициентами")
            continue

        if not event_id or not home or not away or odds <= 1:
            continue

        match_name = f"{home} — {away}"
        market_name = f"ТБ {line:g}"

        if await is_signal_sent(
            match_name,
            market_name,
            event_id=str(event_id),
        ):
            continue

        probability = estimate_probability(
            line,
            match.get("home_strength", 1.5),
            match.get("away_strength", 1.4),
        )
        edge = calculate_edge(probability, odds)

        if probability < MIN_PROBABILITY or edge < MIN_EDGE:
            continue

        text = (
            f"⚽️ <b>{match_name}</b>\n"
            f"🏆 {league}\n"
            f"🕒 {commence_time or 'Время неизвестно'}\n\n"
            f"🎯 <b>{market_name}</b> @ {odds:.2f}\n"
            f"📊 Оценка вероятности: <b>{probability * 100:.1f}%</b>\n"
            f"📈 Расчётный edge: <b>{edge * 100:+.1f}%</b>\n\n"
            "ℹ️ Вероятность предварительная, "
            "это не гарантия результата."
        )

        try:
            saved = await save_signal(
                match_name=match_name,
                league=league,
                market=market_name,
                odds=odds,
                our_prob=probability,
                edge=edge,
                event_id=str(event_id),
                fixture_id=int(fixture_id) if fixture_id is not None else None,
                commence_time=commence_time,
            )
        except Exception:
            logger.exception("Не удалось сохранить сигнал: %s", match_name)
            continue

        if not saved:
            logger.info("Дубликат сигнала пропущен: %s", match_name)
            continue

        delivered = 0

        for user_id in users:
            try:
                await bot.send_message(
                    user_id,
                    text,
                    parse_mode="HTML",
                )
                delivered += 1
            except Exception:
                logger.exception(
                    "Не удалось отправить сигнал пользователю %s",
                    user_id,
                )

        new_signals += 1

        logger.info(
            "Сигнал сохранён: %s, %s; доставлено: %s из %s",
            match_name,
            market_name,
            delivered,
            len(users),
        )

    logger.info("Новых сигналов за проверку: %s", new_signals)
