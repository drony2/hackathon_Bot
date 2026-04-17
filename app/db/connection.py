import asyncpg
from DB.dbCon import DB_CONFIG

pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)
    print("POOL:", pool)