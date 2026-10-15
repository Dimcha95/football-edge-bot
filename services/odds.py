
import logging

import aiohttp

from config import ODDS_API_KEY, REGIONS

logger = logging.getLogger(__name__)

API_URL = "https://api.the-odds-api.com/v4/sports/soccer/odds"
SUPPORTED_LINES = {1.5, 2.5, 3.5}
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


async def get_soccer_odds():
    """Получает футбольные коэффициенты из The Odds API."""

    if not ODDS_API_KEY:
        logger.error("ODDS_API_KEY не задан в .env")
        return []

    params = {
        "apiKey": ODDS_API_KEY,
        "regions": REGIONS,
        "markets": "totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    try:
        async with aiohttp.ClientSession(
            timeout=REQUEST_TIMEOUT
        ) as session:
            async with session.get(API_URL, params=params) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.error(
                        "The Odds API: HTTP %s; ответ: %s",
                        response.status,
                        body[:300],
                    )
                    return []

                data = await response.json()

                if not isinstance(data, list):
                    logger.error("The Odds API вернул неожиданный формат")
                    return []

                remaining = response.headers.get(
                    "x-requests-remaining"
                )
                used = response.headers.get("x-requests-used")

                if remaining is not None:
                    logger.info(
                        "The Odds API: осталось запросов %s, использовано %s",
                        remaining,
                        used or "неизвестно",
                    )

                return data

    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.error("Ошибка запроса The Odds API: %s", exc)
        return []


def parse_matches(raw_data: list) -> list:
    """
    Преобразует события API в формат, который использует sender.py.

    event_id — идентификатор The Odds API.
    fixture_id оставляем None: ID API-Football здесь неизвестен.
    """

    matches = []

    for event in raw_data:
        try:
            event_id = event.get("id")
            home = event.get("home_team")
            away = event.get("away_team")

            if not event_id or not home or not away:
                continue

            commence_time = event.get("commence_time")

            best_odds = {}

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "totals":
                        continue

                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") != "Over":
                            continue

                        point = outcome.get("point")
                        price = outcome.get("price")

                        if point is None or price is None:
                            continue

                        try:
                            line = float(point)
                            price = float(price)
                        except (TypeError, ValueError):
                            continue

                        if line not in SUPPORTED_LINES or price <= 1:
                            continue

                        # Выбираем максимальный коэффициент среди букмекеров.
                        if (
                            line not in best_odds
                            or price > best_odds[line]
                        ):
                            best_odds[line] = price

            for line, price in best_odds.items():
                matches.append({
                    "event_id": str(event_id),
                    "fixture_id": None,
                    "home": home,
                    "away": away,
                    "league": event.get("sport_title", "Футбол"),
                    "commence_time": commence_time,
                    "line": line,
                    "odds": price,
                    "home_strength": 1.5,
                    "away_strength": 1.4,
                })

        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("Не удалось разобрать событие: %s", exc)

    return matches


async def get_odds() -> list:
    """Основная функция, вызываемая из services/sender.py."""
    raw_data = await get_soccer_odds()
    matches = parse_matches(raw_data)

    logger.info("Получено вариантов коэффициентов: %s", len(matches))

    return matches
