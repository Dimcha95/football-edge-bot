
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from config import API_FOOTBALL_KEY
from database import (
    get_pending_signals_with_time,
    update_signal_result,
    get_api_cache,
    set_api_cache,
)

logger = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"
CACHE_TTL = 15 * 60
FINAL_STATUSES = {"FT", "AET", "PEN"}


async def api_get(
    endpoint: str,
    params: dict,
    ttl: int = CACHE_TTL,
):
    """Получает данные API-Football с кешированием."""

    if not API_FOOTBALL_KEY:
        logger.error("API_FOOTBALL_KEY не задан")
        return None

    cache_key = f"results:{endpoint}:{sorted(params.items())}"

    cached = await get_api_cache(cache_key)
    if cached is not None:
        return cached

    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    timeout = aiohttp.ClientTimeout(total=20)

    try:
        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
        ) as session:
            async with session.get(
                f"{API_BASE}/{endpoint.lstrip('/')}",
                params=params,
            ) as response:
                if response.status != 200:
                    logger.error(
                        "API-Football HTTP %s: %s",
                        response.status,
                        (await response.text())[:200],
                    )
                    return None

                data = await response.json()

                if not isinstance(data, dict):
                    logger.error("Некорректный ответ API-Football")
                    return None

                if data.get("errors"):
                    logger.error(
                        "Ошибки API-Football: %s",
                        data["errors"],
                    )
                    return None

                await set_api_cache(cache_key, data, ttl)
                return data

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as exc:
        logger.error("Ошибка API-Football: %s", exc)
        return None


def normalize_name(name: str) -> str:
    """Упрощает название команды для сравнения."""
    return "".join(
        char.lower()
        for char in (name or "")
        if char.isalnum()
    )


def parse_total_line(market: str):
    """Извлекает линию тотала из ТБ 2.5 или over_2.5."""

    text = (market or "").lower().replace(",", ".")

    for prefix in ("тб", "over_", "over"):
        if text.startswith(prefix):
            tail = text[len(prefix):].strip().replace("_", "")

            try:
                return float(tail)
            except ValueError:
                return None

    return None


def calculate_result(market: str, total_goals: int):
    """Определяет исход ставки на тотал больше."""

    line = parse_total_line(market)

    if line is None:
        return None

    if total_goals > line:
        return "win"

    if total_goals < line:
        return "loss"

    return "push"


def parse_match_name(match_name: str):
    """Разделяет название матча на две команды."""

    for separator in (" — ", " – ", " - "):
        if separator in match_name:
            home, away = match_name.split(separator, 1)
            return home.strip(), away.strip()

    return None, None


def get_match_date(commence_time):
    """Получает дату матча в UTC."""

    if not commence_time:
        return None

    try:
        start = datetime.fromisoformat(
            commence_time.replace("Z", "+00:00")
        )

        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)

        return start.astimezone(timezone.utc).date()

    except (ValueError, AttributeError):
        return None


async def find_fixture(
    home: str,
    away: str,
    commence_time: str = None,
):
    """Ищет матч в API-Football по командам и дате."""

    match_date = get_match_date(commence_time)

    if match_date is None:
        logger.warning(
            "Нет корректной даты матча %s — %s",
            home,
            away,
        )
        return None

    # Проверяем три даты на случай расхождений
    # между источниками данных.
    for offset in (0, -1, 1):
        date = (
            match_date + timedelta(days=offset)
        ).isoformat()

        data = await api_get(
            "fixtures",
            {
                "date": date,
                "timezone": "UTC",
            },
        )

        if not data:
            continue

        for fixture in data.get("response", []):
            teams = fixture.get("teams") or {}

            api_home = (
                teams.get("home") or {}
            ).get("name", "")

            api_away = (
                teams.get("away") or {}
            ).get("name", "")

            if (
                normalize_name(home) == normalize_name(api_home)
                and normalize_name(away) == normalize_name(api_away)
            ):
                return fixture

    return None


def extract_total_goals(fixture: dict):
    """Извлекает итоговый счёт завершённого матча."""

    fixture_info = fixture.get("fixture") or {}
    status = (fixture_info.get("status") or {}).get("short")

    if status not in FINAL_STATUSES:
        return None

    goals = fixture.get("goals") or {}
    home_goals = goals.get("home")
    away_goals = goals.get("away")

    if home_goals is None or away_goals is None:
        return None

    try:
        return int(home_goals) + int(away_goals)
    except (TypeError, ValueError):
        return None


async def check_results(bot):
    """Проверяет незавершённые сигналы и обновляет результаты."""

    signals = await get_pending_signals_with_time()

    if not signals:
        logger.info("Нет сигналов для проверки")
        return

    logger.info(
        "Проверка результатов: %s сигналов",
        len(signals),
    )

    updated = 0
    fixtures_cache = {}

    now = datetime.now(timezone.utc)

    for signal in signals:
        try:
            (
                signal_id,
                match_name,
                market,
                created_at,
                event_id,
                fixture_id,
                commence_time,
            ) = signal

            match_date = get_match_date(commence_time)

            if match_date is None:
                logger.warning(
                    "Сигнал %s: отсутствует дата матча",
                    signal_id,
                )
                continue

            # Не проверяем будущие матчи.
            if match_date > now.date():
                continue

            home, away = parse_match_name(match_name)

            if not home or not away:
                logger.warning(
                    "Не удалось определить команды: %s",
                    match_name,
                )
                continue

            cache_key = (
                str(fixture_id)
                if fixture_id is not None
                else f"{home}|{away}|{commence_time}"
            )

            if cache_key in fixtures_cache:
                fixture = fixtures_cache[cache_key]

            elif fixture_id is not None:
                data = await api_get(
                    "fixtures",
                    {"id": fixture_id},
                )

                fixtures = (
                    data.get("response", [])
                    if data
                    else []
                )

                fixture = fixtures[0] if fixtures else None
                fixtures_cache[cache_key] = fixture

            else:
                fixture = await find_fixture(
                    home,
                    away,
                    commence_time,
                )
                fixtures_cache[cache_key] = fixture

            if not fixture:
                logger.info(
                    "Матч не найден: %s",
                    match_name,
                )
                continue

            total_goals = extract_total_goals(fixture)

            if total_goals is None:
                continue

            result = calculate_result(
                market,
                total_goals,
            )

            if result is None:
                logger.warning(
                    "Неизвестный рынок: %s",
                    market,
                )
                continue

            await update_signal_result(
                signal_id=signal_id,
                result=result,
                total_goals=total_goals,
            )

            updated += 1

            logger.info(
                "Результат обновлён: %s | %s | %s | голов: %s",
                match_name,
                market,
                result,
                total_goals,
            )

        except Exception:
            logger.exception(
                "Ошибка обработки сигнала: %s",
                signal,
            )

    logger.info(
        "Проверка завершена. Обновлено: %s",
        updated,
    )

    return updated
