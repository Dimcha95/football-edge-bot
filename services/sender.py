import aiohttp
from config import ODDS_API_KEY
from database import get_users_for_signals, save_signal, is_signal_sent, get_today_signals_count

MAX_SIGNALS_PER_DAY = 15

async def get_odds():
    url = "https://api.the-odds-api.com/v4/sports/soccer/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "totals",
        "oddsFormat": "decimal"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"Ошибка API: {resp.status}")
                return []
            return await resp.json()

async def check_and_send(bot):
    print("=== check_and_send вызван ===")
    matches = await get_odds()
    print(f"Матчей: {len(matches)}")
    
    users = await get_users_for_signals()
    if not users:
        print("Нет пользователей")
        return
    
    sent = 0
    for match in matches[:5]:  # Берём только первые 5
        home = match.get("home_team", "Home")
        away = match.get("away_team", "Away")
        match_name = f"{home} — {away}"
        
        # Берём первый тотал, который найдём
        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") == "Over":
                        line = outcome.get("point", 2.5)
                        odds = outcome.get("price", 1.9)
                        
                        text = (
                            f"⚽️ <b>{match_name}</b>\n\n"
                            f"🎯 <b>ТБ {line}</b> @ {odds:.2f}\n"
                            f"📊 Тестовый сигнал"
                        )
                        
                        for user_id in users:
                            try:
                                await bot.send_message(user_id, text, parse_mode="HTML")
                            except Exception as e:
                                print(f"Ошибка отправки: {e}")
                        
                        await save_signal(match_name, "", f"ТБ {line}", odds, 0.5, 0.05)
                        sent += 1
                        print(f"Отправлен: {match_name}")
                        break
                if sent > 0:
                    break
            if sent > 0:
                break
        if sent >= 3:  # Максимум 3 тестовых сигнала
            break
    
    print(f"Отправлено: {sent}")
