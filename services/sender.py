import aiohttp
from datetime import datetime
from config import ODDS_API_KEY, MIN_EDGE, MIN_PROBABILITY, MAX_SIGNALS_PER_DAY
from database import get_active_users, save_signal, is_signal_sent, get_today_signals_count
from services.model import calculate_over_probability, calculate_edge, is_value_bet

async def get_odds():
    url = f"https://api.the-odds-api.com/v4/sports/soccer/odds?regions=eu&markets=totals&apiKey={ODDS_API_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"Odds API error: {resp.status}")
                return []
            return await resp.json()

async def check_and_send(bot):
    print("=== check_and_send вызван ===")
    matches = await get_odds()
    print(f"Матчей: {len(matches)}")

    today_count = await get_today_signals_count()
    if today_count >= MAX_SIGNALS_PER_DAY:
        print(f"Лимит на сегодня ({MAX_SIGNALS_PER_DAY}) достигнут")
        return

    users = await get_active_users()
    if not users:
        print("Нет активных пользователей")
        return

    sent = 0
    for match in matches:
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        league = match.get("sport_title", "")
        match_name = f"{home} — {away}"

        for bookmaker in match.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "totals":
                    continue
                for outcome in market.get("outcomes", []):
                    if "over" not in outcome.get("name", "").lower():
                        continue

                    line = outcome.get("point")
                    odds = outcome.get("price")
                    if not line or not odds:
                        continue

                    market_name = f"ТБ {line}"

                    if await is_signal_sent(match_name, market_name):
                        continue

                    # Простая модель (можно улучшить)
                    avg_goals = 2.6
                    avg_conceded = 1.3
                    form_factor = 1.0
                    our_prob = calculate_over_probability(avg_goals, avg_conceded, line, form_factor)
                    edge = calculate_edge(our_prob, odds)

                    if not is_value_bet(our_prob, edge):
                        continue

                    if today_count + sent >= MAX_SIGNALS_PER_DAY:
                        break

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
                        except Exception as e:
                            print(f"Ошибка отправки {user_id}: {e}")

                    await save_signal(match_name, league, market_name, odds, our_prob, edge)
                    sent += 1
                    print(f"Отправлен сигнал: {match_name} {market_name}")

    print(f"Отправлено сигналов: {sent}")
