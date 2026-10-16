"""Накопление результатов API-Football и расчёт статистики из SQLite."""

import asyncio
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aiohttp

from config import API_FOOTBALL_KEY
from database import (
    get_api_cache,
    set_api_cache,
    save_finished_fixture,
    get_saved_fixture_history,
)

logger = logging.getLogger(__name__)
BASE_URL = "https://v3.football.api-sports.io"
CACHE_TTL = 60 * 60  # Кеш успешных ответов на час.
MIN_TEAM_MATCHES = 3
MIN_LEAGUE_MATCHES = 10
MIN_EXPECTED_GOALS = 0.25
MAX_EXPECTED_GOALS = 4.0


def safe_divide(a, b, default=0.0):
    return a / b if b else default


def calculate_team_stats(fixtures):
    """Статистика домашних/выездных матчей из завершённых игр."""
    stats = defaultdict(lambda: {
        "home_played": 0, "home_scored": 0, "home_conceded": 0,
        "away_played": 0, "away_scored": 0, "away_conceded": 0,
    })
    league_totals = defaultdict(lambda: {
        "matches": 0, "home_goals": 0, "away_goals": 0,
    })
    seen_ids = set()

    for item in fixtures:
        fixture = item.get("fixture") or {}
        if (fixture.get("status") or {}).get("short") != "FT":
            continue
        fixture_id = fixture.get("id")
        if fixture_id is not None:
            if fixture_id in seen_ids:
                continue
            seen_ids.add(fixture_id)

        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        league_id = (item.get("league") or {}).get("id")
        home_id = (teams.get("home") or {}).get("id")
        away_id = (teams.get("away") or {}).get("id")
        home_goals = goals.get("home")
        away_goals = goals.get("away")
        if any(v is None for v in (
            league_id, home_id, away_id, home_goals, away_goals,
        )):
            continue
        try:
            league_id, home_id, away_id = map(
                int, (league_id, home_id, away_id)
            )
            home_goals, away_goals = int(home_goals), int(away_goals)
        except (ValueError, TypeError):
            continue
        if home_goals < 0 or away_goals < 0:
            continue

        home = stats[(league_id, home_id)]
        away = stats[(league_id, away_id)]
        home["home_played"] += 1
        home["home_scored"] += home_goals
        home["home_conceded"] += away_goals
        away["away_played"] += 1
        away["away_scored"] += away_goals
        away["away_conceded"] += home_goals
        league = league_totals[league_id]
        league["matches"] += 1
        league["home_goals"] += home_goals
        league["away_goals"] += away_goals

    return stats, league_totals


def estimate_expected_goals(home_id, away_id, league_id, stats, league_totals):
    """Ожидаемые голы по относительной результативности команд."""
    try:
        league_id, home_id, away_id = map(int, (league_id, home_id, away_id))
    except (ValueError, TypeError):
        return None
    home = stats.get((league_id, home_id))
    away = stats.get((league_id, away_id))
    league = league_totals.get(league_id)
    if not home or not away or not league:
        return None
    if league["matches"] < MIN_LEAGUE_MATCHES:
        return None
    if (home["home_played"] < MIN_TEAM_MATCHES
            or away["away_played"] < MIN_TEAM_MATCHES):
        return None

    league_home_avg = safe_divide(league["home_goals"], league["matches"])
    league_away_avg = safe_divide(league["away_goals"], league["matches"])
    if league_home_avg <= 0 or league_away_avg <= 0:
        return None

    home_attack = safe_divide(home["home_scored"], home["home_played"]) / league_home_avg
    away_defense = safe_divide(away["away_conceded"], away["away_played"]) / league_home_avg
    away_attack = safe_divide(away["away_scored"], away["away_played"]) / league_away_avg
    home_defense = safe_divide(home["home_conceded"], home["home_played"]) / league_away_avg

    home_xg = league_home_avg * home_attack * away_defense
    away_xg = league_away_avg * away_attack * home_defense
    if not all(map(math.isfinite, (home_xg, away_xg))):
        return None
    home_xg = max(MIN_EXPECTED_GOALS, min(MAX_EXPECTED_GOALS, home_xg))
    away_xg = max(MIN_EXPECTED_GOALS, min(MAX_EXPECTED_GOALS, away_xg))
    return {
        "home_expected_goals": home_xg,
        "away_expected_goals": away_xg,
        "expected_total": home_xg + away_xg,
    }


async def fetch_fixture_history():
    """Сохраняет доступные FT-матчи за сегодня и вчера; читает всю историю из БД.

    Вызывается планировщиком отдельно от рассылки сигналов.
    """
    if not API_FOOTBALL_KEY:
        logger.warning("API_FOOTBALL_KEY не настроен; читаем сохранённую историю")
        return await get_saved_fixture_history()

    today = datetime.now(timezone.utc).date()
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    timeout = aiohttp.ClientTimeout(total=25)
    saved = 0

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        for day in (today - timedelta(days=1), today):
            date_str = day.isoformat()
            cache_key = f"team_stats:fixtures:collect:v1:{date_str}"
            data = await get_api_cache(cache_key)
            if data is None:
                try:
                    async with session.get(
                        f"{BASE_URL}/fixtures",
                        params={"date": date_str, "timezone": "UTC"},
                    ) as response:
                        if response.status != 200:
                            logger.warning("Fixtures %s: HTTP %s", date_str, response.status)
                            continue
                        data = await response.json()
                        if not isinstance(data, dict) or data.get("errors"):
                            logger.warning("Fixtures %s: API не разрешил дату: %s", date_str,
                                           data.get("errors") if isinstance(data, dict) else "bad response")
                            continue
                        await set_api_cache(cache_key, data, CACHE_TTL)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    logger.exception("Не удалось получить результаты за %s", date_str)
                    continue

            for item in data.get("response", []):
                if (item.get("fixture") or {}).get("status", {}).get("short") != "FT":
                    continue
                try:
                    if await save_finished_fixture(item):
                        saved += 1
                except Exception:
                    logger.exception("Не удалось сохранить матч")

    fixtures = await get_saved_fixture_history()
    logger.info("История: обработано FT=%s; всего в SQLite=%s", saved, len(fixtures))
    return fixtures


async def get_team_statistics():
    fixtures = await fetch_fixture_history()
    stats, league_totals = calculate_team_stats(fixtures)
    logger.info("Статистика: %s команд/лиг, %s лиг", len(stats), len(league_totals))
    return stats, league_totals
