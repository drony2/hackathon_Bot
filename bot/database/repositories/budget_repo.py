import asyncpg

class BudgetRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def set_budget(self, telegram_id: int, currency: str, monthly_limit: float):
        async with self.pool.acquire() as conn:
            user_id = await conn.fetchval("SELECT id FROM users WHERE telegram_id = $1", telegram_id)
            if user_id:
                await conn.execute("""
                    INSERT INTO budgets (user_id, currency, monthly_limit, updated_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (user_id, currency) DO UPDATE
                    SET monthly_limit = $3, updated_at = NOW()
                """, user_id, currency, monthly_limit)
    
    async def get_budget(self, telegram_id: int, currency: str = None):
        async with self.pool.acquire() as conn:
            if currency:
                return await conn.fetchval("""
                    SELECT b.monthly_limit
                    FROM budgets b
                    JOIN users u ON u.id = b.user_id
                    WHERE u.telegram_id = $1 AND b.currency = $2
                """, telegram_id, currency)
            else:
                rows = await conn.fetch("""
                    SELECT b.currency, b.monthly_limit
                    FROM budgets b
                    JOIN users u ON u.id = b.user_id
                    WHERE u.telegram_id = $1
                """, telegram_id)
                return {row["currency"]: float(row["monthly_limit"]) for row in rows}