
import logging
from datetime import datetime, timezone
from html import escape

from config import (
    ADMIN_IDS,
    MIN_EDGE,
    MIN_PROBABILITY,
    MAX_SIGNALS_PER_DAY,
)

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


# Пока нет реальных ожидаемых голов команд,
# массовую отправку отключаем.
ENABLE_LIVE_SIGNALS = False


def estimate_probability(
    line: float,
    home_strength: float = 1.5,
    away_strength: float = 1.4,
) -> float:
    """
    Временная эвристика.
    Используется только для диагностики.
    """

    expected_total = (
        home_strength + away_strength
    ) / 2

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


def is_future_match(commence_time):
    """Проверяет, что матч ещё не начался."""

    if not commence_time:
        return False

    try:
        kickoff = datetime.fromisoformat(
            str(commence_time).replace(
                "Z", "+00:00"
            )
        )

        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(
                tzinfo=timezone.utc
            )

        return kickoff > datetime.now(timezone.utc)

    except (ValueError, TypeError):
        return False


async def get_eligible_users():
    """Активные пользователи с действующим доступом."""

    active_users = await get_active_users()
    eligible_users = []

    for user_id in active_users:
        try:
            status, days_left = await get_user_status(
                user_id
            )

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
                "Ошибка проверки доступа пользователя %s",
                user_id,
            )

    return eligible_users


def evaluate_match(match):
    """
    Рассчитывает предварительные показатели.
    Возвращает None для некорректного матча.
    """

    try:
        line = float(match["line"])
        odds = float(match["odds"])

        home_strength = float(
            match.get("home_strength", 1.5)
        )
        away_strength = float(
            match.get("away_strength", 1.4)
        )

    except (KeyError, ValueError, TypeError):
        return None

    if odds <= 1:
        return None

    probability = estimate_probability(
        line,
        home_strength,
        away_strength,
    )

    edge = calculate_edge(
        probability,
        odds,
    )

    return {
        "line": line,
        "odds": odds,
        "probability": probability,
        "edge": edge,
        "passes_filters": (
            probability >= MIN_PROBABILITY
            and edge >= MIN_EDGE
        ),
    }


def format_signal(match, evaluation, test=False):
    """Формирует сообщение Telegram."""

    home = escape(str(match.get("home", "")))
    away = escape(str(match.get("away", "")))
    league = escape(
        str(match.get("league", "Футбол"))
    )
    commence_time = escape(
        str(match.get("commence_time", ""))
    )

    line = evaluation["line"]
    odds = evaluation["odds"]
    probability = evaluation["probability"]
    edge = evaluation["edge"]

    prefix = (
        "🧪 <b>ТЕСТОВЫЙ СИГНАЛ</b>\n"
        if test else
        "⚽️ <b>FOOTBALL EDGE BOT</b>\n"
    )

    return (
        f"{prefix}\n"
        f"⚽️ <b>{home} — {away}</b>\n"
        f"🏆 {league}\n"
        f"🕒 {commence_time}\n\n"
        f"🎯 ТБ {line:g} @ {odds:.2f}\n"
        f"📊 Оценка вероятности: "
        f"{probability * 100:.1f}%\n"
        f"📈 Расчётный Edge: "
        f"{edge * 100:+.1f}%\n\n"
        "⚠️ Вероятность основана на временной "
        "эвристике и не подтверждена "
        "статистической моделью."
    )


async def test_signal(bot, admin_id=None):
    """
    Отправляет один диагностический сигнал
    только администратору.

    Не сохраняет его в базу данных.
    """

    if not ADMIN_IDS:
        logger.error("ADMIN_IDS не настроен")
        return False

    if admin_id is None:
        admin_id = ADMIN_IDS[0]

    if admin_id not in ADMIN_IDS:
        logger.error(
            "Тест разрешён только администратору"
        )
        return False

    matches = await get_odds()

    logger.info(
        "Для тестирования получено %s вариантов",
        len(matches),
    )

    for match in matches:

        if not is_future_match(
            match.get("commence_time")
        ):
            continue

        evaluation = evaluate_match(match)

        if evaluation is None:
            continue

        if not evaluation["passes_filters"]:
            continue

        text = format_signal(
            match,
            evaluation,
            test=True,
        )

        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="HTML",
            )

            logger.info(
                "Тестовый сигнал отправлен %s",
                admin_id,
            )

            return True

        except Exception:
            logger.exception(
                "Ошибка тестовой отправки"
            )
            return False

    logger.info(
        "Подходящих тестовых сигналов нет"
    )
    return False


async def check_and_send(bot):
    """
    Основная автоматическая проверка.

    Пока модель использует временные
    вероятности, отправка отключена.
    """

    if not ENABLE_LIVE_SIGNALS:
        logger.info(
            "Автоматическая рассылка отключена: "
            "нет подтверждённой модели вероятностей"
        )
        return

    matches = await get_odds()

    today_count = await get_today_signals_count()

    if today_count >= MAX_SIGNALS_PER_DAY:
        logger.info(
            "Достигнут дневной лимит сигналов"
        )
        return

    users = await get_eligible_users()

    if not users:
        logger.info(
            "Нет активных пользователей с доступом"
        )
        return

    new_signals = 0

    for match in matches:

        if today_count + new_signals >= MAX_SIGNALS_PER_DAY:
            break

        if not is_future_match(
            match.get("commence_time")
        ):
            continue

        event_id = match.get("event_id")
        home = match.get("home")
        away = match.get("away")

        if not event_id or not home or not away:
            continue

        evaluation = evaluate_match(match)

        if evaluation is None:
            continue

        if not evaluation["passes_filters"]:
            continue

        line = evaluation["line"]

        match_name = f"{home} — {away}"
        market_name = f"ТБ {line:g}"

        if await is_signal_sent(
            match_name,
            market_name,
            event_id=str(event_id),
        ):
            continue

        text = format_signal(
            match,
            evaluation,
        )

        # Отправка до сохранения не гарантирует
        # отсутствие дублей при аварийном завершении.
        # Для production потребуется таблица
        # доставки сигналов по пользователям.
        delivered = 0

        for user_id in users:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="HTML",
                )

                delivered += 1

            except Exception:
                logger.exception(
                    "Ошибка отправки пользователю %s",
                    user_id,
                )

        if delivered == 0:
            logger.warning(
                "Сигнал никому не доставлен: %s",
                match_name,
            )
            continue

        fixture_id = match.get("fixture_id")

        try:
            saved = await save_signal(
                match_name=match_name,
                league=match.get(
                    "league", "Футбол"
                ),
                market=market_name,
                odds=evaluation["odds"],
                our_prob=evaluation["probability"],
                edge=evaluation["edge"],
                event_id=str(event_id),
                fixture_id=(
                    int(fixture_id)
                    if fixture_id is not None
                    else None
                ),
                commence_time=match.get(
                    "commence_time"
                ),
            )

            if saved:
                new_signals += 1

        except Exception:
            logger.exception(
                "Ошибка сохранения сигнала"
            )

    logger.info(
        "Новых сохранённых сигналов: %s",
        new_signals,
    )
