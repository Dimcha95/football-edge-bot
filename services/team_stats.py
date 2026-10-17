"""BSD-only finished-match collection and team goal statistics."""
import asyncio
import logging
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aiohttp
from dotenv import load_dotenv
from database import save_bsd_finished_event, get_bsd_match_history

load_dotenv()
logger = logging.getLogger(__name__)
BASE_URL = "https://sports.bzzoiro.com/api/v2"

# Минимум матчей для оценки. Мало данных = шумные оценки и ложные сигналы.
MIN_TEAM_MATCHES = 4
MIN_LEAGUE_MATCHES = 20
MIN_EXPECTED_GOALS = 0.25
MAX_EXPECTED_GOALS = 4.0

# Сглаживание: оценка команды смешивается со средним по лиге, как будто
# у команды есть ещё SHRINKAGE_MATCHES «средних» матчей.
SHRINKAGE_MATCHES = 4

# История: один раз за запуск грузим INITIAL_LOOKBACK_DAYS, дальше — свежие дни.
INITIAL_LOOKBACK_DAYS = 90
RECENT_LOOKBACK_DAYS = 2
PAGE_SIZE = 200
MAX_PAGES = 100
MAX_SAVED_PER_RUN = 20000

# Готовая статистика кэшируется в памяти, чтобы не дёргать API каждый час.
STATS_CACHE_SECONDS = 3 * 60 * 60

_BACKFILL_DONE = False
_STATS_CACHE = None  # (timestamp, stats, league_totals)


def safe_divide(a, b, default=0.0):
    return a / b if b else default


def shrunk_average(total, played, league_avg):
    """Среднее команды, подтянутое к среднему по лиге."""
    return (total + SHRINKAGE_MATCHES * league_avg) / (played + SHRINKAGE_MATCHES)


def calculate_team_stats(fixtures):
    stats = defaultdict(lambda: {
        "home_played": 0, "home_scored": 0, "home_conceded": 0,
        "away_played": 0, "away_scored": 0, "away_conceded": 0,
    })
    league_totals = defaultdict(lambda: {
        "matches": 0, "home_goals": 0, "away_goals": 0,
    })
    seen = set()
    for item in fixtures:
        if not isinstance(item, dict) or item.get("status") != "finished":
            continue
        event_id = item.get("event_id")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        try:
            league_id = int(item["league_id"])
            home_id = int(item["home_id"])
            away_id = int(item["away_id"])
            home_goals = int(item["home_goals"])
            away_goals = int(item["away_goals"])
        except (KeyError, ValueError, TypeError):
            continue
        if min(league_id, home_id, away_id) <= 0 or home_id == away_id:
            continue
        if min(home_goals, away_goals) < 0:
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
    try:
        league_id, home_id, away_id = map(int, (league_id, home_id, away_id))
    except (ValueError, TypeError):
        return None
    home = stats.get((league_id, home_id))
    away = stats.get((league_id, away_id))
    league = league_totals.get(league_id)
    if not home or not away or not league or league["matches"] < MIN_LEAGUE_MATCHES:
        return None
    if home["home_played"] < MIN_TEAM_MATCHES or away["away_played"] < MIN_TEAM_MATCHES:
        return None
    league_home_avg = safe_divide(league["home_goals"], league["matches"])
    league_away_avg = safe_divide(league["away_goals"], league["matches"])
    if league_home_avg <= 0 or league_away_avg <= 0:
        return None

    # Сглаженные средние: гол за матч / пропущено за матч.
    home_scored_avg = shrunk_average(home["home_scored"], home["home_played"], league_home_avg)
    home_conceded_avg = shrunk_average(home["home_conceded"], home["home_played"], league_away_avg)
    away_scored_avg = shrunk_average(away["away_scored"], away["away_played"], league_away_avg)
    away_conceded_avg = shrunk_average(away["away_conceded"], away["away_played"], league_home_avg)

    home_attack = home_scored_avg / league_home_avg
    away_defense = away_conceded_avg / league_home_avg
    away_attack = away_scored_avg / league_away_avg
    home_defense = home_conceded_avg / league_away_avg

    home_xg = league_home_avg * home_attack * away_defense
    away_xg = league_away_avg * away_attack * home_defense
    if not all(map(math.isfinite, (home_xg, away_xg))):
        return None
    home_xg = max(MIN_EXPECTED_GOALS, min(MAX_EXPECTED_GOALS, home_xg))
    away_xg = max(MIN_EXPECTED_GOALS, min(MAX_EXPECTED_GOALS, away_xg))
    return {"home_expected_goals": home_xg,
            "away_expected_goals": away_xg,
            "expected_total": home_xg + away_xg}


async def fetch_fixture_history():
    """Collect BSD finished events; return all locally saved BSD history.

    The server's date filtering/pagination must be verified in VPS logs.
    """
    global _BACKFILL_DONE

    existing = await get_bsd_match_history()
    token = os.getenv("BSD_API_KEY", "").strip()
    if not token:
        logger.warning("BSD_API_KEY missing; using saved history (%d)", len(existing))
        return existing
    today = datetime.now(timezone.utc).date()

    oldest = None
    for item in existing:
        raw = str(item.get("match_date") or "")[:10]
        try:
            day = datetime.fromisoformat(raw).date()
        except ValueError:
            continue
        if oldest is None or day < oldest:
            oldest = day
    target = today - timedelta(days=INITIAL_LOOKBACK_DAYS)
    need_backfill = (not _BACKFILL_DONE
                     and (oldest is None or oldest > target + timedelta(days=1)))
    days = INITIAL_LOOKBACK_DAYS if need_backfill else RECENT_LOOKBACK_DAYS
    logger.info("BSD history lookback: %d days", days)
    params = {
        "status": "finished",
        "date_from": (today - timedelta(days=days)).isoformat(),
        "date_to": today.isoformat(),
        "limit": PAGE_SIZE,
        "offset": 0,
    }
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"Authorization": f"Token {token}"}
    saved = 0
    completed = False
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        for _ in range(MAX_PAGES):
            try:
                async with session.get(BASE_URL + "/events/", params=params) as response:
                    if response.status != 200:
                        logger.warning("BSD history HTTP %s: %s", response.status,
                                       (await response.text())[:200])
                        break
                    data = await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                logger.error("BSD history request failed: %s", exc)
                break
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                logger.error("BSD history: unexpected response shape")
                break
            batch = data["results"]
            if not batch:
                completed = True
                break
            for event in batch:
                try:
                    if await save_bsd_finished_event(event):
                        saved += 1
                except Exception:
                    logger.exception("Could not store BSD event")
                if saved >= MAX_SAVED_PER_RUN:
                    break
            if saved >= MAX_SAVED_PER_RUN:
                break
            if not data.get("next"):
                completed = True
                break
            params["offset"] += len(batch)

    # Полный бэкфилл больше не повторяем до перезапуска процесса.
    if need_backfill and completed:
        _BACKFILL_DONE = True

    history = await get_bsd_match_history()
    logger.info("BSD history: processed %d, stored total %d", saved, len(history))
    return history


async def get_team_statistics():
    global _STATS_CACHE

    if _STATS_CACHE and time.monotonic() - _STATS_CACHE[0] < STATS_CACHE_SECONDS:
        return _STATS_CACHE[1], _STATS_CACHE[2]

    fixtures = await fetch_fixture_history()
    stats, league_totals = calculate_team_stats(fixtures)
    logger.info("BSD stats: %d team/league pairs, %d leagues", len(stats), len(league_totals))

    # Пустой результат не кэшируем, чтобы повторить попытку при следующем запуске.
    if stats and league_totals:
        _STATS_CACHE = (time.monotonic(), stats, league_totals)

    return stats, league_totals
