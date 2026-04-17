import asyncpg
import logging
from bot.database.db_config import DB_CONFIG

pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)
    
    async with pool.acquire() as conn:
        # Users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(255),
                first_name VARCHAR(255),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        
        # Subscriptions table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                currency VARCHAR(10) DEFAULT 'RUB',
                next_payment_date DATE NOT NULL,
                period_days INTEGER NOT NULL,
                reminded_3d BOOLEAN DEFAULT FALSE,
                reminded_1d BOOLEAN DEFAULT FALSE,
                reminded_today BOOLEAN DEFAULT FALSE,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        
        # Payments table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE CASCADE,
                amount DECIMAL(10,2) NOT NULL,
                payment_date DATE NOT NULL,
                status VARCHAR(20) DEFAULT 'paid',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        
        # Budgets table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                currency VARCHAR(10) NOT NULL,
                monthly_limit DECIMAL(10,2) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE(user_id, currency)
            )
        """)
        
        # Notifications table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE CASCADE,
                notify_date DATE NOT NULL,
                type VARCHAR(50) NOT NULL,
                is_sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        
        # User settings table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                keyboard_version VARCHAR(10),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        
        # Create indexes
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_user_name 
            ON subscriptions (user_id, LOWER(name))
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_subscription_id 
            ON payments(subscription_id)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_payment_date 
            ON payments(payment_date)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_subscriptions_next_payment 
            ON subscriptions(next_payment_date)
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_notify_date 
            ON notifications(notify_date, is_sent)
        """)
    
    return pool

async def get_pool():
    global pool
    if pool is None:
        pool = await init_db()
    return pool