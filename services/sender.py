


from aiogram import Bot
from database import get_active_users, save_signal
from services.odds import get_soccer_odds, parse_matches
from services.model import estimate_totals, check_value
import aiosqlite

DB_PATH = "edge_bot.db"

async def is_signal_sent(match_name: str, market: str) -> bool:
    """Проверяет, отправляли ли уже такой сигнал"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM signals WHERE match_name = ? AND market = ?",
            (match_name, market)
        )
        row = await cursor.fetchone()
        return row is not None

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
            match_name = f"{match['home']} - {match['away']}"

            # Проверяем, не отправляли ли уже
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
                    sent += 1
                except Exception:
                    pass

    print(f"Отправлено сигналов: {sent}")
