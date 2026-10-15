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

async def set_premium(user_id: int, is_premium: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_premium = ? WHERE user_id = ?",
            (1 if is_premium else 0, user_id)
        )
        await db.commit()

async def get_active_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE is_active = 1"
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def save_signal(match_name: str, league: str, market: str, odds: float, our_prob: float, edge: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO signals (match_name, league, market, odds, our_prob, edge, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (match_name, league, market, odds, our_prob, edge, datetime.utcnow().isoformat())
        )
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
                COUNT(*) as total,
                SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END) as pending
            FROM signals
        """)
        row = await cursor.fetchone()
        total, wins, losses, pending = row if row else (0, 0, 0, 0)
        wins = wins or 0
        losses = losses or 0
        pending = pending or 0
        finished = wins + losses
        winrate = (wins / finished * 100) if finished > 0 else 0
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "winrate": winrate
        }

async def get_recent_results(limit: int = 15):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT match_name, market, odds, edge, result 
               FROM signals 
               WHERE result != 'pending'
               ORDER BY created_at DESC 
               LIMIT ?""",
            (limit,)
        )
        rows = await cursor.fetchall()
        return rows

async def get_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        return (await cursor.fetchone())[0]

async def get_user_status(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT is_premium, joined_at FROM users WHERE user_id = ?",
            (user_id,)
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
            (today,)
        )
        return (await cursor.fetchone())[0]

async def get_users_ending_trial():
    async with aiosqlite.connect(DB_PATH) as db:
        two_days_ago = (datetime.utcnow() - timedelta(days=2)).date().isoformat()
        cursor = await db.execute("""
            SELECT user_id FROM users 
            WHERE is_premium = 0 
            AND is_active = 1
            AND date(joined_at) = ?
        """, (two_days_ago,))
        rows = await cursor.fetchall()
        return [r[0] for r in rows]
