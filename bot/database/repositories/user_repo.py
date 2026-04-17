import asyncpg

class UserRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def add_user(self, telegram_id: int, username: str, first_name: str) -> int:
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("""
                INSERT INTO users (telegram_id, username, first_name)
                VALUES ($1, $2, $3) 
                ON CONFLICT (telegram_id) DO UPDATE
                SET username = EXCLUDED.username
                RETURNING id
            """, telegram_id, username, first_name)
            
            if user:
                await conn.execute("""
                    INSERT INTO user_settings (user_id, keyboard_version)
                    VALUES ($1, $2) 
                    ON CONFLICT (user_id) DO NOTHING
                """, user["id"], "4.0")
            
            return user["id"] if user else None
    
    async def get_user_id(self, telegram_id: int) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT id FROM users WHERE telegram_id = $1
            """, telegram_id)
    
    async def update_keyboard_version(self, user_id: int, version: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_settings (user_id, keyboard_version, updated_at)
                VALUES ($1, $2, NOW()) 
                ON CONFLICT (user_id) DO UPDATE
                SET keyboard_version = $2, updated_at = NOW()
            """, user_id, version)
    
    async def get_keyboard_version(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT keyboard_version 
                FROM user_settings 
                WHERE user_id = $1
            """, user_id)