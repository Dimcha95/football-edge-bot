import aiosqlite
import json
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DB_PATH = "edge_bot.db"

TRIAL_DAYS = 3

MSK = ZoneInfo("Europe/Moscow")


def _utcnow() -> datetime:
    """Naive UTC (как хранится в БД)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _period_start_utc(days):
    """Начало периода по Москве, переведённое в naive UTC (как хранится created_at).

    days=1 -> сегодня с 00:00 МСК, days=7 -> последние 7 календарных дней,
    None -> всё время.
    """
    if days is None:
        return None
    start_msk = (datetime.now(MSK) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start_msk.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_active INTEGER DEFAULT 1,
                is_premium INTEGER DEFAULT 0,
                joined_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_name TEXT,
                league TEXT,
                market TEXT,
                odds REAL,
                our_prob REAL,
                edge REAL,
                result TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)

        cursor = await db.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in await cursor.fetchall()}

        migrations = {
            "event_id": "TEXT",
            "fixture_id": "INTEGER",
            "commence_time": "TEXT",
            "total_goals": "INTEGER",
            "result_updated_at": "TEXT",
        }

        for column, column_type in migrations.items():
            if column not in columns:
                await db.execute(
                    f"ALTER TABLE signals ADD COLUMN {column} {column_type}"
                )

        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_event_market
            ON signals(event_id, market)
            WHERE event_id IS NOT NULL
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_created_at
            ON signals(created_at)
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS match_history (
                fixture_id INTEGER PRIMARY KEY,
                league_id INTEGER NOT NULL,
                home_id INTEGER NOT NULL,
                away_id INTEGER NOT NULL,
                match_date TEXT NOT NULL,
                home_goals INTEGER NOT NULL CHECK(home_goals >= 0),
                away_goals INTEGER NOT NULL CHECK(away_goals >= 0),
                status TEXT NOT NULL DEFAULT 'FT',
                saved_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_history_league_date
            ON match_history(league_id, match_date)
        """)

        # BSD history is independent of legacy API-Football fixture IDs.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bsd_match_history (
                event_id TEXT PRIMARY KEY,
                league_id INTEGER NOT NULL,
                home_id INTEGER NOT NULL,
                away_id INTEGER NOT NULL,
                match_date TEXT NOT NULL,
                home_goals INTEGER NOT NULL CHECK(home_goals >= 0),
                away_goals INTEGER NOT NULL CHECK(away_goals >= 0),
                status TEXT NOT NULL DEFAULT 'finished',
                saved_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_bsd_history_league_date
            ON bsd_match_history(league_id, match_date)
        """)

        await db.commit()


async def add_user(user_id: int, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users
                (user_id, username, joined_at)
            VALUES (?, ?, ?)
        """, (user_id, username, _utcnow().isoformat()))
        await db.commit()


async def set_user_active(user_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?",
            (int(active), user_id),
        )
        await db.commit()


async def set_premium(user_id: int, is_premium: bool) -> bool:
    """Возвращает True, если пользователь найден в базе."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET is_premium = ? WHERE user_id = ?",
            (int(is_premium), user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_active_users():
    """Только те, у кого сигналы включены И есть доступ (premium или живой триал)."""
    trial_cutoff = (_utcnow() - timedelta(days=TRIAL_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id FROM users
            WHERE is_active = 1
              AND (is_premium = 1 OR joined_at IS NULL OR joined_at > ?)
        """, (trial_cutoff,))
        return [row[0] for row in await cursor.fetchall()]


async def save_signal(
    match_name: str,
    league: str,
    market: str,
    odds: float,
    our_prob: float,
    edge: float,
    event_id: str = None,
    fixture_id: int = None,
    commence_time: str = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("""
                INSERT INTO signals (
                    match_name, league, market, odds, our_prob, edge,
                    result, created_at, event_id, fixture_id, commence_time
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """, (
                match_name,
                league,
                market,
                odds,
                our_prob,
                edge,
                _utcnow().isoformat(),
                str(event_id) if event_id is not None else None,
                fixture_id,
                commence_time,
            ))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            # Например, сигнал с таким event_id и market уже существует.
            return False


async def delete_signal(event_id: str, market: str):
    """Удаляет сигнал (например, если он не был доставлен ни одному пользователю)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM signals WHERE event_id = ? AND market = ? AND result = 'pending'",
            (str(event_id), market),
        )
        await db.commit()


async def is_signal_sent(
    match_name: str,
    market: str,
    event_id: str = None,
) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        if event_id is not None:
            cursor = await db.execute("""
                SELECT id FROM signals
                WHERE event_id = ? AND market = ?
                LIMIT 1
            """, (str(event_id), market))
        else:
            cursor = await db.execute("""
                SELECT id FROM signals
                WHERE match_name = ? AND market = ?
                LIMIT 1
            """, (match_name, market))

        return await cursor.fetchone() is not None


async def get_pending_signals_with_time():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT id, match_name, market, created_at,
                   event_id, fixture_id, commence_time
            FROM signals
            WHERE result = 'pending'
            ORDER BY created_at ASC
        """)
        return await cursor.fetchall()


async def update_signal_result(
    signal_id: int,
    result: str,
    total_goals: int = None,
):
    if result not in ("win", "loss", "push"):
        raise ValueError(f"Недопустимый результат: {result}")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE signals
            SET result = ?,
                total_goals = ?,
                result_updated_at = ?
            WHERE id = ? AND result = 'pending'
        """, (
            result,
            total_goals,
            _utcnow().isoformat(),
            signal_id,
        ))
        await db.commit()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT
                COUNT(*),
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END),
                SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END),
                SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END),
                SUM(CASE WHEN result = 'push' THEN 1 ELSE 0 END)
            FROM signals
        """)
        row = await cursor.fetchone()

    total, wins, losses, pending, pushes = row
    wins = wins or 0
    losses = losses or 0
    pending = pending or 0
    pushes = pushes or 0
    settled = wins + losses

    return {
        "total": total or 0,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "pushes": pushes,
        "winrate": wins / settled * 100 if settled else 0,
    }


async def get_stats_for_period(days):
    """Статистика за период (days=None — всё время), ставка 1 ед. на сигнал."""
    start = _period_start_utc(days)

    query = "SELECT result, odds FROM signals"
    params = ()
    if start:
        query += " WHERE created_at >= ?"
        params = (start,)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

    s = {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "pending": 0,
        "profit": 0.0,
        "roi_bets": 0,
    }

    for result, odds in rows:
        s["total"] += 1

        if result == "win":
            s["wins"] += 1
        elif result in ("loss", "lose"):
            s["losses"] += 1
        elif result == "push":
            s["pushes"] += 1
        else:
            s["pending"] += 1
            continue

        if result == "push":
            s["roi_bets"] += 1  # ставка была, прибыль 0
            continue

        try:
            price = float(odds)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 1:
            continue

        s["roi_bets"] += 1
        s["profit"] += (price - 1) if result == "win" else -1

    s["profit"] = round(s["profit"], 2)
    return s


async def get_recent_results(limit: int = 15):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT match_name, market, odds, edge, result
            FROM signals
            WHERE result != 'pending'
            ORDER BY result_updated_at DESC, created_at DESC
            LIMIT ?
        """, (limit,))
        return await cursor.fetchall()


async def get_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_user_status(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT is_premium, joined_at
            FROM users
            WHERE user_id = ?
        """, (user_id,))
        row = await cursor.fetchone()

    if not row:
        return None, None

    is_premium, joined_at = row

    if is_premium:
        return "premium", None

    if not joined_at:
        return "trial", TRIAL_DAYS

    joined = datetime.fromisoformat(joined_at)
    days_used = max(0, (_utcnow() - joined).days)
    days_left = max(0, TRIAL_DAYS - days_used)

    return "trial", days_left


async def get_today_signals_count():
    """Количество сигналов за сегодняшний день по Москве."""
    start = _period_start_utc(1)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE created_at >= ?",
            (start,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_users_ending_trial():
    """Пользователи, у которых триал закончится завтра (осталось 1 день).

    Это те, кто зарегистрировался 2–3 суток назад. При запуске раз в сутки
    каждый пользователь попадает в выборку ровно один раз.
    """
    now = _utcnow()
    older_bound = (now - timedelta(days=TRIAL_DAYS)).isoformat()
    newer_bound = (now - timedelta(days=TRIAL_DAYS - 1)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id
            FROM users
            WHERE is_premium = 0
              AND is_active = 1
              AND joined_at > ?
              AND joined_at <= ?
        """, (older_bound, newer_bound))
        return [row[0] for row in await cursor.fetchall()]


async def get_api_cache(cache_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT response, expires_at
            FROM api_cache
            WHERE cache_key = ?
        """, (cache_key,))
        row = await cursor.fetchone()

    if not row:
        return None

    response, expires_at = row

    if datetime.fromisoformat(expires_at) <= _utcnow():
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM api_cache WHERE cache_key = ?",
                (cache_key,),
            )
            await db.commit()
        return None

    return json.loads(response)


async def set_api_cache(
    cache_key: str,
    response,
    ttl_seconds: int,
):
    expires_at = (
        _utcnow() + timedelta(seconds=ttl_seconds)
    ).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO api_cache (cache_key, response, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                response = excluded.response,
                expires_at = excluded.expires_at
        """, (
            cache_key,
            json.dumps(response),
            expires_at,
        ))
        await db.commit()


async def save_finished_fixture(item):
    """Сохраняет один завершённый матч API-Football. Возвращает True при успехе."""
    if not isinstance(item, dict):
        return False

    fixture = item.get("fixture") or {}
    teams = item.get("teams") or {}
    league = item.get("league") or {}
    goals = item.get("goals") or {}

    if (fixture.get("status") or {}).get("short") != "FT":
        return False

    try:
        fixture_id = int(fixture["id"])
        league_id = int(league["id"])
        home_id = int(teams["home"]["id"])
        away_id = int(teams["away"]["id"])
        home_goals = int(goals["home"])
        away_goals = int(goals["away"])
        match_date = str(fixture["date"])
        datetime.fromisoformat(match_date.replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return False

    if (fixture_id <= 0 or league_id <= 0 or home_id <= 0
            or away_id <= 0 or home_id == away_id
            or home_goals < 0 or away_goals < 0):
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO match_history (
                fixture_id, league_id, home_id, away_id,
                match_date, home_goals, away_goals, status, saved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'FT', ?)
            ON CONFLICT(fixture_id) DO UPDATE SET
                league_id = excluded.league_id,
                home_id = excluded.home_id,
                away_id = excluded.away_id,
                match_date = excluded.match_date,
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                status = excluded.status
        """, (
            fixture_id, league_id, home_id, away_id, match_date,
            home_goals, away_goals, _utcnow().isoformat(),
        ))
        await db.commit()
    return True


async def get_saved_fixture_history():
    """Возвращает сохранённые матчи в формате API-Football для team_stats.py."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT fixture_id, league_id, home_id, away_id,
                   match_date, home_goals, away_goals, status
            FROM match_history
            ORDER BY match_date DESC
        """)
        rows = await cursor.fetchall()

    return [
        {
            "fixture": {"id": fixture_id, "date": match_date,
                        "status": {"short": status}},
            "league": {"id": league_id},
            "teams": {"home": {"id": home_id}, "away": {"id": away_id}},
            "goals": {"home": home_goals, "away": away_goals},
        }
        for (fixture_id, league_id, home_id, away_id,
             match_date, home_goals, away_goals, status) in rows
    ]


async def get_over_stats():
    """Статистика только ТБ 1.5, 2.5, 3.5, ставка 1 ед. на сигнал.

    ROI вычисляется по завершённым ставкам с корректным коэффициентом.
    Статусы: win, loss, push, pending.
    """
    lines = ("1.5", "2.5", "3.5")
    stats = {
        line: {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "pending": 0,
            "pushes": 0,
            "profit": 0.0,
            "roi": 0.0,
            "winrate": 0.0,
            "roi_bets": 0,
        }
        for line in lines
    }

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT market, result, odds FROM signals"
        )
        rows = await cursor.fetchall()

    for market, result, odds in rows:
        normalized = re.sub(r"\s+", "", str(market or "")).upper()
        found = re.fullmatch(r"ТБ(1\.5|2\.5|3\.5)", normalized)
        if found is None:
            continue

        item = stats[found.group(1)]
        item["total"] += 1

        if result == "win":
            item["wins"] += 1
        elif result in ("loss", "lose"):
            item["losses"] += 1
        elif result == "push":
            item["pushes"] += 1
        else:
            item["pending"] += 1
            continue

        # Некорректные коэффициенты не включаем в ROI.
        try:
            price = float(odds)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 1:
            continue

        item["roi_bets"] += 1
        if result == "win":
            item["profit"] += price - 1
        elif result == "loss":
            item["profit"] -= 1
        # push: возврат ставки, прибыль 0

    for item in stats.values():
        decided = item["wins"] + item["losses"]
        item["winrate"] = round(100 * item["wins"] / decided, 2) if decided else 0.0
        item["profit"] = round(item["profit"], 2)
        item["roi"] = (
            round(100 * item["profit"] / item["roi_bets"], 2)
            if item["roi_bets"] else 0.0
        )

    return stats


async def save_bsd_finished_event(event):
    """Store a finished BSD event. Never mix BSD IDs with API-Football IDs.

    Returns True for a valid upsert, False for invalid/incomplete data.
    This expects the event dictionary from BSD /api/v2/events/.
    """
    if not isinstance(event, dict):
        return False
    if isinstance(event.get("event"), dict):
        event = event["event"]

    status = str(event.get("status") or "").strip().lower()
    period = str(event.get("period") or "").strip().upper()
    if status != "finished" or period != "FT":
        return False
    if event.get("cancelled") or event.get("postponed"):
        return False

    event_id = event.get("id")
    if event_id is None or isinstance(event_id, bool) or not str(event_id).strip():
        return False

    try:
        league_id = int(event["league_id"])
        home_id = int(event["home_team_id"])
        away_id = int(event["away_team_id"])
        home_goals = int(event["home_score"])
        away_goals = int(event["away_score"])
        if any(isinstance(event[key], bool) for key in (
            "league_id", "home_team_id", "away_team_id", "home_score", "away_score"
        )):
            return False
        if str(event["home_score"]).strip() != str(home_goals):
            return False
        if str(event["away_score"]).strip() != str(away_goals):
            return False
        date_raw = str(event["event_date"])
        date_obj = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError, OverflowError):
        return False

    if (min(league_id, home_id, away_id) <= 0 or home_id == away_id
            or min(home_goals, away_goals) < 0):
        return False

    # Guard against malformed or ambiguous scores for extra-time matches.
    if event.get("extra_time_score") not in (None, ""):
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO bsd_match_history (
                event_id, league_id, home_id, away_id, match_date,
                home_goals, away_goals, status, saved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'finished', ?)
            ON CONFLICT(event_id) DO UPDATE SET
                league_id = excluded.league_id,
                home_id = excluded.home_id,
                away_id = excluded.away_id,
                match_date = excluded.match_date,
                home_goals = excluded.home_goals,
                away_goals = excluded.away_goals,
                status = excluded.status,
                saved_at = excluded.saved_at
        """, (
            str(event_id), league_id, home_id, away_id,
            date_obj.isoformat(), home_goals, away_goals,
            _utcnow().isoformat(),
        ))
        await db.commit()
    return True


async def get_bsd_match_history():
    """Return normalized BSD history for the future team_stats adapter."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT event_id, league_id, home_id, away_id, match_date,
                   home_goals, away_goals, status
            FROM bsd_match_history
            ORDER BY match_date DESC
        """)
        rows = await cursor.fetchall()
    return [
        {
            "event_id": event_id,
            "league_id": league_id,
            "home_id": home_id,
            "away_id": away_id,
            "match_date": match_date,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "status": status,
        }
        for (event_id, league_id, home_id, away_id, match_date,
             home_goals, away_goals, status) in rows
    ]
