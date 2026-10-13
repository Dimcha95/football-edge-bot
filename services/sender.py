import aiohttp
import aiosqlite
from config import ODDS_API_KEY, API_FOOTBALL_KEY
from database import (
    get_users_for_signals, save_signal, is_signal_sent, 
    get_today_signals_count, update_signal_result
)
from services.model import calculate_over_probability, calculate_edge
from services.stats import get_team_stats

MAX_SIGNALS_PER_DAY = 30

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

async def get_scores_odds_api():
    url = "https://api.the-odds-api.com/v4/sports/soccer/scores"
    params = {"apiKey": ODDS_API_KEY, "daysFrom": 1}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                return await resp.json()
    except:
        return []

async def get_scores_api_football():
    if not API_FOOTBALL_KEY:
        return []
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"date": "2026-10-13", "status": "FT"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("response", [])
    except:
        return []

async def update_results():
    print("Обновляю результаты...")
    odds_scores = await get_scores_odds_api()
    for game in odds_scores:
        if not game.get("completed"):
            continue
        home = game.get("home_team", "").lower()
        away = game.get("away_team", "").lower()
        home_score = away_score = 0
        for score in game.get("scores", []):
            name = score.get("name", "").lower()
            if name == home:
                home_score = int(score.get("score", 0))
            elif name == away:
                away_score = int(score.get("score", 0))
        total = home_score + away_score
        await _update_matching_signals(home, away, total)
    
    football_scores = await get_scores_api_football()
    for fixture in football_scores:
        teams = fixture.get("teams", {})
        home = teams.get("home", {}).get("name", "").lower()
        away = teams.get("away", {}).get("name", "").lower()
        goals = fixture.get("goals", {})
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0
        total = home_goals + away_goals
        await _update_matching_signals(home, away, total)

async def _update_matching_signals(home: str, away: str, total_goals: int):
    async with aiosqlite.connect("edge_bot.db") as db:
        cursor = await db.execute(
            "SELECT match_name, market FROM signals WHERE result = 'pending'"
        )
        pending = await cursor.fetchall()
    
    for db_match, market in pending:
        db_lower = db_match.lower()
        if home in db_lower or away in db_lower:
            try:
                line = float(market.replace("ТБ ", "").replace("ТМ ", ""))
            except:
                continue
            if "ТБ" in market:
                result = "win" if total_goals > line else "lose"
            else:
                result = "win" if total_goals < line else "lose"
            await update_signal_result(db_match, market, result)
            print(f"Обновлён: {db_match} → {result} ({total_goals} голов)")

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
        
        home_stats = await get_team_stats(home)
        away_stats = await get_team_stats(away)
        
        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    
                    line = outcome.get("point", 2.5)
                    odds = outcome.get("price", 0)
                    
                    if odds < 1.30 or odds > 4.0:
                        continue
                    
                    our_prob = calculate_over_probability(
                        home_stats["avg_goals"], away_stats["avg_goals"],
                        home_stats["avg_conceded"], away_stats["avg_conceded"],
                        home_stats["form"], away_stats["form"],
                        line,
                        home_stats.get("xg", 1.3),
                        away_stats.get("xg", 1.3)
                    )
                    edge = calculate_edge(our_prob, odds)
                    
                    if edge < 0.05:
                        continue
                    
                    market_name = f"ТБ {line}"
                    if await is_signal_sent(match_name, market_name):
                        continue
                    
                    text = (
                        f"⚽️ <b>{match_name}</b>\n"
                        f"🏆 {league}\n\n"
                        f"🎯 <b>{market_name}</b> @ {odds:.2f}\n"
                        f"📊 Вероятность: <b>{our_prob*100:.0f}%</b>\n"
                        f"📈 Edge: <b>+{edge*100:.1f}%</b>\n"
                        f"📝 xG: {home_stats.get('xg', 0):.1f} / {away_stats.get('xg', 0):.1f}"
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
    
    print(f"Отправлено: {sent}")
