```python
import aiosqlite
from datetime import datetime, timedelta

DB_PATH = "edge_bot.db"


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

        # Добавляем новые поля, не удаляя старые записи.
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
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                response TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        await db.commit()


async def add_user(user_id: int, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO users
                (user_id, username, joined_at)
            VALUES (?, ?, ?)
            """,
            (user_id, username, datetime.utcnow().isoformat()),
        )
        await db.commit()


async def set_user_active(user_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?",
            (int(active), user_id),
        )
        await db.commit()


async def set_premium(user_id: int, is_premium: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_premium = ? WHERE user_id = ?",
            (int(is_premium), user_id),
        )
        await db.commit()


async def get_active_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE is_active = 1"
        )
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
        if event_id is not None:
            cursor = await db.execute(
                """
                SELECT id FROM signals
                WHERE event_id = ? AND market = ?
                """,
                (str(event_id), market),
            )
            if await cursor.fetchone():
                return False

        await db.execute(
            """
            INSERT INTO signals (
                match_name, league, market, odds, our_prob, edge,
                result, created_at, event_id, fixture_id, commence_time
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                match_name,
                league,
                market,
                odds,
                our_prob,
                edge,
                datetime.utcnow().isoformat(),
                str(event_id) if event_id is not None else None,
                fixture_id,
                commence_time,
            ),
        )
        await db.commit()
        return True


async def is_signal_sent(
    match_name: str,
    market: str,
    event_id: str = None,
) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        if event_id is not None:
            cursor = await db.execute(
                """
                SELECT id FROM signals
                WHERE event_id = ? AND market = ?
                LIMIT 1
                """,
                (str(event_id), market),
            )
        else:
            cursor = await db.execute(
                """
                SELECT id FROM signals
                WHERE match_name = ? AND market = ?
                LIMIT 1
                """,
                (match_name, market),
            )

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
            datetime.utcnow().isoformat(),
            signal_id,
        ))
        await db.commit()


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT
                COUNT(*) AS total,
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
    winrate = wins / settled * 100 if settled else 0

    return {
        "total": total or 0,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "pushes": pushes,
        "winrate": winrate,
    }


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
        cursor = await db.execute(
            "SELECT is_premium, joined_at FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()

    if not row:
        return None, None

    is_premium, joined_at = row

    if is_premium:
        return "premium", None

    if not joined_at:
        return "trial", 3

    joined = datetime.fromisoformat(joined_at)
    days_left = 3 - (datetime.utcnow() - joined).days
    return "trial", max(0, days_left)


async def get_today_signals_count():
    async with aiosqlite.connect(DB_PATH) as db:
        today = datetime.utcnow().date().isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE date(created_at) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_users_ending_trial():
    async with aiosqlite.connect(DB_PATH) as db:
        two_days_ago = (
            datetime.utcnow() - timedelta(days=2)
        ).date().isoformat()

        cursor = await db.execute("""
            SELECT user_id FROM users
            WHERE is_premium = 0
              AND is_active = 1
              AND date(joined_at) = ?
        """, (two_days_ago,))
        return [row[0] for row in await cursor.fetchall()]


async def get_api_cache(cache_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT response, expires_at
            FROM api_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        )
        row = await cursor.fetchone()

    if not row:
        return None

    response, expires_at = row
    if datetime.fromisoformat(expires_at) <= datetime.utcnow():
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM api_cache WHERE cache_key = ?",
                (cache_key,),
            )
            await db.commit()
        return None

    import json
    return json.loads(response)


async def set_api_cache(cache_key: str, response, ttl_seconds: int):
    import json

    expires_at = (
        datetime.utcnow() + timedelta(seconds=ttl_seconds)
    ).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO api_cache (cache_key, response, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                response = excluded.response,
                expires_at = excluded.expires_at
        """, (cache_key, json.dumps(response), expires_at))
        await db.commit()
```
