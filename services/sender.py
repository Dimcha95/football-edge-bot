import aiohttp
from config import ODDS_API_KEY
from database import get_users_for_signals, save_signal, is_signal_sent, get_today_signals_count
from services.model import calculate_over_probability, calculate_edge, is_value_bet

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
    print("Проверяю матчи...")
    
    today_count = await get_today_signals_count()
    if today_count >= MAX_SIGNALS_PER_DAY:
        print(f"Лимит: {today_count}")
        return
    
    matches = await get_odds()
    print(f"Матчей: {len(matches)}")
    users = await get_users_for_signals()
    if not users:
        print("Нет пользователей")
        return
    
    sent = 0
    for match in matches:
        if today_count + sent >= MAX_SIGNALS_PER_DAY:
            break
        
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        league = match.get("sport_title", "")
        match_name = f"{home} — {away}"
        
        # Временные значения без API-Football
        home_stats = {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5}
        away_stats = {"avg_goals": 1.3, "avg_conceded": 1.2, "form": 0.5}
        
        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    print(f"Outcome: {outcome.get('name')} | point={outcome.get('point')} | price={outcome.get('price')}")
                    if outcome.get("name") != "Over":
                        continue
                    
                    line = outcome.get("point", 2.5)
                    odds = outcome.get("price", 0)
                    
                    if odds < 1.20 or odds > 5.0:
                        continue
                    
                    our_prob = calculate_over_probability(
                        home_stats["avg_goals"], away_stats["avg_goals"],
                        home_stats["avg_conceded"], away_stats["avg_conceded"],
                        home_stats["form"], away_stats["form"],
                        line
                    )
                    edge = calculate_edge(our_prob, odds)
                    
                    print(f"{match_name} | ТБ {line} | odds={odds:.2f} | prob={our_prob:.2f} | edge={edge:.3f}")
                    
                    if edge < 0.01:
                        continue
                    
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
                    break
    
    print(f"Отправлено: {sent}")
