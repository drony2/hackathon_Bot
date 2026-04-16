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
    period = State()
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


def spending_ideas(amount):
    if amount <= 300:
        return ["☕ кофе", "🍫 перекус", "📱 подписка"]
    elif amount <= 1000:
        return ["🍔 еда", "☕ кофе", "🎬 кино"]
    elif amount <= 3000:
        return ["🍕 доставка", "🎮 игры", "📺 сервисы"]
    else:
        return ["✈️ поездка", "🎮 покупки", "🍽 еда"]


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
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
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


def action_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Продлить", callback_data=f"renew_{sub_id}"),
            InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{sub_id}")
        ],
        [
            InlineKeyboardButton(text="➡️ Не продлевать", callback_data=f"skip_{sub_id}")
        ]
    ])


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


# ================= ADD FLOW =================

@dp.message(lambda m: m.text == "➕ Добавить")
async def add(message: types.Message, state: FSMContext):
    await state.set_state(AddSub.name)
    await message.answer("Название:", reply_markup=cancel_kb())


@dp.message(AddSub.name)
async def name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await state.set_state(AddSub.amount)
    await m.answer("Сумма:")


@dp.message(AddSub.amount)
async def amount(m: types.Message, state: FSMContext):
    try:
        val = float(m.text)
        await state.update_data(amount=val)
        await state.set_state(AddSub.currency)
        await m.answer("Валюта:", reply_markup=currency_kb())
    except:
        await m.answer("Ошибка числа")


@dp.callback_query(lambda c: c.data.startswith("cur_"))
async def currency(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(currency=c.data.split("_")[1])
    await state.set_state(AddSub.period)
    await c.message.answer("Период (дней):")
    await c.answer()


@dp.message(AddSub.period)
async def period(m: types.Message, state: FSMContext):
    try:
        days = int(m.text)
        await state.update_data(period=days)
        await state.set_state(AddSub.date)
        await m.answer("Дата (YYYY-MM-DD или DD.MM.YYYY):")
    except:
        await m.answer("Ошибка числа")


@dp.message(AddSub.date)
async def date(m: types.Message, state: FSMContext):
    d = parse_date(m.text)
    if not d:
        return await m.answer("Ошибка даты")

    data = await state.get_data()
    data["date"] = d

    await add_subscription(m.from_user.id, data)

    await state.clear()
    await m.answer("Добавлено", reply_markup=main_kb())


# ================= LIST =================

@dp.message(lambda m: m.text == "📋 Список")
async def list_subs(m: types.Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.id, s.name, s.amount, s.currency, s.next_payment_date
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE u.telegram_id=$1
            ORDER BY next_payment_date
        """, m.from_user.id)

    if not rows:
        await m.answer("Нет подписок")
        return

    for r in rows:
        date = r["next_payment_date"].strftime("%d.%m.%Y")

        await m.answer(
            f"📌 {r['name']}\n💰 {r['amount']} {r['currency']}\n📅 {date}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="❌ Удалить",
                    callback_data=f"del_{r['id']}"
                )]
            ])
        )


# ================= STATS =================

@dp.message(lambda m: m.text == "📊 Статистика")
async def stats(m: types.Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT amount, currency FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE u.telegram_id=$1
        """, m.from_user.id)

    total = {}
    for r in rows:
        total[r["currency"]] = total.get(r["currency"], 0) + r["amount"]

    text = "\n".join([f"{k}: {v}" for k, v in total.items()])
    await m.answer(text or "Нет данных")


# ================= NOTIFICATIONS (FIXED) =================

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

            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Списание: {pay_date}\n"
                f"🔁 Период: {r['period_days']} дней\n\n"
                f"💡 Идеи:\n{ideas}"
            )

            # ===== 3 дня (1 раз) =====
            if delta == 3 and not r["reminded_3d"]:
                await bot.send_message(r["telegram_id"], "⏳ Через 3 дня\n\n" + text)

                async with pool.acquire() as conn2:
                    await conn2.execute("""
                        UPDATE subscriptions
                        SET reminded_3d = TRUE
                        WHERE id=$1
                    """, r["id"])

            # ===== 1 день (1 раз) =====
            if delta == 1 and not r["reminded_1d"]:
                await bot.send_message(r["telegram_id"], "⏰ Завтра\n\n" + text)

                async with pool.acquire() as conn2:
                    await conn2.execute("""
                        UPDATE subscriptions
                        SET reminded_1d = TRUE
                        WHERE id=$1
                    """, r["id"])

            # ===== сегодня =====
            if delta <= 0:
                await bot.send_message(
                    r["telegram_id"],
                    "💸 Сегодня списание\n\n" + text,
                    reply_markup=action_kb(r["id"])
                )

                new_date = next_payment(r["next_payment_date"], r["period_days"])

                async with pool.acquire() as conn2:
                    await conn2.execute("""
                        UPDATE subscriptions
                        SET next_payment_date=$1,
                            reminded_3d=FALSE,
                            reminded_1d=FALSE
                        WHERE id=$2
                    """, new_date, r["id"])

        await asyncio.sleep(60)


# ================= ACTIONS =================

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text("❌ Удалено")
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("renew_"))
async def renew(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE subscriptions
            SET next_payment_date = next_payment_date + period_days * interval '1 day'
            WHERE id=$1
        """, sub_id)

    await c.message.edit_text("🔁 Продлено")
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("skip_"))
async def skip(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text("❌ Подписка завершена")
    await c.answer()


# ================= MAIN =================

async def main():
    await init_db()
    asyncio.create_task(notification_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())