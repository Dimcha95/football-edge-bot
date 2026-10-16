
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aiohttp

from config import API_FOOTBALL_KEY
from database import get_api_cache, set_api_cache

logger = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"

HISTORY_DAYS = 45
MAX_HISTORY_REQUESTS = 8
CACHE_TTL = 24 * 60 * 60

MIN_TEAM_MATCHES = 3
MIN_LEAGUE_MATCHES = 10

MIN_EXPECTED_GOALS = 0.25
MAX_EXPECTED_GOALS = 4.0


def safe_divide(a, b, default=0.0):
    if not b:
        return default
    return a / b


def calculate_team_stats(fixtures):
    """
    Собирает домашнюю и выездную статистику
    по завершённым матчам.

    Использует только FT.
    """

    stats = defaultdict(
        lambda: {
            "home_played": 0,
            "home_scored": 0,
            "home_conceded": 0,
            "away_played": 0,
            "away_scored": 0,
            "away_conceded": 0,
        }
    )

    league_totals = defaultdict(
        lambda: {
            "matches": 0,
            "home_goals": 0,
            "away_goals": 0,
        }
    )

    for item in fixtures:
        fixture = item.get("fixture") or {}
        status = (fixture.get("status") or {}).get("short")

        if status != "FT":
            continue

        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        league = item.get("league") or {}

        home_id = (teams.get("home") or {}).get("id")
        away_id = (teams.get("away") or {}).get("id")
        league_id = league.get("id")

        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if any(
            value is None
            for value in (
                home_id,
                away_id,
                league_id,
                home_goals,
                away_goals,
            )
        ):
            continue

        try:
            home_goals = int(home_goals)
            away_goals = int(away_goals)
        except (ValueError, TypeError):
            continue

        key_home = (league_id, home_id)
        key_away = (league_id, away_id)

        stats[key_home]["home_played"] += 1
        stats[key_home]["home_scored"] += home_goals
        stats[key_home]["home_conceded"] += away_goals

        stats[key_away]["away_played"] += 1
        stats[key_away]["away_scored"] += away_goals
        stats[key_away]["away_conceded"] += home_goals

        league_totals[league_id]["matches"] += 1
        league_totals[league_id]["home_goals"] += home_goals
        league_totals[league_id]["away_goals"] += away_goals

    return stats, league_totals


def estimate_expected_goals(
    home_id,
    away_id,
    league_id,
    stats,
    league_totals,
):
    """
    Оценивает голы через относительную
    силу атаки и защиты.

    Возвращает None, если статистики мало.
    """

    home = stats.get((league_id, home_id))
    away = stats.get((league_id, away_id))
    league = league_totals.get(league_id)

    if not home or not away or not league:
        return None

    if league["matches"] < MIN_LEAGUE_MATCHES:
        return None

    if (
        home["home_played"] < MIN_TEAM_MATCHES
        or away["away_played"] < MIN_TEAM_MATCHES
    ):
        return None

    league_home_avg = safe_divide(
        league["home_goals"],
        league["matches"],
    )

    league_away_avg = safe_divide(
        league["away_goals"],
        league["matches"],
    )

    if league_home_avg <= 0 or league_away_avg <= 0:
        return None

    home_attack = safe_divide(
        home["home_scored"],
        home["home_played"],
    ) / league_home_avg

    away_defense = safe_divide(
        away["away_conceded"],
        away["away_played"],
    ) / league_home_avg

    away_attack = safe_divide(
        away["away_scored"],
        away["away_played"],
    ) / league_away_avg

    home_defense = safe_divide(
        home["home_conceded"],
        home["home_played"],
    ) / league_away_avg

    home_xg = (
        league_home_avg
        * home_attack
        * away_defense
    )

    away_xg = (
        league_away_avg
        * away_attack
        * home_defense
    )

    if not (
        math.isfinite(home_xg)
        and math.isfinite(away_xg)
    ):
        return None

    home_xg = max(
        MIN_EXPECTED_GOALS,
        min(MAX_EXPECTED_GOALS, home_xg),
    )

    away_xg = max(
        MIN_EXPECTED_GOALS,
        min(MAX_EXPECTED_GOALS, away_xg),
    )

    return {
        "home_expected_goals": home_xg,
        "away_expected_goals": away_xg,
        "expected_total": home_xg + away_xg,
    }


async def fetch_fixture_history():
    """
    Загружает завершённые матчи за ограниченное
    число дней и кеширует каждый ответ.

    Максимум 8 новых запросов за вызов.
    """

    if not API_FOOTBALL_KEY:
        logger.error("API_FOOTBALL_KEY отсутствует")
        return []

    now = datetime.now(timezone.utc)
    all_fixtures = []

    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
    }

    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout,
    ) as session:

        for days_ago in range(
            1,
            min(HISTORY_DAYS, MAX_HISTORY_REQUESTS) + 1,
        ):
            date = (
                now - timedelta(days=days_ago)
            ).date().isoformat()

            cache_key = f"team_stats:fixtures:{date}"

            data = await get_api_cache(cache_key)

            if data is None:
                try:
                    async with session.get(
                        f"{BASE_URL}/fixtures",
                        params={
                            "date": date,
                            "timezone": "UTC",
                        },
                    ) as response:

                        if response.status != 200:
                            logger.warning(
                                "Ошибка fixtures %s: HTTP %s",
                                date,
                                response.status,
                            )
                            continue

                        data = await response.json()

                        if data.get("errors"):
                            logger.warning(
                                "API-Football: %s",
                                data["errors"],
                            )
                            continue

                        await set_api_cache(
                            cache_key,
                            data,
                            CACHE_TTL,
                        )

                except (
                    aiohttp.ClientError,
                    TimeoutError,
                    ValueError,
                ):
                    logger.exception(
                        "Не удалось загрузить матчи за %s",
                        date,
                    )
                    continue

            all_fixtures.extend(
                data.get("response", [])
            )

    return all_fixtures


async def get_team_statistics():
    """
    Возвращает статистику команд и лиг.
    Не отправляет никаких сигналов.
    """

    fixtures = await fetch_fixture_history()

    stats, league_totals = calculate_team_stats(
        fixtures
    )

    logger.info(
        "Статистика: %s команд/лиг, %s лиг",
        len(stats),
        len(league_totals),
    )

    return stats, league_totals
