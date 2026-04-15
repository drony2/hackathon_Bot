import asyncio
import json
import logging
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

from dbCon import DB_CONFIG


# ================= CONFIG =================

with open('package.json', 'r', encoding='utf-8') as file:
    data = json.load(file)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=data.get('token'))
dp = Dispatcher(storage=MemoryStorage())

pool = None


# ================= DB =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)


# ================= DATE =================

def parse_date(text: str):
    formats = ["%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d"]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except:
            pass

    return None


# ================= DB FUNCTIONS =================

async def add_user(tg_id, username):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, username)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO NOTHING
        """, tg_id, username)


async def add_subscription(tg_id, name, amount, date):
    async with pool.acquire() as conn:

        user = await conn.fetchrow("""
            SELECT id FROM users WHERE telegram_id = $1
        """, tg_id)

        if not user:
            return

        await conn.execute("""
            INSERT INTO subscriptions (
                user_id,
                name,
                amount,
                next_payment_date,
                currency,
                billing_period,
                billing_interval,
                status
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """,
        user["id"],
        name,
        amount,
        date,
        "RUB",
        "monthly",
        1,
        "active"
        )


async def get_all_subscriptions():
    async with pool.acquire() as conn:
        return await conn.fetch("""
            SELECT u.telegram_id, s.name, s.next_payment_date, s.amount
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
        """)


# ================= UI =================

def kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить")],
            [KeyboardButton(text="📋 Список")],
            [KeyboardButton(text="📊 Статистика")]
        ],
        resize_keyboard=True
    )


# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message):
    user_states.pop(message.from_user.id, None)
    await add_user(message.from_user.id, message.from_user.username)
    await message.answer("💳 Бот подписок", reply_markup=kb())


# ================= FSM =================

user_states = {}


@dp.message(lambda m: m.text == "➕ Добавить")
async def add(message: types.Message):
    user_states[message.from_user.id] = {"step": "name"}
    await message.answer("Введите название")


# ================= ОБРАБОТКА =================

@dp.message()
async def handler(message: types.Message):
    uid = message.from_user.id

    # ❗ игнор кнопок
    if message.text in ["➕ Добавить", "📋 Список", "📊 Статистика"]:
        return

    if uid not in user_states:
        return

    state = user_states[uid]

    if state["step"] == "name":
        state["name"] = message.text
        state["step"] = "amount"
        await message.answer("Введите сумму")

    elif state["step"] == "amount":
        try:
            state["amount"] = float(message.text)
            state["step"] = "date"
            await message.answer("Введите дату (YYYY-MM-DD или DD.MM.YYYY)")
        except:
            await message.answer("Введите число")

    elif state["step"] == "date":
        date = parse_date(message.text)

        if not date:
            await message.answer("❌ Неверная дата")
            return

        await add_subscription(
            uid,
            state["name"],
            state["amount"],
            date
        )

        user_states.pop(uid, None)
        await message.answer("✅ Добавлено", reply_markup=kb())


# ================= LIST =================

@dp.message(lambda m: m.text == "📋 Список")
async def list_subs(message: types.Message):
    user_states.pop(message.from_user.id, None)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.name, s.amount, s.next_payment_date
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE u.telegram_id = $1
            ORDER BY s.next_payment_date
        """, message.from_user.id)

    if not rows:
        await message.answer("❌ Нет подписок")
        return

    text = "📋 Подписки:\n\n"

    for r in rows:
        date = r["next_payment_date"]

        if isinstance(date, datetime):
            date = date.date()

        text += f"🔹 {r['name']}\n"
        text += f"   💰 {r['amount']}₽\n"
        text += f"   📅 {date}\n\n"

    await message.answer(text)


# ================= STATS =================

def calc_monthly(amount, period):
    if period == "monthly":
        return amount
    if period == "yearly":
        return amount / 12
    if period == "weekly":
        return amount * 4
    return amount


@dp.message(lambda m: m.text == "📊 Статистика")
async def stats(message: types.Message):
    user_states.pop(message.from_user.id, None)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.amount, s.billing_period
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE u.telegram_id = $1
        """, message.from_user.id)

    total = sum(calc_monthly(r["amount"], r["billing_period"]) for r in rows)

    await message.answer(
        f"📊 Статистика:\n\n"
        f"💸 В месяц: {round(total,2)}₽\n"
        f"📦 Подписок: {len(rows)}"
    )


# ================= NOTIFICATIONS =================

async def notification_loop():
    sent = set()

    while True:
        try:
            rows = await get_all_subscriptions()
            today = datetime.now().date()

            for r in rows:
                date = r["next_payment_date"]

                if isinstance(date, datetime):
                    date = date.date()

                key = (r["telegram_id"], r["name"], str(date))

                if key in sent:
                    continue

                if (date - today).days in [0, 1]:
                    await bot.send_message(
                        r["telegram_id"],
                        f"⏰ Напоминание: {r['name']} ({date}) — {r['amount']}₽"
                    )
                    sent.add(key)

        except Exception as e:
            print("LOOP ERROR:", e)

        await asyncio.sleep(10)


# ================= MAIN =================

async def main():
    await init_db()
    asyncio.create_task(notification_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())