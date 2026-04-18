from app.config.settings import KEYBOARD_VERSION, MAX_SUBSCRIPTIONS
from app.db.connection import get_pool

from app.keyboards.keyboards import main_kb

from datetime import datetime

from aiogram import types


async def add_user(tg_id, username, first_name):
    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("""
                                   INSERT INTO users (telegram_id, username, first_name)
                                   VALUES ($1, $2, $3) ON CONFLICT (telegram_id) DO
                                   UPDATE
                                       SET username = EXCLUDED.username
                                       RETURNING id
                                   """, tg_id, username, first_name)

        if user:
            await conn.execute("""
                               INSERT INTO user_settings (user_id, keyboard_version)
                               VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING
                               """, user["id"], KEYBOARD_VERSION)

        return user["id"] if user else None

async def add_subscription(tg_id, data):

    async with get_pool().acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )

        await conn.execute("""
            INSERT INTO subscriptions
            (user_id, name, amount, currency, next_payment_date, period_days,
             reminded_3d, reminded_1d, reminded_today, status, period_type)
            VALUES ($1, $2, $3, $4, $5, $6, FALSE, FALSE, FALSE, 'active', $7)
        """,
        user["id"],
        data["name"],
        data["amount"],
        data["currency"],
        data["date"],
        data["period"],
        data.get("period_type")  # ← добавить period_type
        )

async def get_payment_history(tg_id, limit=20):
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT p.id,
                                       p.subscription_id,
                                       p.amount,
                                       p.payment_date,
                                       p.status,
                                       s.name as subscription_name,
                                       s.currency
                                FROM payments p
                                         JOIN subscriptions s ON s.id = p.subscription_id
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY p.payment_date DESC, p.id DESC
                                    LIMIT $2
                                """, tg_id, limit)
        return rows

async def set_budget(tg_id, currency, monthly_limit):
    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )
        if user:
            existing = await conn.fetchrow(
                "SELECT id FROM budgets WHERE user_id=$1 AND currency=$2",
                user["id"], currency
            )
            if existing:
                await conn.execute("""
                                   UPDATE budgets
                                   SET monthly_limit = $1
                                   WHERE user_id = $2
                                     AND currency = $3
                                   """, monthly_limit, user["id"], currency)
            else:
                await conn.execute("""
                                   INSERT INTO budgets (user_id, currency, monthly_limit)
                                   VALUES ($1, $2, $3)
                                   """, user["id"], currency, monthly_limit)


async def get_budget(tg_id, currency=None):
    pool = get_pool()
    async with pool.acquire() as conn:
        if currency:
            row = await conn.fetchrow("""
                                      SELECT b.monthly_limit
                                      FROM budgets b
                                               JOIN users u ON u.id = b.user_id
                                      WHERE u.telegram_id = $1
                                        AND b.currency = $2
                                      """, tg_id, currency)
            return row["monthly_limit"] if row else None
        else:
            rows = await conn.fetch("""
                                    SELECT b.currency, b.monthly_limit
                                    FROM budgets b
                                             JOIN users u ON u.id = b.user_id
                                    WHERE u.telegram_id = $1
                                    """, tg_id)
            return {row["currency"]: float(row["monthly_limit"]) for row in rows}


async def check_budget_status(tg_id, currency):
    budget = await get_budget(tg_id, currency)
    if not budget:
        return None

    spending = await get_monthly_spending(tg_id, currency)

    total_spent = 0
    for row in spending:
        total_spent += float(row["total"])

    return {
        "currency": currency,
        "limit": float(budget),
        "spent": total_spent,
        "remaining": float(budget) - total_spent
    }

async def add_notification(tg_id, subscription_id, notify_date, notify_type):
    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )
        if user:
            await conn.execute("""
                               INSERT INTO notifications (user_id, subscription_id, notify_date, type, is_sent)
                               VALUES ($1, $2, $3, $4, FALSE)
                               """, user["id"], subscription_id, notify_date, notify_type)

async def check_subscription_exists(tg_id, name):
    """Проверяет, существует ли у пользователя подписка с таким названием"""
    clean_name = " ".join(name.split())
    pool = get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("""
                                     SELECT EXISTS(SELECT 1
                                                   FROM subscriptions s
                                                            JOIN users u ON u.id = s.user_id
                                                   WHERE u.telegram_id = $1
                                                     AND LOWER(s.name) = LOWER($2))
                                     """, tg_id, clean_name)
        return exists


async def check_subscription_limit(tg_id: int) -> bool:
    pool = get_pool()
    """Проверяет, не превышен ли лимит подписок"""
    async with pool.acquire() as conn:
        count = await conn.fetchval("""
                                    SELECT COUNT(*)
                                    FROM subscriptions s
                                             JOIN users u ON u.id = s.user_id
                                    WHERE u.telegram_id = $1
                                    """, tg_id)
        return count >= MAX_SUBSCRIPTIONS


async def check_and_update_keyboard(user_tg_id: int, message: types.Message = None):
    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            user_tg_id
        )

        if not user:
            return False

        settings = await conn.fetchrow(
            "SELECT keyboard_version FROM user_settings WHERE user_id=$1",
            user["id"]
        )

        current_version = settings["keyboard_version"] if settings else None

        if current_version != KEYBOARD_VERSION:
            await conn.execute("""
                               INSERT INTO user_settings (user_id, keyboard_version, updated_at)
                               VALUES ($1, $2, NOW()) ON CONFLICT (user_id) 
                DO
                               UPDATE SET keyboard_version = $2, updated_at = NOW()
                               """, user["id"], KEYBOARD_VERSION)

            if message:
                await message.answer(
                    "🔄 Клавиатура обновлена!",
                    reply_markup=main_kb()
                )

            return True

        return False


async def force_update_keyboard(user_tg_id: int):
    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            user_tg_id
        )

        if user:
            await conn.execute("""
                               INSERT INTO user_settings (user_id, keyboard_version, updated_at)
                               VALUES ($1, $2, NOW()) ON CONFLICT (user_id) 
                DO
                               UPDATE SET keyboard_version = $2, updated_at = NOW()
                               """, user["id"], KEYBOARD_VERSION)

async def add_payment_record(tg_id, subscription_id, amount, payment_date, status="paid"):
    pool = get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )
        if user:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, $3, $4, NOW())
                               """, subscription_id, amount, payment_date, status)

async def get_monthly_spending(tg_id, currency=None, year=None, month=None):
    if year is None or month is None:
        today = datetime.now()
        year = today.year
        month = today.month
    pool = get_pool()
    async with pool.acquire() as conn:
        query = """
                SELECT s.currency,
                       SUM(p.amount) as total
                FROM payments p
                         JOIN subscriptions s ON s.id = p.subscription_id
                         JOIN users u ON u.id = s.user_id
                WHERE u.telegram_id = $1
                  AND EXTRACT(YEAR FROM p.payment_date) = $2
                  AND EXTRACT(MONTH FROM p.payment_date) = $3
                  AND p.status = 'paid' \
                """
        params = [tg_id, year, month]

        if currency:
            query += " AND s.currency = $4"
            params.append(currency)

        query += " GROUP BY s.currency"

        rows = await conn.fetch(query, *params)
        return rows
