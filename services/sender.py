
import asyncio
import aiohttp
import re
from datetime import datetime, timezone

from config import (
    API_FOOTBALL_KEY,
    MIN_EDGE,
    MIN_PROBABILITY,
    MAX_SIGNALS_PER_DAY,
)
from database import (
    get_active_users,
    save_signal,
    is_signal_sent,
    get_today_signals_count,
)
from services.model import calculate_over_probability, calculate_edge


API_BASE = "https://v3.football.api-sports.io"

ALLOWED_LINES = {1.5, 2.5, 3.5}
MIN_ODDS = 1.30
MAX_ODDS = 4.00

# Кеш на время работы процесса.
_CACHE = {}
FIXTURES_TTL = 30 * 60
ODDS_TTL = 3 * 60 * 60

# Максимум страниц коэффициентов за один запуск.
MAX_ODDS_PAGES = 3

# Не допускаем одновременного выполнения нескольких проверок.
_CHECK_LOCK = asyncio.Lock()


async def api_get(endpoint, params=None, ttl=1800):
    """Запрос к API-Football с кешированием."""
    if not API_FOOTBALL_KEY:
        print("ОШИБКА: API_FOOTBALL_KEY отсутствует в .env")
        return None

    params = params or {}
    cache_key = (
        endpoint,
        tuple(sorted((str(k), str(v)) for k, v in params.items())),
    )

    now = datetime.now(timezone.utc).timestamp()
    cached = _CACHE.get(cache_key)

    if cached and now - cached["time"] < ttl:
        return cached["data"]

    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    timeout = aiohttp.ClientTimeout(total=25)

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
                    body = await response.text()
                    print(
                        f"API error {response.status}, "
                        f"{endpoint}: {body[:250]}"
                    )
                    return None

                data = await response.json()

                if data.get("errors"):
                    print(f"API errors ({endpoint}): {data['errors']}")
                    return None

                _CACHE[cache_key] = {
                    "time": now,
                    "data": data,
                }

                requests = data.get("get", endpoint)
                remaining = response.headers.get(
                    "x-ratelimit-requests-remaining", "?"
                )
                print(
                    f"API {requests}: HTTP 200, "
                    f"дневной остаток по заголовку: {remaining}"
                )

                return data

    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"Ошибка API ({endpoint}): {exc}")
        return None


async def get_fixtures():
    """Получает расписание на текущую дату UTC."""
    today = datetime.now(timezone.utc).date().isoformat()

    data = await api_get(
        "fixtures",
        {"date": today, "timezone": "UTC"},
        ttl=FIXTURES_TTL,
    )

    if not data:
        return {}

    fixtures = {}

    for item in data.get("response", []):
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        league = item.get("league", {})

        fixture_id = fixture.get("id")
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")

        if not fixture_id or not home or not away:
            continue

        # Не используем матчи, которые уже начались или завершились.
        status = (fixture.get("status") or {}).get("short", "")
        if status != "NS":
            continue

        fixtures[int(fixture_id)] = {
            "home": home,
            "away": away,
            "league": league.get("name", "Футбол"),
            "date": fixture.get("date"),
        }

    print(f"Предстоящих матчей на сегодня: {len(fixtures)}")
    return fixtures


def parse_over_market(bet):
    """Извлекает линии Over 1.5, Over 2.5 и Over 3.5."""
    name = str(bet.get("name", "")).lower()

    if not any(word in name for word in ("over/under", "total", "goals")):
        return []

    found = []

    for value in bet.get("values", []):
        label = str(value.get("value", "")).strip()

        match = re.search(
            r"^over\s*([0-9]+(?:\.[0-9]+)?)$",
            label,
            re.IGNORECASE,
        )

        if not match:
            continue

        try:
            line = float(match.group(1))
            odds = float(value.get("odd"))
        except (TypeError, ValueError):
            continue

        if line not in ALLOWED_LINES:
            continue

        if not MIN_ODDS <= odds <= MAX_ODDS:
            continue

        found.append((line, odds))

    return found


async def get_odds():
    """
    Получает доступные коэффициенты и сопоставляет их
    с предстоящими матчами из /fixtures.
    """
    fixtures = await get_fixtures()

    if not fixtures:
        print("Нет предстоящих матчей для обработки.")
        return []

    today = datetime.now(timezone.utc).date().isoformat()
    best = {}

    for page in range(1, MAX_ODDS_PAGES + 1):
        data = await api_get(
            "odds",
            {"date": today, "page": page},
            ttl=ODDS_TTL,
        )

        if data is None:
            break

        items = data.get("response", [])
        paging = data.get("paging", {})

        print(
            f"Страница odds {page}: записей={len(items)}, "
            f"всего страниц={paging.get('total', '?')}"
        )

        if not items:
            break

        for item in items:
            fixture_id = (item.get("fixture") or {}).get("id")

            if fixture_id is None:
                continue

            fixture_id = int(fixture_id)

            # Коэффициенты принимаем только для матчей,
            # которые найдены в расписании на текущий день.
            fixture = fixtures.get(fixture_id)

            if not fixture:
                continue

            for bookmaker in item.get("bookmakers", []):
                for bet in bookmaker.get("bets", []):
                    for line, odds in parse_over_market(bet):
                        key = (fixture_id, line)

                        # Берём лучший доступный коэффициент.
                        if key not in best or odds > best[key]["odds"]:
                            best[key] = {
                                "fixture_id": fixture_id,
                                "home": fixture["home"],
                                "away": fixture["away"],
                                "league": fixture["league"],
                                "line": line,
                                "odds": odds,
                            }

        total_pages = paging.get("total", page)

        if page >= total_pages:
            break

    result = list(best.values())
    print(f"Найдено ставок Over с коэффициентами: {len(result)}")

    return result


def estimate_probability(line):
    """
    Временная базовая оценка.
    Это НЕ статистический прогноз конкретного матча.
    """
    baseline = {
        1.5: 0.68,
        2.5: 0.52,
        3.5: 0.38,
    }
    return baseline.get(line, 0.30)


async def check_and_send(bot):
    """Проверяет матчи и отправляет подходящие сигналы."""
    if _CHECK_LOCK.locked():
        print("Проверка уже выполняется, пропускаем повторный запуск.")
        return

    async with _CHECK_LOCK:
        print("=== check_and_send ===")

        if not API_FOOTBALL_KEY:
            print("Нет API_FOOTBALL_KEY в конфигурации.")
            return

        today_count = await get_today_signals_count()

        if today_count >= MAX_SIGNALS_PER_DAY:
            print(f"Достигнут лимит сигналов: {MAX_SIGNALS_PER_DAY}")
            return

        users = await get_active_users()

        if not users:
            print("Нет активных пользователей.")
            return

        matches = await get_odds()
        sent = 0

        for item in matches:
            if today_count + sent >= MAX_SIGNALS_PER_DAY:
                print("Достигнут дневной лимит сигналов.")
                break

            home = item["home"]
            away = item["away"]
            league = item["league"]
            line = item["line"]
            odds = item["odds"]

            match_name = f"{home} — {away}"
            market_name = f"ТБ {line:g}"

            if await is_signal_sent(match_name, market_name):
                continue

            # Пока нет подключённой статистики команд,
            # используем базовую вероятность.
            our_prob = estimate_probability(line)
            edge = calculate_edge(our_prob, odds)

            if our_prob < MIN_PROBABILITY:
                continue

            if edge < MIN_EDGE:
                continue

            text = (
                f"⚽️ <b>{match_name}</b>\n"
                f"🏆 {league}\n\n"
                f"🎯 <b>{market_name}</b> @ {odds:.2f}\n"
                f"📊 Базовая вероятность: {our_prob * 100:.0f}%\n"
                f"📈 Расчётный edge: {edge * 100:+.1f}%\n\n"
                f"ℹ️ Вероятность пока базовая, "
                f"статистика команд не учитывается."
            )

            # Сохраняем сигнал перед рассылкой, чтобы
            # не создавать его повторно при повторном запуске.
            await save_signal(
                match_name,
                league,
                market_name,
                odds,
                our_prob,
                edge,
            )

            sent += 1

            for user_id in users:
                try:
                    await bot.send_message(
                        user_id,
                        text,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    print(
                        f"Ошибка отправки пользователю "
                        f"{user_id}: {exc}"
                    )

            print(
                f"Сигнал сохранён: {match_name}, "
                f"{market_name} @ {odds:.2f}"
            )

        print(f"Всего новых сигналов: {sent}")
