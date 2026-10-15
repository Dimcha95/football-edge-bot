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

async def get_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        return (await cursor.fetchone())[0]

async def get_users_for_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id FROM users 
            WHERE is_active = 1 
            AND (is_premium = 1 OR julianday('now') - julianday(joined_at) <= 3)
        """)
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

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
        joined = datetime.fromisoformat(joined_at)
        days_left = 3 - (datetime.utcnow() - joined).days
        return "trial", max(0, days_left)

async def save_signal(match_name, league, market, odds, our_prob, edge):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO signals (match_name, league, market, odds, our_prob, edge, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (match_name, league, market, odds, our_prob, edge, datetime.utcnow().isoformat()))
        await db.commit()

async def is_signal_sent(match_name, market):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM signals WHERE match_name = ? AND market = ?",
            (match_name, market)
        )
        return await cursor.fetchone() is not None

async def get_today_signals_count():
    async with aiosqlite.connect(DB_PATH) as db:
        today = datetime.utcnow().date().isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE date(created_at) = ?",
            (today,)
        )
        return (await cursor.fetchone())[0]

async def update_signal_result(match_name, market, result):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE signals SET result = ? WHERE match_name = ? AND market = ?",
            (result, match_name, market)
        )
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT result FROM signals")
        rows = await cursor.fetchall()
        total = len(rows)
        wins = sum(1 for r in rows if r[0] == "win")
        losses = sum(1 for r in rows if r[0] == "lose")
        pending = sum(1 for r in rows if r[0] == "pending")
        finished = wins + losses
        winrate = (wins / finished * 100) if finished > 0 else 0
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "winrate": winrate
        }
