import asyncpg
from datetime import date

class SubscriptionRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def add(self, user_id: int, data: dict):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO subscriptions
                (user_id, name, amount, currency, next_payment_date, period_days, status)
                VALUES ($1, $2, $3, $4, $5, $6, 'active')
            """, user_id, data["name"], data["amount"], 
               data["currency"], data["date"], data["period"])
    
    async def check_exists(self, telegram_id: int, name: str, exclude_id: int = None) -> bool:
        async with self.pool.acquire() as conn:
            query = """
                SELECT EXISTS(SELECT 1
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = $1 AND LOWER(s.name) = LOWER($2)
            """
            params = [telegram_id, name]
            
            if exclude_id:
                query += " AND s.id != $3"
                params.append(exclude_id)
            
            query += ")"
            
            return await conn.fetchval(query, *params)
    
    async def get_count(self, telegram_id: int) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT COUNT(*)
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = $1
            """, telegram_id)
    
    async def get_all(self, telegram_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT s.id, s.name, s.amount, s.currency, 
                       s.next_payment_date, s.period_days, s.status
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = $1
                ORDER BY CASE WHEN s.status = 'active' THEN 0 ELSE 1 END,
                         s.next_payment_date
            """, telegram_id)
    
    async def get_by_id(self, subscription_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM subscriptions WHERE id = $1
            """, subscription_id)
    
    async def update_field(self, subscription_id: int, field: str, value):
        async with self.pool.acquire() as conn:
            await conn.execute(f"""
                UPDATE subscriptions SET {field} = $1, updated_at = NOW()
                WHERE id = $2
            """, value, subscription_id)
    
    async def update_reminders(self, subscription_id: int, **kwargs):
        async with self.pool.acquire() as conn:
            updates = []
            params = []
            for key, value in kwargs.items():
                updates.append(f"{key} = ${len(params)+1}")
                params.append(value)
            
            if updates:
                params.append(subscription_id)
                await conn.execute(f"""
                    UPDATE subscriptions 
                    SET {', '.join(updates)}
                    WHERE id = ${len(params)}
                """, *params)
    
    async def delete(self, subscription_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM subscriptions WHERE id = $1", subscription_id)
    
    async def get_active_with_notifications(self):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT s.*, u.telegram_id
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE s.status = 'active'
            """)