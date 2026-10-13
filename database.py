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
        await db.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (1 if active else 0, user_id))
        await db.commit()

async def set_premium(user_id: int, premium: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (1 if premium else 0, user_id))
        await db.commit()

async def get_users_for_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        three_days_ago = (datetime.utcnow() - timedelta(days=3)).isoformat()
        cursor = await db.execute("""
            SELECT user_id FROM users 
            WHERE is_active = 1 AND (is_premium = 1 OR joined_at >= ?)
        """, (three_days_ago,))
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def save_signal(match_name, league, market, odds, our_prob, edge):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO signals (match_name, league, market, odds, our_prob, edge, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (match_name, league, market, odds, our_prob, edge, datetime.utcnow().isoformat()))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM signals")).fetchone())[0]
        wins = (await (await db.execute("SELECT COUNT(*) FROM signals WHERE result = 'win'")).fetchone())[0]
        loses = (await (await db.execute("SELECT COUNT(*) FROM signals WHERE result = 'lose'")).fetchone())[0]
        return total, wins, loses, total - wins - loses

async def get_stats_period(days: int):
    async with aiosqlite.connect(DB_PATH) as db:
        from_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor = await db.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) as loses
            FROM signals WHERE created_at >= ?
        """, (from_date,))
        row = await cursor.fetchone()
        return row[0] or 0, row[1] or 0, row[2] or 0

async def get_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")).fetchone())[0]
        premium = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")).fetchone())[0]
        return total, active, premium

async def update_signal_result(signal_id: int, result: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE signals SET result = ? WHERE id = ?", (result, signal_id))
        await db.commit()

async def get_pending_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, match_name, market, created_at FROM signals WHERE result = 'pending' ORDER BY id DESC LIMIT 20")
        return await cursor.fetchall()

async def get_pending_signals_with_time():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, match_name, market, created_at FROM signals WHERE result = 'pending' ORDER BY id DESC LIMIT 30")
        return await cursor.fetchall()

async def is_signal_sent(match_name: str, market: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id FROM signals WHERE match_name = ? AND market = ?", (match_name, market))
        return await cursor.fetchone() is not None

async def get_users_ending_trial():
    async with aiosqlite.connect(DB_PATH) as db:
        two_days_ago = (datetime.utcnow() - timedelta(days=2)).date().isoformat()
        cursor = await db.execute("""
            SELECT user_id FROM users 
            WHERE is_premium = 0 AND is_active = 1 AND date(joined_at) = ?
        """, (two_days_ago,))
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def get_user_status(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT is_premium, joined_at FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return None, None
        is_premium, joined_at = row
        if is_premium:
            return "premium", None
        joined = datetime.fromisoformat(joined_at)
        days_left = 3 - (datetime.utcnow() - joined).days
        return "trial", max(0, days_left)

async def get_today_signals_count():
    async with aiosqlite.connect(DB_PATH) as db:
        today = datetime.utcnow().date().isoformat()
        cursor = await db.execute("SELECT COUNT(*) FROM signals WHERE date(created_at) = ?", (today,))
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
        
