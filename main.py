import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
import aiosqlite

API_TOKEN = "YOUR_BOT_TOKEN"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

DB_NAME = "subscriptions.db"

user_states = {}

# ------------------ БАЗА ------------------

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            amount REAL,
            billing_period TEXT,
            next_payment_date TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER,
            amount REAL,
            payment_date TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            monthly_limit REAL
        )
        """)

        await db.commit()


# ------------------ КНОПКИ ------------------

def get_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить подписку")],
            [KeyboardButton(text="📋 Мои подписки")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="💰 Установить лимит")]
        ],
        resize_keyboard=True
    )


# ------------------ /start ------------------

@dp.message(Command("start"))
async def start(message: types.Message):
    user_states.pop(message.from_user.id, None)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)",
            (message.from_user.id, message.from_user.username)
        )
        await db.commit()

    await message.answer("💳 Бот учёта подписок", reply_markup=get_kb())


# ------------------ FSM ------------------

@dp.message(lambda m: m.text == "➕ Добавить подписку")
async def add_sub(message: types.Message):
    user_states[message.from_user.id] = {"step": "name"}
    await message.answer("Название подписки:")


@dp.message(lambda m: m.text == "💰 Установить лимит")
async def set_limit(message: types.Message):
    user_states[message.from_user.id] = {"step": "limit"}
    await message.answer("Введите месячный лимит (€):")


# ------------------ ОБРАБОТКА ВВОДА ------------------

@dp.message()
async def handle(message: types.Message):
    uid = message.from_user.id

    # 🔥 игнор кнопок
    if message.text in [
        "➕ Добавить подписку",
        "📋 Мои подписки",
        "📊 Статистика",
        "💰 Установить лимит"
    ]:
        return

    if uid not in user_states:
        return

    state = user_states[uid]

    # -------- лимит --------
    if state["step"] == "limit":
        try:
            limit = float(message.text)

            async with aiosqlite.connect(DB_NAME) as db:
                cur = await db.execute(
                    "SELECT id FROM users WHERE telegram_id = ?",
                    (uid,))
                user_id = (await cur.fetchone())[0]

                await db.execute("""
                    INSERT OR REPLACE INTO budgets (user_id, monthly_limit)
                    VALUES (?, ?)
                """, (user_id, limit))
                await db.commit()

            user_states.pop(uid)
            await message.answer("✅ Лимит сохранён")

        except:
            await message.answer("Введите число!")

    # -------- подписка --------
    elif state["step"] == "name":
        state["name"] = message.text
        state["step"] = "amount"
        await message.answer("Сумма (€):")

    elif state["step"] == "amount":
        try:
            state["amount"] = float(message.text)
            state["step"] = "period"
            await message.answer("Период (monthly/yearly/weekly):")
        except:
            await message.answer("Введите число!")

    elif state["step"] == "period":
        if message.text not in ["monthly", "yearly", "weekly"]:
            await message.answer("Введите: monthly / yearly / weekly")
            return

        state["period"] = message.text
        state["step"] = "date"
        await message.answer("Дата платежа (YYYY-MM-DD):")

    elif state["step"] == "date":
        try:
            datetime.strptime(message.text, "%Y-%m-%d")

            async with aiosqlite.connect(DB_NAME) as db:
                cur = await db.execute(
                    "SELECT id FROM users WHERE telegram_id = ?",
                    (uid,))
                user_id = (await cur.fetchone())[0]

                await db.execute("""
                    INSERT INTO subscriptions 
                    (user_id, name, amount, billing_period, next_payment_date)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, state["name"], state["amount"],
                      state["period"], message.text))

                await db.commit()

            user_states.pop(uid)
            await message.answer("✅ Подписка добавлена")

        except:
            await message.answer("Неверная дата!")


# ------------------ СПИСОК ------------------

@dp.message(lambda m: m.text == "📋 Мои подписки")
async def list_subs(message: types.Message):
    user_states.pop(message.from_user.id, None)

    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("""
            SELECT name, amount, billing_period, next_payment_date
            FROM subscriptions s
            JOIN users u ON s.user_id = u.id
            WHERE u.telegram_id = ?
            ORDER BY next_payment_date
        """, (message.from_user.id,))
        
        rows = await cur.fetchall()

    if not rows:
        await message.answer("❌ У тебя нет подписок")
        return

    text = "📋 Твои подписки:\n\n"

    for name, amount, period, date in rows:
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
            date_str = d.strftime("%d.%m.%Y")
        except:
            date_str = date

        text += f"🔹 {name}\n"
        text += f"   💰 {amount}€ ({period})\n"
        text += f"   📅 {date_str}\n\n"

    await message.answer(text)


# ------------------ СТАТИСТИКА ------------------

def calc_monthly(amount, period):
    if period == "monthly":
        return amount
    if period == "yearly":
        return amount / 12
    if period == "weekly":
        return amount * 4
    return amount


async def get_budget(tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("""
            SELECT monthly_limit FROM budgets b
            JOIN users u ON b.user_id = u.id
            WHERE u.telegram_id = ?
        """, (tg_id,))
        row = await cur.fetchone()
        return row[0] if row else None


@dp.message(lambda m: m.text == "📊 Статистика")
async def stats(message: types.Message):
    user_states.pop(message.from_user.id, None)

    async with aiosqlite.connect(DB_NAME) as db:

        cur = await db.execute("""
            SELECT amount, billing_period
            FROM subscriptions s
            JOIN users u ON s.user_id = u.id
            WHERE u.telegram_id = ?
        """, (message.from_user.id,))
        subs = await cur.fetchall()

    monthly_total = sum(calc_monthly(a, p) for a, p in subs)

    text = f"📊 Статистика:\n\n"
    text += f"💸 В месяц: {round(monthly_total,2)}€\n"
    text += f"📦 Подписок: {len(subs)}\n"

    limit = await get_budget(message.from_user.id)
    if limit:
        text += f"\n🎯 Лимит: {limit}€\n"
        if monthly_total > limit:
            text += "🚨 Превышение!"
        else:
            text += f"✅ Осталось: {round(limit-monthly_total,2)}€"

    await message.answer(text)


# ------------------ НАПОМИНАНИЯ ------------------

async def reminder_loop():
    while True:
        async with aiosqlite.connect(DB_NAME) as db:
            cur = await db.execute("""
                SELECT u.telegram_id, s.name, s.next_payment_date
                FROM subscriptions s
                JOIN users u ON s.user_id = u.id
            """)
            rows = await cur.fetchall()

        today = datetime.now().date()

        for tg_id, name, date_str in rows:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()

            if d - today == timedelta(days=1):
                try:
                    await bot.send_message(tg_id, f"⏰ Завтра: {name}")
                except:
                    pass

        await asyncio.sleep(86400)


# ------------------ RUN ------------------


async def main():
    await init_db()
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

