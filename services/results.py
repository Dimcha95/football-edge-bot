```python
from datetime import datetime, timedelta, timezone

import aiohttp

from config import API_FOOTBALL_KEY
from database import (
    get_pending_signals_with_time,
    update_signal_result,
    get_api_cache,
    set_api_cache,
)

API_BASE = "https://v3.football.api-sports.io"
CACHE_TTL = 15 * 60
FINAL_STATUSES = {"FT", "AET", "PEN"}


async def api_get(endpoint: str, params: dict, ttl: int = CACHE_TTL):
    if not API_FOOTBALL_KEY:
        print("API_FOOTBALL_KEY не задан")
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
                    print(f"API-Football HTTP {response.status}")
                    return None

                data = await response.json()

                if data.get("errors"):
                    print(f"API-Football errors: {data['errors']}")
                    return None

                await set_api_cache(cache_key, data, ttl)
                return data

    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"Ошибка запроса результатов: {exc}")
        return None


def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def parse_total_line(market: str):
    text = market.lower().replace(",", ".")

    for prefix in ("тб", "over_"):
        if prefix in text:
            tail = text.split(prefix, 1)[1].strip()
            try:
                return float(tail.replace("_", " "))
            except ValueError:
                pass

    if "over" in text:
        tail = text.split("over", 1)[1].strip()
        try:
            return float(tail.replace("_", " "))
        except ValueError:
            pass

    return None


def calculate_result(market: str, total_goals: int):
    line = parse_total_line(market)

    if line is None:
        return None

    if total_goals > line:
        return "win"
    if total_goals < line:
        return "loss"
    return "push"


async def find_fixture(home: str, away: str, commence_time: str = None):
    if commence_time:
        try:
            start = datetime.fromisoformat(
                commence_time.replace("Z", "+00:00")
            )
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            match_date = start.astimezone(timezone.utc).date()
        except ValueError:
            match_date = datetime.now(timezone.utc).date()
    else:
        match_date = datetime.now(timezone.utc).date()

    # Проверяем день матча и соседние даты на случай часового пояса.
    for offset in (-1, 0, 1):
        date = (match_date + timedelta(days=offset)).isoformat()

        data = await api_get(
            "fixtures",
            {"date": date, "timezone": "UTC"},
        )

        if not data:
            continue

        for fixture in data.get("response", []):
            teams = fixture.get("teams") or {}
            api_home = (teams.get("home") or {}).get("name", "")
            api_away = (teams.get("away") or {}).get("name", "")

            # Сопоставляем только полные нормализованные названия.
            if (
                normalize_name(home) == normalize_name(api_home)
                and normalize_name(away) == normalize_name(api_away)
            ):
                return fixture

    return None


async def check_results(bot):
    signals = await get_pending_signals_with_time()

    if not signals:
        print("Нет сигналов для проверки")
        return

    print(f"Проверяю результатов: {len(signals)}")
    updated = 0

    for signal in signals:
       
