
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import aiohttp

from config import API_FOOTBALL_KEY
from database import get_api_cache, set_api_cache

logger = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"

# API-Football: Goals Over/Under
TOTALS_BET_ID = 5
SUPPORTED_LINES = {1.5, 2.5, 3.5}

# Ограничиваем расходы бесплатного тарифа.
MAX_PAGES_PER_DATE = 2
DAYS_AHEAD = 2
CACHE_TTL = 3 * 60 * 60

TIMEOUT = aiohttp.ClientTimeout(total=25)


async def api_get(session, endpoint, params):
    """Запрос API-Football с кешированием."""

    cache_key = (
        f"odds_v2:{endpoint}:"
        f"{sorted(params.items())}"
    )

    cached = await get_api_cache(cache_key)
    if cached is not None:
        return cached

    if not API_FOOTBALL_KEY:
        logger.error("API_FOOTBALL_KEY не задан")
        return None

    try:
        async with session.get(
            f"{API_BASE}/{endpoint.lstrip('/')}",
            params=params,
        ) as response:

            remaining = response.headers.get(
                "x-ratelimit-requests-remaining"
            )

            if remaining is not None:
                logger.info(
                    "API-Football: осталось запросов %s",
                    remaining,
                )

            if response.status != 200:
                logger.error(
                    "API-Football HTTP %s: %s",
                    response.status,
                    (await response.text())[:200],
                )
                return None

            data = await response.json()

            if not isinstance(data, dict):
                logger.error("Неожиданный ответ API")
                return None

            if data.get("errors"):
                logger.error(
                    "API-Football errors: %s",
                    data["errors"],
                )
                return None

            await set_api_cache(
                cache_key,
                data,
                CACHE_TTL,
            )

            return data

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as exc:
        logger.error("Ошибка API-Football: %s", exc)
        return None


def parse_over_line(value):
    """
    Распознаёт варианты:
    Over 1.5
    Over 2.5
    Over 3.5
    """

    text = str(value or "").strip()

    match = re.fullmatch(
        r"Over\s*([0-9]+(?:[.,][0-9]+)?)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    try:
        line = float(
            match.group(1).replace(",", ".")
        )
    except ValueError:
        return None

    if line not in SUPPORTED_LINES:
        return None

    return line


def parse_odds(raw_data, fixture_map):
    """Преобразует коэффициенты в формат sender.py."""

    best_odds = {}

    for item in raw_data:
        fixture_info = item.get("fixture") or {}
        fixture_id = fixture_info.get("id")

        if fixture_id is None:
            continue

        try:
            fixture_id = int(fixture_id)
        except (TypeError, ValueError):
            continue

        match = fixture_map.get(fixture_id)

        if not match:
            continue

        for bookmaker in item.get("bookmakers") or []:
            for bet in bookmaker.get("bets") or []:

                try:
                    bet_id = int(bet.get("id"))
                except (TypeError, ValueError):
                    continue

                if bet_id != TOTALS_BET_ID:
                    continue

                for outcome in bet.get("values") or []:

                    line = parse_over_line(
                        outcome.get("value")
                    )

                    if line is None:
                        continue

                    try:
                        price = float(
                            outcome.get("odd")
                        )
                    except (TypeError, ValueError):
                        continue

                    if price <= 1:
                        continue

                    key = (fixture_id, line)

                    if (
                        key not in best_odds
                        or price > best_odds[key]
                    ):
                        best_odds[key] = price

    matches = []

    for (fixture_id, line), price in best_odds.items():
        match = fixture_map[fixture_id]

        matches.append({
            "event_id": f"api_football_{fixture_id}",
            "fixture_id": fixture_id,
            "home": match["home"],
            "away": match["away"],
            "league": match["league"],
            "commence_time": match["commence_time"],
            "line": line,
            "odds": price,

            # Временные значения для текущего sender.py.
            # Позже заменим реальной моделью команд.
            "home_strength": 1.5,
            "away_strength": 1.4,
        })

    return matches


async def get_odds():
    """Получает прематчевые тоталы API-Football."""

    if not API_FOOTBALL_KEY:
        logger.error("Отсутствует API_FOOTBALL_KEY")
        return []

    now = datetime.now(timezone.utc)

    all_odds = []
    fixture_map = {}

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
    }

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=TIMEOUT,
    ) as session:

        for day_offset in range(DAYS_AHEAD):

            date = (
                now + timedelta(days=day_offset)
            ).date().isoformat()

            # Получаем матчи на выбранную дату.
            fixtures_data = await api_get(
                session,
                "fixtures",
                {
                    "date": date,
                    "timezone": "UTC",
                },
            )

            if not fixtures_data:
                logger.warning(
                    "Не удалось получить матчи на %s",
                    date,
                )
                continue

            for item in fixtures_data.get("response", []):
                fixture = item.get("fixture") or {}
                teams = item.get("teams") or {}
                league = item.get("league") or {}

                fixture_id = fixture.get("id")
                commence_time = fixture.get("date")

                if fixture_id is None or not commence_time:
                    continue

                try:
                    fixture_id = int(fixture_id)

                    kickoff = datetime.fromisoformat(
                        commence_time.replace(
                            "Z", "+00:00"
                        )
                    )

                    if kickoff.tzinfo is None:
                        kickoff = kickoff.replace(
                            tzinfo=timezone.utc
                        )

                except (ValueError, TypeError):
                    continue

                # Только будущие матчи.
                if kickoff <= now:
                    continue

                home = (
                    teams.get("home") or {}
                ).get("name")

                away = (
                    teams.get("away") or {}
                ).get("name")

                if not home or not away:
                    continue

                fixture_map[fixture_id] = {
                    "home": home,
                    "away": away,
                    "league": league.get(
                        "name", "Футбол"
                    ),
                    "commence_time": commence_time,
                }

            # Коэффициенты: максимум 2 страницы.
            for page in range(
                1,
                MAX_PAGES_PER_DATE + 1,
            ):

                odds_data = await api_get(
                    session,
                    "odds",
                    {
                        "date": date,
                        "bet": TOTALS_BET_ID,
                        "page": page,
                    },
                )

                if not odds_data:
                    break

                all_odds.extend(
                    odds_data.get("response", [])
                )

                paging = odds_data.get("paging") or {}

                try:
                    total_pages = int(
                        paging.get("total", 1)
                    )
                except (ValueError, TypeError):
                    total_pages = 1

                if page >= total_pages:
                    break

    matches = parse_odds(
        all_odds,
        fixture_map,
    )

    logger.info(
        "API-Football: найдено %s вариантов тоталов",
        len(matches),
    )

    return matches


async def get_soccer_odds():
    """Совместимость со старым названием функции."""
    return await get_odds()
