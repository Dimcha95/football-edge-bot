"""BSD Football API adapter for Football Edge Bot.

Replace services/odds.py with this file. The sender expects one dict per
match and Over line. BSD event IDs are NOT API-Football fixture IDs.
"""
import asyncio
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

import aiohttp
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
BASE = "https://sports.bzzoiro.com/api/v2"
LINES = {1.5: "over_15_goals", 2.5: "over_25_goals", 3.5: "over_35_goals"}
CACHE_SECONDS = 20 * 60
CACHE_MAX_ITEMS = 2000
PAGE_SIZE = 200
MAX_PARALLEL = 5


def _int_env(name, default):
    try:
        value = int(os.getenv(name, "").strip())
        return value if value > 0 else default
    except ValueError:
        return default


# Меньше матчей = меньше запросов к API. Настраивается через .env.
MAX_EVENTS = _int_env("BSD_MAX_EVENTS", 400)

_CACHE = {}


def _purge_cache():
    """Удаляет просроченные записи, чтобы кэш не рос бесконечно."""
    if len(_CACHE) <= CACHE_MAX_ITEMS:
        return
    now = time.monotonic()
    for key in [k for k, (ts, _) in _CACHE.items() if now - ts >= CACHE_SECONDS]:
        _CACHE.pop(key, None)
    # Если всё ещё слишком много — оставляем самые свежие.
    if len(_CACHE) > CACHE_MAX_ITEMS:
        newest = sorted(_CACHE.items(), key=lambda kv: kv[1][0], reverse=True)
        _CACHE.clear()
        _CACHE.update(newest[:CACHE_MAX_ITEMS])


def _name(value):
    if isinstance(value, dict):
        return str(value.get("name") or value.get("short_name") or value.get("title") or "").strip()
    return str(value or "").strip()


def _kickoff(event):
    return (event.get("event_date") or event.get("start_time") or
            event.get("kickoff") or event.get("date") or
            event.get("match_date") or event.get("scheduled_at") or "")


def _parse_dt(raw):
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _future(raw):
    dt = _parse_dt(raw)
    return dt is not None and dt > datetime.now(timezone.utc)


def _nested_id(event, obj_key, id_key):
    """ID из вложенного объекта (event[obj_key]["id"]) или из плоского поля."""
    obj = event.get(obj_key)
    if isinstance(obj, dict):
        return obj.get("id")
    return event.get(id_key)


async def _get(session, path, params=None):
    key = (path, tuple(sorted((params or {}).items())))
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        async with session.get(BASE + path, params=params) as response:
            if response.status != 200:
                logger.error("BSD %s HTTP %s: %s", path, response.status,
                             (await response.text())[:250])
                return None
            data = await response.json(content_type=None)
            _CACHE[key] = (time.monotonic(), data)
            _purge_cache()
            return data
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.error("BSD request %s failed: %s", path, exc)
        return None


async def get_odds():
    """Return sender-compatible match/line rows for upcoming BSD fixtures."""
    token = os.getenv("BSD_API_KEY", "").strip()
    if not token:
        logger.error("BSD_API_KEY is missing in .env")
        return []

    today = datetime.now(timezone.utc).date()
    params = {"status": "upcoming", "date_from": today.isoformat(),
              "date_to": (today + timedelta(days=1)).isoformat(),
              "limit": PAGE_SIZE, "offset": 0}
    timeout = aiohttp.ClientTimeout(total=25)
    headers = {"Authorization": f"Token {token}"}

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        events = []
        while len(events) < MAX_EVENTS:
            page = await _get(session, "/events/", params)
            if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                logger.error("BSD unexpected events response shape")
                break  # используем то, что уже загружено
            batch = page["results"]
            events.extend(batch)
            if not page.get("next") or not batch:
                break
            params["offset"] += len(batch)

        if not events:
            return []

        eligible = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = event.get("id")
            kickoff = _kickoff(event)
            home = _name(event.get("home_team") or event.get("home_team_name") or event.get("home"))
            away = _name(event.get("away_team") or event.get("away_team_name") or event.get("away"))
            if event_id is None or not home or not away or not _future(kickoff):
                continue
            eligible.append((event, event_id, home, away, kickoff))

        # Ближайшие матчи — первыми; при лимите остаются самые актуальные.
        eligible.sort(key=lambda row: _parse_dt(row[4]) or datetime.max.replace(tzinfo=timezone.utc))
        eligible = eligible[:MAX_EVENTS]

        sem = asyncio.Semaphore(MAX_PARALLEL)

        async def evaluate(row):
            event, event_id, home, away, kickoff = row
            async with sem:
                payload = await _get(session, f"/events/{event_id}/odds/")
            if not isinstance(payload, dict):
                return []
            prices = payload.get("odds") or {}
            if not isinstance(prices, dict):
                return []
            league = _name(event.get("league") or event.get("league_name") or event.get("competition")) or "Football"
            home_id = _nested_id(event, "home_team", "home_team_id")
            away_id = _nested_id(event, "away_team", "away_team_id")
            league_id = _nested_id(event, "league", "league_id")
            result = []
            for line, field in LINES.items():
                try:
                    odd = float(prices.get(field))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(odd) or odd <= 1:
                    continue
                result.append({
                    "event_id": str(event_id),
                    "fixture_id": None,  # BSD IDs must not be used in API-Football /fixtures?id
                    "home": home, "away": away, "league": league,
                    "commence_time": kickoff,
                    "line": line, "odds": odd,
                    "home_id": home_id,
                    "away_id": away_id,
                    "league_id": league_id,
                })
            return result

        groups = await asyncio.gather(
            *(evaluate(row) for row in eligible),
            return_exceptions=True,
        )

        result = []
        for group in groups:
            if isinstance(group, Exception):
                logger.error("BSD odds evaluation failed: %r", group)
                continue
            result.extend(group)

        logger.info("BSD: %d events, %d upcoming, %d total-goals prices",
                    len(events), len(eligible), len(result))
        return result
