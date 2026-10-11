   import os
import asyncpg
from datetime import datetime, timedelta

DATABASE_URL = os.getenv("DATABASE_URL")

async def get_connection():
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    conn = await get_connection()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                is_active INTEGER DEFAULT 1,
                is_premium INTEGER DEFAULT 0,
                joined_at TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id SERIAL PRIMARY KEY,
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
    finally:
        await conn.close()

async def add_user(user_id: int, username: str = None):
    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT INTO users (user_id, username, joined_at) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
            user_id, username, datetime.utcnow().isoformat()
        )
    finally:
        await conn.close()

async def set_user_active(user_id: int, active: bool):
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE users SET is_active = $1 WHERE user_id = $2",
            1 if active else 0, user_id
        )
    finally:
        await conn.close()

async def set_premium(user_id: int, premium: bool):
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE users SET is_premium = $1 WHERE user_id = $2",
            1 if premium else 0, user_id
        )
    finally:
        await conn.close()

async def get_users_for_signals():
    conn = await get_connection()
    try:
        three_days_ago = (datetime.utcnow() - timedelta(days=3)).isoformat()
        rows = await conn.fetch("""
            SELECT user_id FROM users 
            WHERE is_active = 1 
            AND (is_premium = 1 OR joined_at >= $1)
        """, three_days_ago)
        return [r["user_id"] for r in rows]
    finally:
        await conn.close()

async def save_signal(match_name, league, market, odds, our_prob, edge):
    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO signals 
               (match_name, league, market, odds, our_prob, edge, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            match_name, league, market, odds, our_prob, edge, datetime.utcnow().isoformat()
        )
    finally:
        await conn.close()

async def get_stats():
    conn = await get_connection()
    try:
        total = await conn.fetchval("SELECT COUNT(*) FROM signals")
        wins = await conn.fetchval("SELECT COUNT(*) FROM signals WHERE result = 'win'")
        loses = await conn.fetchval("SELECT COUNT(*) FROM signals WHERE result = 'lose'")
        pending = total - wins - loses
        return total, wins, loses, pending
    finally:
        await conn.close()

async def get_users_count():
    conn = await get_connection()
    try:
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        active = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_active = 1")
        premium = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_premium = 1")
        return total, active, premium
    finally:
        await conn.close()

async def update_signal_result(signal_id: int, result: str):
    conn = await get_connection()
    try:
        await conn.execute(
            "UPDATE signals SET result = $1 WHERE id = $2",
            result, signal_id
        )
    finally:
        await conn.close()

async def get_pending_signals():
    conn = await get_connection()
    try:
        rows = await conn.fetch(
            "SELECT id, match_name, market, created_at FROM signals WHERE result = 'pending' ORDER BY id DESC LIMIT 20"
        )
        return [(r["id"], r["match_name"], r["market"], r["created_at"]) for r in rows]
    finally:
        await conn.close()

async def get_pending_signals_with_time():
    conn = await get_connection()
    try:
        rows = await conn.fetch("""
            SELECT id, match_name, market, created_at 
            FROM signals 
            WHERE result = 'pending'
            ORDER BY id DESC
            LIMIT 30
        """)
        return [(r["id"], r["match_name"], r["market"], r["created_at"]) for r in rows]
    finally:
        await conn.close()

async def is_signal_sent(match_name: str, market: str) -> bool:
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            "SELECT id FROM signals WHERE match_name = $1 AND market = $2",
            match_name, market
        )
        return row is not None
    finally:
        await conn.close()

async def get_users_ending_trial():
    conn = await get_connection()
    try:
        two_days_ago = (datetime.utcnow() - timedelta(days=2)).date().isoformat()
        rows = await conn.fetch("""
            SELECT user_id FROM users 
            WHERE is_premium = 0 
            AND is_active = 1
            AND LEFT(joined_at, 10) = $1
        """, two_days_ago)
        return [r["user_id"] for r in rows]
    finally:
        await conn.close()

async def get_user_status(user_id: int):
    conn = await get_connection()
    try:
        row = await conn.fetchrow(
            "SELECT is_premium, joined_at FROM users WHERE user_id = $1",
            user_id
        )
        if not row:
            return None, None
        is_premium, joined_at = row["is_premium"], row["joined_at"]
        if is_premium:
            return "premium", None
        joined = datetime.fromisoformat(joined_at)
        days_left = 3 - (datetime.utcnow() - joined).days
        return "trial", max(0, days_left)
    finally:
        await conn.close()

async def get_today_signals_count():
    conn = await get_connection()
    try:
        today = datetime.utcnow().date().isoformat()
        return await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE LEFT(created_at, 10) = $1",
            today
        )
    finally:
        await conn.close()
