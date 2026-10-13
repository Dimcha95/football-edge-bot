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
        await db.commit()

async def add_user(user_id: int, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, joined_at) VALUES (?, ?, ?)",
            (user_id, username, datetime.utcnow().isoformat())
        )
        await db.commit()

async def set_user_active(user_id: int, active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_active = ? WHERE user_id = ?",
            (1 if active else 0, user_id)
        )
        await db.commit()

async def set_premium(user_id: int, premium: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_premium = ? WHERE user_id = ?",
            (1 if premium else 0, user_id)
        )
        await db.commit()

async def get_users_for_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id FROM users 
            WHERE is_active = 1 
            AND (is_premium = 1 OR datetime(joined_at) > datetime('now', '-3 days'))
        """)
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def get_active_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE is_active = 1")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def save_signal(match_name, league, market, odds, our_prob, edge):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO signals (match_name, league, market, odds, our_prob, edge, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (match_name, league, market, odds, our_prob, edge, datetime.utcnow().isoformat()))
        await db.commit()

async def is_signal_sent(match_name: str, market: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM signals WHERE match_name = ? AND market = ?",
            (match_name, market)
        )
        row = await cursor.fetchone()
        return row is not None

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT 
                COUNT(*),
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END),
                SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END),
                SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END)
            FROM signals
        """)
        row = await cursor.fetchone()
        return row if row else (0, 0, 0, 0)

async def get_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")).fetchone())[0]
        premium = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")).fetchone())[0]
        return total, active, premium

async def get_stats_period(days: int):
    async with aiosqlite.connect(DB_PATH) as db:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor = await db.execute("""
            SELECT 
                COUNT(*),
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END),
                SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END)
            FROM signals WHERE created_at >= ?
        """, (since,))
        row = await cursor.fetchone()
        return row if row else (0, 0, 0)

async def get_today_signals_count():
    async with aiosqlite.connect(DB_PATH) as db:
        today = datetime.utcnow().date().isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE date(created_at) = ?",
            (today,)
        )
        return (await cursor.fetchone())[0]

async def update_signal_result(match_name: str, market: str, result: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE signals SET result = ? WHERE match_name = ? AND market = ? AND result = 'pending'",
            (result, match_name, market)
        )
        await db.commit()

async def get_today_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        today = datetime.utcnow().date().isoformat()
        cursor = await db.execute("""
            SELECT match_name, market, odds, result 
            FROM signals 
            WHERE date(created_at) = ?
            ORDER BY created_at
        """, (today,))
        return await cursor.fetchall()
