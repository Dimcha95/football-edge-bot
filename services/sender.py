
 from aiogram import Bot
from database import get_users_for_signals, save_signal, is_signal_sent
from services.odds import get_soccer_odds, parse_matches
from services.model import estimate_totals, check_value
from datetime import datetime
import aiosqlite

async def check_and_send(bot: Bot):
    print("Проверяю матчи...")
    raw = await get_soccer_odds()
    if not raw:
        print("Нет данных от API")
        return

    matches = parse_matches(raw)
    print(f"Найдено матчей: {len(matches)}")

    users = await get_users_for_signals()
    if not users:
        print("Нет пользователей для рассылки")
        return

    # Лимит 15 сигналов в день
    today = datetime.utcnow().date().isoformat()
    async with aiosqlite.connect("edge_bot.db") as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE date(created_at) = ?",
            (today,)
        )
        sent_today = (await cursor.fetchone())[0]

    if sent_today >= 15:
        print(f"Лимит 15 сигналов в день уже достигнут ({sent_today})")
        return

    sent = 0
    for match in matches:
        if sent_today + sent >= 15:
            print("Достигнут дневной лимит 15 сигналов")
            break

        totals = estimate_totals(
            match["home_strength"],
            match["away_strength"],
            home_form=1.0,
            away_form=1.0,
            home_avg_goals=1.5,
            away_avg_goals=1.2,
            home_avg_conceded=1.0,
            away_avg_conceded=1.3
        )

        odds = match["odds"]
        candidates = []

        if "over_15" in odds:
            ok, edge = check_value(odds["over_15"], totals["over_15"])
            if ok:
                candidates.append(("ТБ 1.5", odds["over_15"], totals["over_15"], edge))

        if "over_25" in odds:
            ok, edge = check_value(odds["over_25"], totals["over_25"])
            if ok:
                candidates.append(("ТБ 2.5", odds["over_25"], totals["over_25"], edge))

        if "over_35" in odds:
            ok, edge = check_value(odds["over_35"], totals["over_35"])
            if ok:
                candidates.append(("ТБ 3.5", odds["over_35"], totals["over_35"], edge))

        for market, price, prob, edge in candidates:
            if sent_today + sent >= 15:
                break

            match_name = f"{match['home']} - {match['away']}"

            if await is_signal_sent(match_name, market):
                continue

            text = (
                f"⚽️ <b>{match['home']} — {match['away']}</b>\n"
                f"🏆 {match['league']}\n"
                f"🕐 {match['time']} UTC\n\n"
                f"🎯 <b>{market}</b> @ <b>{price}</b>\n"
                f"📊 Вероятность: <b>{prob*100:.1f}%</b>\n"
                f"📈 Edge: <b>+{edge*100:.1f}%</b>\n\n"
                f"#value #over"
            )

            await save_signal(match_name, match["league"], market, price, prob, edge)

            for uid in users:
                try:
                    await bot.send_message(uid, text, parse_mode="HTML")
                except Exception:
                    pass

            sent += 1

    print(f"Отправлено сигналов: {sent} (всего сегодня: {sent_today + sent})")

