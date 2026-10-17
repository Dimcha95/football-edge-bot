import logging
import os
import argparse
import asyncio
import math
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

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
    delete_signal,
    is_signal_sent,
    get_today_signals_count,
    set_user_active,
)
from services.odds import get_odds
from services.model import (
    calculate_edge,
    calculate_over_probability,
    estimate_probability,
)
from services.team_stats import (
    get_team_statistics,
    estimate_expected_goals,
)

logger = logging.getLogger(__name__)

# Рассылка выключена по умолчанию до проверки модели; включение через .env.
ENABLE_LIVE_SIGNALS = os.getenv("ENABLE_LIVE_SIGNALS", "0").strip().lower() in ("1", "true", "yes", "on")
_SEND_LOCK = asyncio.Lock()

# Разрешённые рынки: только тотал больше.
ALLOWED_OVER_LINES = (1.5, 2.5, 3.5)

# Режимы расчёта: для рабочей рассылки используется historical.
HISTORICAL_MODE = "historical"
EXPERIMENTAL_MODE = "experimental"
EXPERIMENTAL_HOME_GOALS = 1.5
EXPERIMENTAL_AWAY_GOALS = 1.4

MSK = ZoneInfo("Europe/Moscow")

# Пауза между сообщениями (~20 сообщений/сек, лимит Telegram ~30/сек).
SEND_DELAY = 0.05


def is_future_match(commence_time):
    """Проверяет, что матч ещё не начался."""
    if not commence_time:
        return False

    try:
        kickoff = datetime.fromisoformat(
            str(commence_time).replace("Z", "+00:00")
        )

        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        return kickoff > datetime.now(timezone.utc)

    except (ValueError, TypeError, OverflowError):
        return False


def format_kickoff(value) -> str:
    """Время начала матча по Москве в удобном виде."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK).strftime("%d.%m %H:%M МСК")
    except (ValueError, TypeError, OverflowError):
        return str(value)


async def safe_send(bot, user_id: int, text: str) -> bool:
    """Отправка с обработкой блокировки бота и лимитов Telegram."""
    for _ in range(2):
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
            )
            return True
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — отключаем рассылку.
            await set_user_active(user_id, False)
            logger.info("Пользователь %s заблокировал бота, отключён", user_id)
            return False
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            logger.exception("Ошибка доставки пользователю %s", user_id)
            return False
    return False


async def get_eligible_users():
    """Возвращает пользователей с Premium или активным trial."""
    active_users = await get_active_users()
    eligible_users = []

    for user_id in active_users:
        try:
            status, days_left = await get_user_status(user_id)

            if status == "premium" or (
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


def attach_expected_goals(match, stats, league_totals):
    """Добавляет ожидаемые голы при достаточной статистике."""
    try:
        home_id = int(match["home_id"])
        away_id = int(match["away_id"])
        league_id = int(match["league_id"])

    except (KeyError, ValueError, TypeError):
        return None

    estimate = estimate_expected_goals(
        home_id,
        away_id,
        league_id,
        stats,
        league_totals,
    )

    if estimate is None:
        return None

    return {**match, **estimate}


def evaluate_match(match):
    """Рассчитывает вероятность ТБ и математическое преимущество."""
    try:
        line = float(match["line"])
        odds = float(match["odds"])
        home_xg = float(match["home_expected_goals"])
        away_xg = float(match["away_expected_goals"])

    except (KeyError, ValueError, TypeError):
        return None

    values = (line, odds, home_xg, away_xg)

    if not all(math.isfinite(value) for value in values):
        return None

    if line not in ALLOWED_OVER_LINES:
        return None

    if odds <= 1 or home_xg <= 0 or away_xg <= 0:
        return None

    try:
        probability = calculate_over_probability(
            home_xg,
            away_xg,
            line,
            prior_total=match.get("league_avg_total"),
        )

        edge = calculate_edge(probability, odds)

    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        logger.exception("Ошибка расчёта вероятности ТБ")
        return None

    if not all(math.isfinite(v) for v in (probability, edge)):
        return None

    if not 0 <= probability <= 1:
        return None

    # Safety heuristic, not a calibrated model: reject extreme disagreement.
    market_implied = 1.0 / odds
    passes_filters = (
        probability <= market_implied + 0.20
        and probability >= MIN_PROBABILITY
        and edge >= MIN_EDGE
    )

    return {
        "line": line,
        "odds": odds,
        "probability": probability,
        "edge": edge,
        "passes_filters": passes_filters,
    }


def format_signal(match, evaluation, test=False):
    """Формирует сообщение Telegram."""
    home = escape(str(match.get("home", "")))
    away = escape(str(match.get("away", "")))
    league = escape(str(match.get("league", "Футбол")))
    commence_time = escape(
        format_kickoff(match.get("commence_time", ""))
    )

    line = evaluation["line"]
    odds = evaluation["odds"]
    probability = evaluation["probability"]
    edge = evaluation["edge"]

    if test:
        prefix = "🧪 <b>ТЕСТОВЫЙ СИГНАЛ</b>\n"
    else:
        prefix = "⚽️ <b>FOOTBALL EDGE BOT</b>\n"

    return (
        f"{prefix}\n"
        f"⚽️ <b>{home} — {away}</b>\n"
        f"🏆 {league}\n"
        f"🕒 {commence_time}\n\n"
        f"🎯 <b>ТБ {line:g}</b> @ {odds:.2f}\n"
        f"📊 Оценка вероятности: {probability * 100:.1f}%\n"
        f"📈 Расчётный Edge: {edge * 100:+.1f}%\n\n"
        "⚠️ Оценка на основе исторических результатов и модели Пуассона. "
        "Вероятности и Edge не подтверждены бэктестом; это не гарантия выигрыша."
    )


def get_match_key(match):
    """Уникальный ключ матча для выбора одного прогноза."""
    event_id = match.get("event_id")

    if event_id is not None and str(event_id).strip():
        return str(event_id)

    fixture_id = match.get("fixture_id")

    if fixture_id is not None and str(fixture_id).strip():
        return f"fixture:{fixture_id}"

    return None


def select_best_over_signals(prepared):
    """
    Оставляет один лучший ТБ на каждый матч.

    Условия:
    - только ТБ 1.5 / 2.5 / 3.5;
    - вероятность и Edge проходят фильтры;
    - максимальный Edge среди подходящих вариантов.
    """
    best_by_match = {}

    for match, evaluation in prepared:
        if not evaluation["passes_filters"]:
            continue

        key = get_match_key(match)

        if key is None:
            continue

        current = best_by_match.get(key)

        if current is None or evaluation["edge"] > current[1]["edge"]:
            best_by_match[key] = (match, evaluation)

    selected = list(best_by_match.values())

    # Самые высокие расчётные преимущества — первыми.
    selected.sort(
        key=lambda item: item[1]["edge"],
        reverse=True,
    )

    logger.info(
        "Отобрано лучших ТБ: %s из %s вариантов",
        len(selected),
        len(prepared),
    )

    return selected


def evaluate_experimental_match(match):
    """Только демонстрационный расчёт: одинаковые голы для всех команд.

    Вероятность и Edge НЕ являются валидированной оценкой преимущества.
    """
    try:
        line = float(match["line"])
        odds = float(match["odds"])
    except (KeyError, ValueError, TypeError):
        return None

    if (line not in ALLOWED_OVER_LINES or not math.isfinite(odds)
            or odds <= 1):
        return None

    probability = estimate_probability(
        line,
        home_strength=EXPERIMENTAL_HOME_GOALS,
        away_strength=EXPERIMENTAL_AWAY_GOALS,
    )
    edge = calculate_edge(probability, odds)
    if not all(math.isfinite(x) for x in (probability, edge)):
        return None

    return {
        "line": line,
        "odds": odds,
        "probability": probability,
        "edge": edge,
        "passes_filters": probability >= MIN_PROBABILITY and edge >= MIN_EDGE,
    }


async def load_evaluated_matches(mode=HISTORICAL_MODE):
    """Выбирает лучший ТБ; experimental доступен только для предпросмотра."""
    if mode not in (HISTORICAL_MODE, EXPERIMENTAL_MODE):
        raise ValueError(f"Неизвестный режим: {mode}")
    matches = await get_odds()

    if not matches:
        logger.info("Нет доступных коэффициентов")
        return []

    if mode == EXPERIMENTAL_MODE:
        prepared = []
        for match in matches:
            if not is_future_match(match.get("commence_time")):
                continue
            evaluation = evaluate_experimental_match(match)
            if evaluation is not None:
                prepared.append((match, evaluation))
        logger.warning(
            "Упрощённая модель: %s вариантов из %s; "
            "оценки вероятностей не откалиброваны",
            len(prepared), len(matches),
        )
        return select_best_over_signals(prepared)

    stats, league_totals = await get_team_statistics()

    if not stats or not league_totals:
        logger.warning(
            "Исторической статистики недостаточно; "
            "сигналы пропущены"
        )
        return []

    prepared = []
    skipped = 0

    for match in matches:
        if not is_future_match(match.get("commence_time")):
            continue

        enriched = attach_expected_goals(
            match,
            stats,
            league_totals,
        )

        if enriched is None:
            skipped += 1
            continue

        evaluation = evaluate_match(enriched)

        if evaluation is not None:
            prepared.append((enriched, evaluation))

    logger.info(
        "Оценено вариантов: %s, "
        "без достаточной статистики: %s",
        len(prepared),
        skipped,
    )

    return select_best_over_signals(prepared)


async def test_signal(bot, admin_id=None):
    """
    Отправляет один тестовый сигнал администратору.
    В базу данных тестовый сигнал не записывается.
    """
    if not ADMIN_IDS:
        logger.error("ADMIN_IDS не настроен")
        return False

    if admin_id is None:
        admin_id = ADMIN_IDS[0]

    if admin_id not in ADMIN_IDS:
        logger.error("Тест разрешён только администратору")
        return False

    evaluated_matches = await load_evaluated_matches(mode=HISTORICAL_MODE)

    if not evaluated_matches:
        logger.info("Подходящих тестовых сигналов нет")
        return False

    # Список уже отсортирован по Edge.
    match, evaluation = evaluated_matches[0]

    ok = await safe_send(
        bot,
        admin_id,
        format_signal(match, evaluation, test=True),
    )

    if ok:
        logger.info("Тестовый сигнал отправлен администратору %s", admin_id)

    return ok


async def was_match_signalled(match_name, event_id):
    """
    Проверяет все разрешённые ТБ, чтобы не отправлять
    другой тотал на уже обработанный матч.
    """
    for line in ALLOWED_OVER_LINES:
        market_name = f"ТБ {line:g}"

        already_sent = await is_signal_sent(
            match_name,
            market_name,
            event_id=str(event_id),
        )

        if already_sent:
            return True

    return False


async def check_and_send(bot):
    """Рассылка лучших исторических ТБ. Сигнал сохраняется до отправки
    и откатывается, если не доставлен никому."""
    if not ENABLE_LIVE_SIGNALS:
        logger.info("Рассылка отключена (ENABLE_LIVE_SIGNALS=0)")
        return 0

    if _SEND_LOCK.locked():
        logger.info("Предыдущая рассылка ещё выполняется")
        return 0

    async with _SEND_LOCK:
        today_count = await get_today_signals_count()
        if today_count >= MAX_SIGNALS_PER_DAY:
            logger.info("Дневной лимит сигналов достигнут")
            return 0

        users = await get_eligible_users()
        if not users:
            logger.info("Нет пользователей с активным доступом")
            return 0

        evaluated_matches = await load_evaluated_matches(mode=HISTORICAL_MODE)
        new_signals = 0

        for match, evaluation in evaluated_matches:
            if today_count + new_signals >= MAX_SIGNALS_PER_DAY:
                break
            if not evaluation["passes_filters"]:
                continue

            event_id = match.get("event_id")
            home, away = match.get("home"), match.get("away")
            if not event_id or not home or not away:
                continue
            if not is_future_match(match.get("commence_time")):
                continue

            line = evaluation["line"]
            if line not in ALLOWED_OVER_LINES:
                continue

            match_name = f"{home} — {away}"
            market_name = f"ТБ {line:g}"

            try:
                if await was_match_signalled(match_name, event_id):
                    continue

                # Фиксируем в БД до отправки, чтобы повторный запуск не
                # создавал дублей.
                saved = await save_signal(
                    match_name=match_name,
                    league=match.get("league", "Футбол"),
                    market=market_name,
                    odds=evaluation["odds"],
                    our_prob=evaluation["probability"],
                    edge=evaluation["edge"],
                    event_id=str(event_id),
                    fixture_id=None,
                    commence_time=match.get("commence_time"),
                )
                if not saved:
                    logger.info("Сигнал уже существует: %s %s", match_name, market_name)
                    continue
            except Exception:
                logger.exception("Ошибка сохранения: %s", match_name)
                continue

            new_signals += 1
            message = format_signal(match, evaluation)
            delivered = 0

            for user_id in users:
                if await safe_send(bot, user_id, message):
                    delivered += 1
                await asyncio.sleep(SEND_DELAY)

            logger.info(
                "Сигнал %s | %s | доставлено %s/%s",
                match_name, market_name, delivered, len(users),
            )

            if delivered == 0:
                # Никто не получил сигнал — не учитываем его в статистике.
                # Если матч ещё не начался, он уйдёт при следующем запуске.
                logger.warning("Сигнал не доставлен, откатываю: %s", match_name)
                try:
                    await delete_signal(str(event_id), market_name)
                except Exception:
                    logger.exception("Не удалось откатить сигнал: %s", match_name)
                new_signals -= 1

        logger.info("Сохранено новых сигналов: %s", new_signals)
        return new_signals


async def preview_signals(mode=HISTORICAL_MODE, limit=10):
    """Печатает тестовые варианты. Ничего не отправляет и не сохраняет."""
    if mode not in (HISTORICAL_MODE, EXPERIMENTAL_MODE):
        raise ValueError(f"Неизвестный режим: {mode}")
    print(f"Режим: {mode}")
    if mode == EXPERIMENTAL_MODE:
        print("ВНИМАНИЕ: вероятности основаны на одинаковых 2.9 гола для всех матчей.")
        print("Расчётный Edge не доказывает преимущество. Ставки не рекомендуются.")
    selected = await load_evaluated_matches(mode=mode)
    print(f"Подходящих матчей: {len(selected)}")
    for match, evaluation in selected[:limit]:
        print(
            f"{match.get('home', '?')} — {match.get('away', '?')} | "
            f"ТБ {evaluation['line']:g} @ {evaluation['odds']:.2f} | "
            f"p={evaluation['probability']:.1%} | "
            f"условный edge={evaluation['edge']:+.1%}"
        )
    print("Сообщения не отправлялись. Сигналы в БД не записывались.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Предпросмотр прогнозов без рассылки")
    parser.add_argument("--preview", choices=[HISTORICAL_MODE, EXPERIMENTAL_MODE], required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(preview_signals(args.preview, max(0, args.limit)))
