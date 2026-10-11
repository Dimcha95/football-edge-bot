from aiogram import Bot
from database import get_active_users, save_signal
from services.odds import get_soccer_odds, parse_matches
from services.model import estimate_probabilities, check_value

async def check_and_send(bot: Bot):
    print("Проверяю матчи...")
    raw = await get_soccer_odds()
    if not raw:
        print("Нет данных от API")
        return

    matches = parse_matches(raw)
    print(f"Найдено матчей: {len(matches)}")

    users = await get_active_users()
    if not users:
        print("Нет активных пользователей")
        return

    sent = 0
    for match in matches:
        probs = estimate_probabilities(match["home_strength"], match["away_strength"])
        odds = match["odds"]

        candidates = []

        if "home" in odds:
            ok, edge = check_value(odds["home"], probs["home"])
            if ok:
                candidates.append(("П1", odds["home"], probs["home"], edge))

        if "draw" in odds:
            ok, edge = check_value(odds["draw"], probs["draw"])
            if ok:
                candidates.append(("Х", odds["draw"], probs["draw"], edge))

        if "away" in odds:
            ok, edge = check_value(odds["away"], probs["away"])
            if ok:
                candidates.append(("П2", odds["away"], probs["away"], edge))

        if "over_25" in odds:
            ok, edge = check_value(odds["over_25"], 0.52)
            if ok:
                candidates.append(("ТБ 2.5", odds["over_25"], 0.52, edge))

        if "under_25" in odds:
            ok, edge = check_value(odds["under_25"], 0.48)
            if ok:
                candidates.append(("ТМ 2.5", odds["under_25"], 0.48, edge))

        for market, price, prob, edge in candidates:
            text = (
                f"⚽️ <b>{match['home']} — {match['away']}</b>\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']} UTC\n\n"
                f"🎯 <b>{market}</b> @ <b>{price}</b>\n"
                f"📊 Вероятность: <b>{prob*100:.1f}%</b>\n"
                f"📈 Edge: <b>+{edge*100:.1f}%</b>\n\n"
                f"#value #edge"
            )

            await save_signal(
                f"{match['home']} - {match['away']}",
                match["league"],
                market,
                price,
                prob,
                edge
            )

            for uid in users:
                try:
                    await bot.send_message(uid, text, parse_mode="HTML")
                    sent += 1
                except Exception:
                    pass

    print(f"Отправлено сигналов: {sent}")