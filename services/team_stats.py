"""BSD-only finished-match collection and team goal statistics."""
import asyncio
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aiohttp
from dotenv import load_dotenv
from database import save_bsd_finished_event, get_bsd_match_history

load_dotenv()
logger = logging.getLogger(__name__)
BASE_URL = "https://sports.bzzoiro.com/api/v2"
MIN_TEAM_MATCHES = 2
MIN_LEAGUE_MATCHES = 5
MIN_EXPECTED_GOALS = 0.25
MAX_EXPECTED_GOALS = 4.0
# First collection: 14 days. Subsequent calls: recent 2 days.
INITIAL_LOOKBACK_DAYS = 14
RECENT_LOOKBACK_DAYS = 2
PAGE_SIZE = 200
MAX_PAGES = 10
MAX_SAVED_PER_RUN = 1500


def safe_divide(a, b, default=0.0):
    return a / b if b else default


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
    return {"home_expected_goals": home_xg,
            "away_expected_goals": away_xg,
            "expected_total": home_xg + away_xg}


async def fetch_fixture_history():
    """Collect BSD finished events; return all locally saved BSD history.

    The server's date filtering/pagination must be verified in VPS logs.
    """
    existing = await get_bsd_match_history()
    token = os.getenv("BSD_API_KEY", "").strip()
    if not token:
        logger.warning("BSD_API_KEY missing; using saved history (%d)", len(existing))
        return existing
    today = datetime.now(timezone.utc).date()
    days = INITIAL_LOOKBACK_DAYS if not existing else RECENT_LOOKBACK_DAYS
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
                break
            for event in batch:
                try:
                    if await save_bsd_finished_event(event):
                        saved += 1
                except Exception:
                    logger.exception("Could not store BSD event")
                if saved >= MAX_SAVED_PER_RUN:
                    break
            if saved >= MAX_SAVED_PER_RUN or not data.get("next"):
                break
            params["offset"] += len(batch)
    history = await get_bsd_match_history()
    logger.info("BSD history: processed %d, stored total %d", saved, len(history))
    return history


async def get_team_statistics():
    fixtures = await fetch_fixture_history()
    stats, league_totals = calculate_team_stats(fixtures)
    logger.info("BSD stats: %d team/league pairs, %d leagues", len(stats), len(league_totals))
    return stats, league_totals
