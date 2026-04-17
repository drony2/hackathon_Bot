import asyncpg
from datetime import date

class NotificationRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def add(self, user_id: int, subscription_id: int, notify_date: date, notify_type: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO notifications (user_id, subscription_id, notify_date, type, is_sent)
                VALUES ($1, $2, $3, $4, FALSE)
            """, user_id, subscription_id, notify_date, notify_type)