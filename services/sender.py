import aiohttp
from config import ODDS_API_KEY
from database import get_users_for_signals, save_signal, is_signal_sent, get_today_signals_count
from services.model import calculate_over_probability, calculate_edge

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
    for match in matches:
        if sent >= 5:
            break
            
        home = match.get("home_team", "Home")
        away = match.get("away_team", "Away")
        league = match.get("sport_title", "")
        match_name = f"{home} — {away}"
        
        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") == "Over":
                        line = outcome.get("point", 2.5)
                        odds = outcome.get("price", 1.9)
                        
                        if odds < 1.3 or odds > 4.0:
                            continue
                        
                        our_prob = calculate_over_probability(1.3, 1.3, 1.2, 1.2, 0.5, 0.5, line)
                        edge = calculate_edge(our_prob, odds)
                        
                        market_name = f"ТБ {line}"
                        if await is_signal_sent(match_name, market_name):
                            continue
                        
                        text = (
                            f"⚽️ <b>{match_name}</b>\n"
                            f"🏆 {league}\n\n"
                            f"🎯 <b>{market_name}</b> @ {odds:.2f}\n"
                            f"📊 Вероятность: <b>{our_prob*100:.0f}%</b>\n"
                            f"📈 Edge: <b>+{edge*100:.1f}%</b>"
                        )
                        
                        for user_id in users:
                            try:
                                await bot.send_message(user_id, text, parse_mode="HTML")
                            except:
                                pass
                        
                        await save_signal(match_name, league, market_name, odds, our_prob, edge)
                        sent += 1
                        print(f"Отправлен: {match_name}")
                        break
                if sent > 0:
                    break
            if sent > 0:
                break
    
    print(f"Отправлено: {sent}")
