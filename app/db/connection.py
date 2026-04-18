import asyncpg
from DB.dbCon import DB_CONFIG

pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)
    print(f"✅ Pool создан: {pool}")
    return pool

def get_pool():
    print(pool)
    return pool