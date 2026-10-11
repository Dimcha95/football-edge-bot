import aiohttp
import os
from datetime import datetime, timedelta
from database import get_pending_signals_with_time, update_signal_result

API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = "https://v3.football.api-sports.io"

async def search_match(home: str, away: str, date: str):
    """Ищет матч по названиям команд и дате"""
    if not API_KEY:
        return None

    headers = {"x-apisports-key": API_KEY}
    params = {
        "date": date,
        "timezone": "UTC"
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/fixtures", headers=headers, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            
            for fixture in data.get("response", []):
                teams = fixture.get("teams", {})
                home_name = teams.get("home", {}).get("name", "").lower()
                away_name = teams.get("away", {}).get("name", "").lower()
                
                if home.lower() in home_name or home_name in home.lower():
                    if away.lower() in away_name or away_name in away.lower():
                        return fixture
    return None


async def check_results(bot):
    if not API_KEY:
        print("API_FOOTBALL_KEY не задан")
        return

    signals = await get_pending_signals_with_time()
    if not signals:
        return

    print(f"Проверяю {len(signals)} сигналов...")
    updated = 0

    for signal in signals:
        signal_id, match_name, market, created_at = signal
        
        try:
            # Разбираем название матча
            if " - " not in match_name:
                continue
            home, away = match_name.split(" - ", 1)
            
            # Дата матча
            match_date = created_at[:10]
            
            fixture = await search_match(home.strip(), away.strip(), match_date)
            if not fixture:
                continue

            # Проверяем, закончился ли матч
            status = fixture.get("fixture", {}).get("status", {}).get("short")
            if status not in ("FT", "AET", "PEN"):
                continue

            goals = fixture.get("goals", {})
            home_goals = goals.get("home", 0) or 0
            away_goals = goals.get("away", 0) or 0
            total_goals = home_goals + away_goals

            # Определяем результат для тоталов
            result = None
            if "ТБ 1.5" in market or "over_15" in market.lower():
                result = "win" if total_goals > 1.5 else "lose"
            elif "ТБ 2.5" in market or "over_25" in market.lower():
                result = "win" if total_goals > 2.5 else "lose"
            elif "ТБ 3.5" in market or "over_35" in market.lower():
                result = "win" if total_goals > 3.5 else "lose"

            if result:
                await update_signal_result(signal_id, result)
                updated += 1
                print(f"Сигнал {signal_id}: {result} ({total_goals} голов)")

        except Exception as e:
            print(f"Ошибка при проверке сигнала {signal_id}: {e}")

    print(f"Обновлено результатов: {updated}")
