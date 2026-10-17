"""BSD match results for Football Edge Bot.

Uses BSD event IDs, never API-Football fixture IDs.
Only settles Over 1.5 / 2.5 / 3.5 markets.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from dotenv import load_dotenv

from database import (
    get_pending_signals_with_time,
    update_signal_result,
)

load_dotenv()

logger = logging.getLogger(__name__)

BASE = "https://sports.bzzoiro.com/api/v2"
MAX_PARALLEL = 5

# Через сколько после времени матча отменённый сигнал закрывается как возврат.
VOID_AFTER = timedelta(days=3)

FINAL_STATUSES = {
    "finished", "completed", "final", "ft",
    "full_time", "fulltime", "ended",
}

# Статусы, при которых матч не будет доигран: ставка возвращается.
VOID_STATUSES = {
    "cancelled", "canceled", "abandoned", "void",
}

# Не закрываем сразу: матч может быть сыгран позже.
UNSETTLED_STATUSES = VOID_STATUSES | {
    "postponed", "suspended",
}


def parse_total_line(market):
    """Extract an Over total from supported market names."""
    text = str(market or "").lower().strip()
    text = text.replace(",", ".")

    for prefix in ("тб", "over_", "over ", "over"):
        if text.startswith(prefix):
            tail = text[len(prefix):].strip()
            tail = tail.replace("_", ".")

            try:
                line = float(tail)
            except ValueError:
                return None

            if line in (1.5, 2.5, 3.5):
                return line

    return None


def calculate_result(market, total_goals):
    """Calculate Over market settlement."""
    line = parse_total_line(market)

    if line is None:
        return None

    return "win" if total_goals > line else "loss"


def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except (ValueError, TypeError, AttributeError):
        return None


def _score(value):
    if isinstance(value, bool):
        return None

    try:
        number = int(value)
        if number >= 0 and str(value).strip() == str(number):
            return number
    except (ValueError, TypeError):
        pass

    return None


def get_event(payload):
    """Return the event dict from a BSD payload."""
    if not isinstance(payload, dict):
        return None

    event = payload.get("event", payload)

    return event if isinstance(event, dict) else None


def get_status(event):
    """Normalized lowercase status of an event."""
    status_value = event.get("status", "")

    if isinstance(status_value, dict):
        status_value = (
            status_value.get("name")
            or status_value.get("short")
            or status_value.get("code")
            or ""
        )

    return str(status_value).strip().lower()


def is_void_event(event):
    """True for cancelled / abandoned events (stake is returned)."""
    status = get_status(event)

    if status in VOID_STATUSES:
        return True

    return bool(event.get("cancelled"))


def extract_total_goals(payload):
    """Return total goals only for confirmed finished events."""
    event = get_event(payload)

    if event is None:
        return None

    status = get_status(event)

    if status in UNSETTLED_STATUSES:
        return None

    if event.get("cancelled") or event.get("postponed"):
        return None

    finished = event.get("finished")

    if finished is not True and status not in FINAL_STATUSES:
        return None

    # Score may include extra time: don't settle ambiguously.
    if event.get("extra_time_score") not in (None, ""):
        logger.warning(
            "BSD event %s has extra time; settle manually",
            event.get("id"),
        )
        return None

    # Only use explicitly available full-time scores.
    # Do not infer scores from live or penalty-shootout fields.
    pairs = [
        (event.get("home_score"), event.get("away_score")),
        (event.get("home_goals"), event.get("away_goals")),
    ]

    score_data = event.get("score")

    if isinstance(score_data, dict):
        full_time = (
            score_data.get("full_time")
            or score_data.get("fulltime")
        )

        if isinstance(full_time, dict):
            pairs.append((
                full_time.get("home"),
                full_time.get("away"),
            ))

    for home_raw, away_raw in pairs:
        home = _score(home_raw)
        away = _score(away_raw)

        if home is not None and away is not None:
            return home + away

    logger.warning(
        "BSD finished event has unknown score format: %s",
        str(event)[:500],
    )
    return None


async def fetch_event(session, event_id):
    """Fetch an event by its BSD identifier."""
    url = f"{BASE}/events/{event_id}/"

    try:
        async with session.get(url) as response:
            if response.status != 200:
                logger.warning(
                    "BSD event %s HTTP %s: %s",
                    event_id,
                    response.status,
                    (await response.text())[:200],
                )
                return None

            data = await response.json(content_type=None)

            if not isinstance(data, dict):
                logger.warning(
                    "BSD event %s: unexpected response",
                    event_id,
                )
                return None

            return data

    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as exc:
        logger.error(
            "BSD event %s request failed: %s",
            event_id,
            exc,
        )
        return None


async def check_results(bot=None):
    """Check pending signals and update completed bets."""
    token = os.getenv("BSD_API_KEY", "").strip()

    if not token:
        logger.error("BSD_API_KEY is missing")
        return 0

    signals = await get_pending_signals_with_time()

    if not signals:
        logger.info("No pending signals")
        return 0

    logger.info(
        "BSD checking %s pending signals",
        len(signals),
    )

    now = datetime.now(timezone.utc)
    eligible = []

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

            if not event_id:
                logger.warning(
                    "Signal %s has no BSD event_id",
                    signal_id,
                )
                continue

            kickoff = parse_datetime(commence_time)

            if kickoff is None:
                logger.warning(
                    "Signal %s has invalid kickoff",
                    signal_id,
                )
                continue

            if kickoff > now:
                continue

            eligible.append((
                signal_id,
                match_name,
                market,
                str(event_id),
                kickoff,
            ))

        except (ValueError, TypeError):
            logger.exception(
                "Invalid pending signal row: %s",
                signal,
            )

    if not eligible:
        return 0

    timeout = aiohttp.ClientTimeout(total=25)
    headers = {"Authorization": f"Token {token}"}
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    event_ids = list(dict.fromkeys(
        row[3] for row in eligible
    ))

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout,
    ) as session:

        async def load_event(event_id):
            async with semaphore:
                payload = await fetch_event(
                    session, event_id
                )
                return event_id, payload

        fetched = await asyncio.gather(
            *(load_event(event_id) for event_id in event_ids),
            return_exceptions=True,
        )

    events = {}

    for item in fetched:
        if isinstance(item, Exception):
            logger.error("BSD fetch failed: %r", item)
            continue

        event_id, payload = item
        events[event_id] = payload

    updated = 0

    for signal_id, match_name, market, event_id, kickoff in eligible:
        try:
            payload = events.get(event_id)

            if payload is None:
                continue

            # Protect against accidentally matching another event.
            event = get_event(payload)

            if event is None:
                continue

            returned_id = event.get("id")

            if (
                returned_id is not None
                and str(returned_id) != event_id
            ):
                logger.error(
                    "BSD event ID mismatch: %s != %s",
                    event_id,
                    returned_id,
                )
                continue

            # Отменённый матч: возврат ставки после VOID_AFTER.
            if is_void_event(event):
                if now - kickoff >= VOID_AFTER:
                    await update_signal_result(
                        signal_id=signal_id,
                        result="push",
                        total_goals=None,
                    )
                    updated += 1
                    logger.info(
                        "BSD void: %s | %s | push",
                        match_name,
                        market,
                    )
                continue

            total_goals = extract_total_goals(payload)

            if total_goals is None:
                continue

            result = calculate_result(
                market, total_goals
            )

            if result is None:
                logger.warning(
                    "Unknown market for signal %s: %s",
                    signal_id,
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
                "BSD result: %s | %s | %s | goals=%s",
                match_name,
                market,
                result,
                total_goals,
            )

        except Exception:
            logger.exception(
                "Failed to update signal %s",
                signal_id,
            )

    logger.info(
        "BSD results check finished: %s updated",
        updated,
    )

    return updated
