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
                    
                    if odds < 1.60 or odds > 2.50:
                        continue
                    
                    our_prob = calculate_over_probability(
                        home_stats["avg_goals"], away_stats["avg_goals"],
                        home_stats["avg_conceded"], away_stats["avg_conceded"],
                        home_stats["form"], away_stats["form"],
                        line
                    )
                    edge = calculate_edge(our_prob, odds)
                    
                    if not is_value_bet(our_prob, odds, min_edge=0.08):
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
                        f"📝 Форма: {home_stats['form']*100:.0f}% / {away_stats['form']*100:.0f}%"
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
