import asyncpg
from datetime import date, datetime

class PaymentRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def add(self, subscription_id: int, amount: float, payment_date: date, status: str = "paid"):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                VALUES ($1, $2, $3, $4, NOW())
            """, subscription_id, amount, payment_date, status)
    
    async def get_history(self, telegram_id: int, limit: int = 20):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT p.id, p.subscription_id, p.amount, p.payment_date, p.status,
                       s.name as subscription_name, s.currency
                FROM payments p
                JOIN subscriptions s ON s.id = p.subscription_id
                JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = $1
                ORDER BY p.payment_date DESC, p.id DESC
                LIMIT $2
            """, telegram_id, limit)
    
    async def get_monthly_spending(self, telegram_id: int, currency: str = None, 
                                   year: int = None, month: int = None):
        if year is None or month is None:
            today = datetime.now()
            year = today.year
            month = today.month
        
        async with self.pool.acquire() as conn:
            query = """
                SELECT s.currency, SUM(p.amount) as total
                FROM payments p
                JOIN subscriptions s ON s.id = p.subscription_id
                JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = $1
                AND EXTRACT(YEAR FROM p.payment_date) = $2
                AND EXTRACT(MONTH FROM p.payment_date) = $3
                AND p.status = 'paid'
            """
            params = [telegram_id, year, month]
            
            if currency:
                query += " AND s.currency = $4"
                params.append(currency)
            
            query += " GROUP BY s.currency"
            
            return await conn.fetch(query, *params)