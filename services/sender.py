```python
import asyncio
from datetime import datetime, timezone

from config import MIN_EDGE, MIN_PROBABILITY, MAX_SIGNALS_PER_DAY
from database import (
    get_active_users,
    save_signal,
    is_signal_sent,
    get_today_signals_count,
)
from services.odds import get_odds
from services.model import calculate_over_probability, calculate_edge


def estimate_probability(line: float, home_strength: float = 1.5,
                         away_strength: float = 1.4) -> float:
    """
    Начальная эвристическая оценка.
    Это не статистически обученная модель и не гарантия результата.
    """
    expected_total = (home_strength + away_strength) / 2

    base = {
        1.5: 0.68,
        2.5: 0.52,
        3.5: 0.38,
    }.get(float(line), 0.40)

    if expected_total > line + 0.4:
        base += 0.08
    elif expected_total < line - 0.4:
        base -= 0.08

    return max(0.15, min(0.85, base))


async def check_and_send(bot):
    print("=== check_and_send вызван ===")

    matches = await get_odds()
    print(f"Вариантов коэффициентов: {len(matches)}")

    today_count = await get_today_signals_count()
    if today_count >= MAX_SIGNALS_PER_DAY:
        print(f"Достигнут дневной лимит: {MAX_SIGNALS_PER_DAY}")
        return

    users = await get_active_users()
    if not users:
        print("Нет активных пользователей")
        return

    sent = 0

    for match in matches:
        fixture_id = match.get("fixture_id")
        home = match.get("home", "")
        away = match.get("away", "")
        league = match.get("league", "Футбол")
        commence_time = match.get("commence_time")
        line = float(match["line"])
        odds = float(match["odds"])

        if not fixture_id or not home or not away or odds <= 1:
            continue

        match_name = f"{home} — {away}"
        market_name = f"ТБ {line:g}"

        # Сначала защищаемся от повторного сигнала по ID матча и рынку.
        if await is_signal_sent(
            match_name,
            market_name,
            event_id=str(fixture_id),
        ):
            continue

        probability = estimate_probability(
            line,
            match.get("home_strength", 1.5),
            match.get("away_strength", 1.4),
        )
        edge = calculate_edge(probability, odds)

        if probability < MIN_PROBABILITY or edge < MIN_EDGE:
            continue

        if today_count + sent >= MAX_SIGNALS_PER_DAY:
            print("Достигнут дневной лимит сигналов")
            break

        text = (
            f"⚽️ <b>{match_name}</b>\n"
            f"🏆 {league}\n"
            f"🕒 {commence_time or 'Время неизвестно'}\n\n"
            f"🎯 <b>{market_name}</b> @ {odds:.2f}\n"
            f"📊 Оценка вероятности: <b>{probability * 100:.1f}%</b>\n"
            f"📈 Расчётный edge: <b>{edge * 100:+.1f}%</b>\n\n"
            f"ℹ️ Вероятность — предварительная модельная оценка, "
            f"не гарантия результата."
        )

        # Сохраняем до рассылки, чтобы повторный запуск не создал дубль.
        saved = await save_signal(
            match_name=match_name,
            league=league,
            market=market_name,
            odds=odds,
            our_prob=probability,
            edge=edge,
            event_id=str(fixture_id),
            fixture_id=int(fixture_id),
            commence_time=commence_time,
        )

        if not saved:
            continue

        delivered = 0

        for user_id in users:
            try:
                await bot.send_message(
                    user_id,
                    text,
                    parse_mode="HTML",
                )
                delivered += 1
            except Exception as exc:
                print(f"Не удалось отправить сообщение {user_id}: {exc}")

        sent += 1
        print(
            f"Сигнал сохранён: {match_name}, {market_name}; "
            f"доставлено пользователям: {delivered}"
        )

    print(f"Новых сигналов: {sent}")
```
