import asyncio
import json
import logging
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from dbCon import DB_CONFIG

# ================= CONFIG =================

with open('package.json', 'r', encoding='utf-8') as file:
    data = json.load(file)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=data.get('token'))
dp = Dispatcher(storage=MemoryStorage())

pool = None


# ================= DB INIT =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)


# ================= FSM =================

class AddSub(StatesGroup):
    name = State()
    amount = State()
    currency = State()
    period = State()   # 🔥 ДОБАВЛЕНО
    date = State()


# ================= UTILS =================

def parse_date(text):
    formats = ["%Y-%m-%d", "%d.%m.%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except:
            pass
    return None


def next_payment(date, days):
    return date + timedelta(days=days)


# ================= ИДЕИ РАСХОДОВ =================

def spending_ideas(amount):
    if amount <= 300:
        return [
            "☕ 1 кофе в кафе",
            "🍫 небольшой перекус",
            "📱 часть мобильной подписки"
        ]
    elif amount <= 1000:
        return [
            "☕ 3–5 кофе",
            "🍔 1–2 доставки еды",
            "🎬 1 кино",
            "🎧 Spotify / YouTube Premium"
        ]
    elif amount <= 3000:
        return [
            "🍕 несколько доставок еды",
            "🎮 игровая подписка",
            "🎬 2–3 кино",
            "📺 Netflix / сервисы"
        ]
    else:
        return [
            "🍽 регулярная доставка еды",
            "✈️ накопления на поездку",
            "🎮 крупные покупки в играх",
            "📱 несколько подписок"
        ]


# ================= DB =================

async def add_user(tg_id, username):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, username)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO NOTHING
        """, tg_id, username)


async def add_subscription(tg_id, data):
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1", tg_id
        )

        await conn.execute("""
            INSERT INTO subscriptions
            (user_id, name, amount, currency, next_payment_date, period_days,
             reminded_3d, reminded_1d)
            VALUES ($1,$2,$3,$4,$5,$6,FALSE,FALSE)
        """,
        user["id"],
        data["name"],
        data["amount"],
        data["currency"],
        data["date"],
        data["period"]
        )


# ================= UI =================

def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить")],
            [KeyboardButton(text="📋 Список")],
            [KeyboardButton(text="📊 Статистика")]
        ],
        resize_keyboard=True
    )


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


def currency_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="₽ RUB", callback_data="cur_RUB"),
                InlineKeyboardButton(text="$ USD", callback_data="cur_USD")
            ]
        ]
    )


# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id, message.from_user.username)
    await message.answer("💳 Бот подписок", reply_markup=main_kb())


# ================= CANCEL =================

@dp.message(lambda m: m.text == "❌ Отмена")
async def cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено", reply_markup=main_kb())


# ================= ADD =================

@dp.message(lambda m: m.text == "➕ Добавить")
async def add(message: types.Message, state: FSMContext):
    await state.set_state(AddSub.name)
    await message.answer("Введите название", reply_markup=cancel_kb())


@dp.message(AddSub.name)
async def get_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AddSub.amount)
    await message.answer("Введите сумму")


# ================= СУММА (без изменений логики) =================

@dp.message(AddSub.amount)
async def get_amount(message: types.Message, state: FSMContext):
    text = message.text.replace(",", ".")

    try:
        amount = float(text)

        if amount <= 0:
            await message.answer("❗ Сумма должна быть больше 0")
            return

        if amount > 1_000_000:
            await message.answer("❗ Слишком большая сумма")
            return

        await state.update_data(amount=round(amount, 2))
        await state.set_state(AddSub.currency)

        await message.answer("Выберите валюту:", reply_markup=currency_kb())

    except:
        await message.answer("❗ Введите корректное число")


# ================= ВАЛЮТА =================

@dp.callback_query(lambda c: c.data.startswith("cur_"))
async def set_currency(callback: types.CallbackQuery, state: FSMContext):
    currency = callback.data.split("_")[1]

    await state.update_data(currency=currency)
    await state.set_state(AddSub.period)

    await callback.message.answer("Введите период подписки в днях (7 / 30 / 365)")
    await callback.answer()


# ================= ПЕРИОД =================

@dp.message(AddSub.period)
async def get_period(message: types.Message, state: FSMContext):
    try:
        days = int(message.text)

        if days <= 0:
            await message.answer("❗ Период должен быть больше 0")
            return

        await state.update_data(period=days)
        await state.set_state(AddSub.date)

        await message.answer("Введите дату (YYYY-MM-DD или DD.MM.YYYY)")

    except:
        await message.answer("❗ Введите число")


# ================= ДАТА =================

@dp.message(AddSub.date)
async def get_date(message: types.Message, state: FSMContext):
    date = parse_date(message.text)

    if not date:
        await message.answer("❌ Неверная дата")
        return

    data = await state.get_data()
    data["date"] = date

    await add_subscription(message.from_user.id, data)

    await state.clear()
    await message.answer("✅ Добавлено", reply_markup=main_kb())


# ================= LIST =================

@dp.message(lambda m: m.text == "📋 Список")
async def list_subs(message: types.Message, state: FSMContext):

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.id, s.name, s.amount, s.currency, s.next_payment_date
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE u.telegram_id=$1
            ORDER BY next_payment_date
        """, message.from_user.id)

    if not rows:
        await message.answer("❌ Нет подписок")
        return

    for r in rows:
        formatted_date = r["next_payment_date"].strftime("%d.%m.%Y")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="❌ Удалить подписку",
                callback_data=f"del_{r['id']}"
            )]
        ])

        await message.answer(
            f"📌 {r['name']} — {r['amount']} {r['currency']} ({formatted_date})",
            reply_markup=kb
        )


# ================= DELETE =================

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete_subscription(callback: types.CallbackQuery):
    sub_id = int(callback.data.split("_")[1])

    async with pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM subscriptions
            WHERE id = $1
        """, sub_id)

    await callback.message.edit_text("🗑 Подписка удалена")
    await callback.answer("Удалено")


# ================= STATS =================

@dp.message(lambda m: m.text == "📊 Статистика")
async def stats(message: types.Message):

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT name, amount, currency
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE u.telegram_id=$1
        """, message.from_user.id)

    if not rows:
        await message.answer("❌ Нет подписок")
        return

    by_currency = {}
    text = "📊 Расходы:\n"

    for r in rows:
        cur = r["currency"]
        by_currency[cur] = by_currency.get(cur, 0) + r["amount"]

    for cur, total in by_currency.items():
        text += f"\n{cur}: {round(total,2)}"

    await message.answer(text)


# ================= NOTIFICATIONS =================

async def notification_loop():
    while True:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT s.*, u.telegram_id
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
            """)

        today = datetime.now().date()

        for r in rows:
            delta = (r["next_payment_date"] - today).days
            pay_date = r["next_payment_date"].strftime("%d.%m.%Y")

            ideas = "\n".join(spending_ideas(r["amount"]))

            if delta == 3 and not r["reminded_3d"]:
                await bot.send_message(
                    r["telegram_id"],
                    f"💡 Можно потратить:\n{ideas}\n\n⚠️ Списание {pay_date}"
                )

            if delta == 1 and not r["reminded_1d"]:
                await bot.send_message(
                    r["telegram_id"],
                    f"💡 Можно потратить:\n{ideas}\n\n⏰ Завтра списание ({pay_date})"
                )

            if delta <= 0:
                await bot.send_message(
                    r["telegram_id"],
                    f"💸 Списание сегодня ({pay_date})"
                )

                new_date = next_payment(
                    r["next_payment_date"],
                    r["period_days"]
                )

                await conn.execute("""
                    UPDATE subscriptions
                    SET next_payment_date=$1,
                        reminded_3d=FALSE,
                        reminded_1d=FALSE
                    WHERE id=$2
                """, new_date, r["id"])

        await asyncio.sleep(60)


# ================= MAIN =================

async def main():
    await init_db()
    asyncio.create_task(notification_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())