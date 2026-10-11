import aiosqlite
from datetime import datetime

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

async def get_active_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE is_active = 1")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def save_signal(match_name, league, market, odds, our_prob, edge):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO signals 
               (match_name, league, market, odds, our_prob, edge, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (match_name, league, market, odds, our_prob, edge, datetime.utcnow().isoformat())
        )
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM signals")).fetchone())[0]
        wins = (await (await db.execute("SELECT COUNT(*) FROM signals WHERE result = 'win'")).fetchone())[0]
        loses = (await (await db.execute("SELECT COUNT(*) FROM signals WHERE result = 'lose'")).fetchone())[0]
        pending = total - wins - loses
        return total, wins, loses, pending

async def get_users_count():
    async with aiosqlite.connect(DB_PATH) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")).fetchone())[0]
        premium = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1")).fetchone())[0]
        return total, active, premium

async def update_signal_result(signal_id: int, result: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE signals SET result = ? WHERE id = ?",
            (result, signal_id)
        )
        await db.commit()

async def get_pending_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, match_name, market, created_at FROM signals WHERE result = 'pending' ORDER BY id DESC LIMIT 20"
        )
        return await cursor.fetchall()
