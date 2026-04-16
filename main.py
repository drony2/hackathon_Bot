import asyncio
import json
import logging
from datetime import datetime, timedelta
from functools import wraps

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

from DB.dbCon import DB_CONFIG

# ================= CONFIG =================

with open('package.json', 'r', encoding='utf-8') as file:
    data = json.load(file)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=data.get('token'))
dp = Dispatcher(storage=MemoryStorage())

pool = None

# Версия клавиатуры (увеличивайте при изменении main_kb)
KEYBOARD_VERSION = "2.0"


# ================= DB INIT =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)

    # Создаём таблицу для хранения версии клавиатуры пользователя
    async with pool.acquire() as conn:
        await conn.execute("""
                           CREATE TABLE IF NOT EXISTS user_settings
                           (
                               user_id
                               INTEGER
                               PRIMARY
                               KEY
                               REFERENCES
                               users
                           (
                               id
                           ),
                               keyboard_version TEXT,
                               updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                               )
                           """)


# ================= FSM =================

class AddSub(StatesGroup):
    name = State()
    amount = State()
    currency = State()
    period = State()
    date = State()


class SetBudget(StatesGroup):
    monthly_limit = State()


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


# ================= ДЕКОРАТОР ДЛЯ АВТООБНОВЛЕНИЯ КЛАВИАТУРЫ =================

def ensure_keyboard_version(func):
    """Декоратор для проверки и обновления версии клавиатуры"""

    @wraps(func)
    async def wrapper(message: types.Message, *args, **kwargs):
        # Проверяем версию клавиатуры пользователя
        if hasattr(message, 'from_user') and message.from_user:
            await check_and_update_keyboard(message.from_user.id, message)

        # Выполняем основную функцию
        return await func(message, *args, **kwargs)

    return wrapper


async def check_and_update_keyboard(user_tg_id: int, message: types.Message = None):
    """Проверяет версию клавиатуры и обновляет при необходимости"""
    async with pool.acquire() as conn:
        # Получаем пользователя
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            user_tg_id
        )

        if not user:
            return False

        # Проверяем версию клавиатуры
        settings = await conn.fetchrow(
            "SELECT keyboard_version FROM user_settings WHERE user_id=$1",
            user["id"]
        )

        current_version = settings["keyboard_version"] if settings else None

        # Если версия отличается или не установлена
        if current_version != KEYBOARD_VERSION:
            # Обновляем версию в БД
            await conn.execute("""
                               INSERT INTO user_settings (user_id, keyboard_version, updated_at)
                               VALUES ($1, $2, NOW()) ON CONFLICT (user_id) 
                DO
                               UPDATE SET keyboard_version = $2, updated_at = NOW()
                               """, user["id"], KEYBOARD_VERSION)

            # Если передан объект сообщения, отправляем новую клавиатуру
            if message:
                await message.answer(
                    "🔄 Клавиатура обновлена!",
                    reply_markup=main_kb()
                )

            return True

        return False


async def force_update_keyboard(user_tg_id: int):
    """Принудительно обновляет клавиатуру пользователя"""
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


# ================= ДЕКОРАТОР ДЛЯ ОБРАБОТЧИКОВ С ВОЗВРАТОМ КЛАВИАТУРЫ =================

def with_keyboard_update(func):
    """Декоратор для автоматического добавления клавиатуры к ответу"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)

        # Ищем message в аргументах
        message = None
        for arg in args:
            if isinstance(arg, types.Message):
                message = arg
                break

        # Если функция вернула текст и есть message
        if result and isinstance(result, str) and message:
            await message.answer(result, reply_markup=main_kb())
            return

        return result

    return wrapper


# ================= DB =================

async def add_user(tg_id, username):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("""
                                   INSERT INTO users (telegram_id, username)
                                   VALUES ($1, $2) ON CONFLICT (telegram_id) DO
                                   UPDATE
                                       SET username = EXCLUDED.username
                                       RETURNING id
                                   """, tg_id, username)

        # Инициализируем настройки пользователя
        await conn.execute("""
                           INSERT INTO user_settings (user_id, keyboard_version)
                           VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING
                           """, user["id"], KEYBOARD_VERSION)

        return user["id"]


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
                           VALUES ($1, $2, $3, $4, $5, $6, FALSE, FALSE)
                           """,
                           user["id"],
                           data["name"],
                           data["amount"],
                           data["currency"],
                           data["date"],
                           data["period"]
                           )


# ================= НОВЫЕ ФУНКЦИИ ДЛЯ PAYMENTS =================

async def add_payment_record(tg_id, subscription_id, amount, payment_date, status="paid"):
    """Добавляет запись об оплате в таблицу payments"""
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


async def get_payment_history(tg_id, limit=10):
    """Получает историю платежей пользователя"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT p.*, s.name as subscription_name, s.currency
                                FROM payments p
                                         JOIN subscriptions s ON s.id = p.subscription_id
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY p.payment_date DESC
                                    LIMIT $2
                                """, tg_id, limit)
        return rows


async def get_monthly_spending(tg_id, year=None, month=None):
    """Получает траты за указанный месяц (по умолчанию текущий)"""
    if year is None or month is None:
        today = datetime.now()
        year = today.year
        month = today.month

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT s.currency,
                                       SUM(p.amount) as total
                                FROM payments p
                                         JOIN subscriptions s ON s.id = p.subscription_id
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                  AND EXTRACT(YEAR FROM p.payment_date) = $2
                                  AND EXTRACT(MONTH FROM p.payment_date) = $3
                                  AND p.status = 'paid'
                                GROUP BY s.currency
                                """, tg_id, year, month)
        return rows


# ================= НОВЫЕ ФУНКЦИИ ДЛЯ BUDGETS =================

async def set_budget(tg_id, monthly_limit):
    """Устанавливает или обновляет месячный лимит бюджета"""
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )
        if user:
            existing = await conn.fetchrow(
                "SELECT id FROM budgets WHERE user_id=$1",
                user["id"]
            )
            if existing:
                await conn.execute("""
                                   UPDATE budgets
                                   SET monthly_limit = $1
                                   WHERE user_id = $2
                                   """, monthly_limit, user["id"])
            else:
                await conn.execute("""
                                   INSERT INTO budgets (user_id, monthly_limit)
                                   VALUES ($1, $2)
                                   """, user["id"], monthly_limit)


async def get_budget(tg_id):
    """Получает текущий месячный лимит бюджета"""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
                                  SELECT b.monthly_limit
                                  FROM budgets b
                                           JOIN users u ON u.id = b.user_id
                                  WHERE u.telegram_id = $1
                                  """, tg_id)
        return row["monthly_limit"] if row else None


async def check_budget_status(tg_id):
    """Проверяет статус бюджета: траты, лимит, остаток"""
    budget = await get_budget(tg_id)
    if not budget:
        return None

    spending = await get_monthly_spending(tg_id)

    total_spent = {}
    for row in spending:
        total_spent[row["currency"]] = float(row["total"])

    return {
        "limit": float(budget),
        "spent": total_spent,
        "remaining": float(budget) - sum(total_spent.values())
    }


# ================= НОВЫЕ ФУНКЦИИ ДЛЯ NOTIFICATIONS =================

async def add_notification(tg_id, subscription_id, notify_date, notify_type):
    """Добавляет запись об уведомлении"""
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


async def mark_notification_sent(tg_id, subscription_id, notify_date):
    """Отмечает уведомление как отправленное"""
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )
        if user:
            await conn.execute("""
                               UPDATE notifications
                               SET is_sent = TRUE
                               WHERE user_id = $1
                                 AND subscription_id = $2
                                 AND notify_date = $3
                               """, user["id"], subscription_id, notify_date)


async def get_pending_notifications(tg_id):
    """Получает неотправленные уведомления на сегодня"""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT n.*, s.name, s.amount, s.currency
                                FROM notifications n
                                         JOIN users u ON u.id = n.user_id
                                         JOIN subscriptions s ON s.id = n.subscription_id
                                WHERE u.telegram_id = $1
                                  AND n.notify_date <= CURRENT_DATE
                                  AND n.is_sent = FALSE
                                """, tg_id)
        return rows


# ================= UI =================

def main_kb():
    """Главная клавиатура (версия 2.0)"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить")],
            [KeyboardButton(text="📋 Список")],
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="💰 Бюджет")],
            [KeyboardButton(text="📜 История платежей")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
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
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
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


def budget_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Установить лимит", callback_data="set_budget")],
        [InlineKeyboardButton(text="📊 Проверить статус", callback_data="check_budget")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])


# ================= ОБРАБОТЧИК ОТМЕНЫ ДЛЯ INLINE КНОПОК =================

@dp.callback_query(lambda c: c.data == "cancel_action")
async def cancel_inline(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.delete()
    await c.message.answer("❌ Действие отменено", reply_markup=main_kb())
    await c.answer()

# ================= START =================

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id, message.from_user.username)

    # Принудительно обновляем клавиатуру
    await force_update_keyboard(message.from_user.id)

    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"💳 Я бот для управления подписками.\n"
        f"📊 Я помогу тебе отслеживать все твои подписки и не пропустить оплату.\n\n"
        f"✨ Возможности:\n"
        f"• Добавление подписок\n"
        f"• Напоминания об оплате\n"
        f"• Статистика расходов\n"
        f"• Установка бюджета\n"
        f"• История платежей\n\n"
        f"👇 Выбери действие в меню:"
    )

    await message.answer(welcome_text, reply_markup=main_kb())


@dp.message(Command("menu"))
async def show_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("📋 Главное меню:", reply_markup=main_kb())


@dp.message(Command("update"))
async def force_update(message: types.Message):
    """Принудительное обновление клавиатуры"""
    await force_update_keyboard(message.from_user.id)
    await message.answer("✅ Клавиатура обновлена!", reply_markup=main_kb())


# ================= CANCEL =================

@dp.message(lambda m: m.text == "❌ Отмена")
async def cancel_message(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer("❌ Действие отменено", reply_markup=main_kb())
    else:
        await message.answer("👋 Нет активных действий для отмены", reply_markup=main_kb())


# ================= ADD FLOW =================

@dp.message(lambda m: m.text == "➕ Добавить")
async def add(message: types.Message, state: FSMContext):
    await state.set_state(AddSub.name)
    await message.answer("📝 Введите название подписки:", reply_markup=cancel_kb())


@dp.message(AddSub.name)
async def name(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    if len(m.text) > 100:
        return await m.answer("❗ Название слишком длинное (макс. 100 символов)")

    await state.update_data(name=m.text)
    await state.set_state(AddSub.amount)
    await m.answer("💰 Введите сумму:", reply_markup=cancel_kb())


@dp.message(AddSub.amount)
async def amount(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Добавление отменено", reply_markup=main_kb())

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

        await message.answer("💱 Выберите валюту:", reply_markup=currency_kb())

    except:
        await message.answer("❗ Введите корректное число (например: 199.99)")


@dp.callback_query(lambda c: c.data.startswith("cur_"))
async def currency(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(currency=c.data.split("_")[1])
    await state.set_state(AddSub.period)
    await c.message.delete()
    await c.message.answer("📅 Введите период (количество дней):", reply_markup=cancel_kb())
    await c.answer()


@dp.message(AddSub.period)
async def period(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    try:
        days = int(m.text)
        if days <= 0:
            return await m.answer("❗ Период должен быть больше 0")

        if days > 365:
            return await m.answer("❗ Период не может быть больше 365 дней")

        await state.update_data(period=days)
        await state.set_state(AddSub.date)
        await m.answer("📅 Введите дату следующего платежа (YYYY-MM-DD или DD.MM.YYYY):", reply_markup=cancel_kb())
    except:
        await m.answer("❗ Введите целое число дней")


@dp.message(AddSub.date)
async def date(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    d = parse_date(m.text)
    if not d:
        return await m.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или DD.MM.YYYY")

    data = await state.get_data()
    data["date"] = d

    await add_subscription(m.from_user.id, data)

    await state.clear()

    # Формируем сообщение с деталями добавленной подписки
    details = (
        f"✅ Подписка добавлена!\n\n"
        f"📌 Название: {data['name']}\n"
        f"💰 Сумма: {data['amount']} {data['currency']}\n"
        f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
        f"🔁 Период: {data['period']} дней"
    )

    await m.answer(details, reply_markup=main_kb())


# ================= LIST =================

@dp.message(lambda m: m.text == "📋 Список")
async def list_subs(m: types.Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT s.id, s.name, s.amount, s.currency, s.next_payment_date, s.period_days
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY next_payment_date
                                """, m.from_user.id)

    if not rows:
        await m.answer("📭 У вас пока нет подписок", reply_markup=main_kb())
        return

    for r in rows:
        date = r["next_payment_date"].strftime("%d.%m.%Y")

        text = (
            f"📌 {r['name']}\n"
            f"💰 {r['amount']} {r['currency']}\n"
            f"📅 Следующий платёж: {date}\n"
            f"🔁 Период: {r['period_days']} дней"
        )

        await m.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="❌ Удалить",
                    callback_data=f"del_{r['id']}"
                )]
            ])
        )

    # Возвращаем главное меню после списка
    await m.answer("👆 Это все ваши подписки", reply_markup=main_kb())


# ================= STATS =================

@dp.message(lambda m: m.text == "📊 Статистика")
async def stats(message: types.Message, state: FSMContext):
    if await state.get_state():
        await message.answer("⚠️ Сначала завершите текущее действие", reply_markup=main_kb())
        return

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT name, amount, currency, period_days
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                """, message.from_user.id)

    if not rows:
        await message.answer("❌ Нет подписок для статистики", reply_markup=main_kb())
        return

    text = "📊 Ваши подписки:\n\n"
    by_currency = {}
    total_monthly = {}
    total_yearly = {}

    for r in rows:
        cur = r["currency"]

        if cur not in by_currency:
            by_currency[cur] = {"total": 0, "items": [], "monthly": 0, "yearly": 0}

        by_currency[cur]["total"] += r["amount"]
        by_currency[cur]["items"].append(
            f"• {r['name']} — {r['amount']} {cur} (каждые {r['period_days']} дн.)"
        )

        # Расчёт месячных и годовых трат
        monthly_cost = (r["amount"] * 30) / r["period_days"]
        yearly_cost = (r["amount"] * 365) / r["period_days"]

        by_currency[cur]["monthly"] += monthly_cost
        by_currency[cur]["yearly"] += yearly_cost

    for cur, data in by_currency.items():
        text += f"\n💱 {cur}:\n"
        text += "\n".join(data["items"])
        text += f"\n\n📊 Итоги по {cur}:"
        text += f"\n💸 За период: {round(data['total'], 2)} {cur}"
        text += f"\n📅 В месяц: ~{round(data['monthly'], 2)} {cur}"
        text += f"\n📆 В год: ~{round(data['yearly'], 2)} {cur}\n"

    await message.answer(text, reply_markup=main_kb())


# ================= БЮДЖЕТ =================

@dp.message(lambda m: m.text == "💰 Бюджет")
async def budget_menu(message: types.Message):
    budget = await get_budget(message.from_user.id)

    if budget:
        status = await check_budget_status(message.from_user.id)
        spent = sum(status["spent"].values()) if status["spent"] else 0

        text = (
            f"💰 Текущий бюджет:\n"
            f"📊 Лимит: {budget:,.2f} ₽\n"
            f"💸 Потрачено: {spent:,.2f} ₽\n"
            f"✨ Осталось: {status['remaining']:,.2f} ₽"
        ).replace(",", " ")
    else:
        text = "💰 Бюджет не установлен"

    await message.answer(text, reply_markup=budget_kb())


@dp.callback_query(lambda c: c.data == "set_budget")
async def set_budget_start(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetBudget.monthly_limit)
    await c.message.delete()
    await c.message.answer("💰 Введите месячный лимит бюджета (в рублях):", reply_markup=cancel_kb())
    await c.answer()


@dp.message(SetBudget.monthly_limit)
async def set_budget_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Установка бюджета отменена", reply_markup=main_kb())

    try:
        limit = float(message.text.replace(",", "."))
        if limit <= 0:
            await message.answer("❗ Лимит должен быть больше 0")
            return

        if limit > 10_000_000:
            await message.answer("❗ Слишком большой лимит")
            return

        await set_budget(message.from_user.id, limit)
        await state.clear()
        await message.answer(f"✅ Месячный лимит установлен: {limit:,.2f} ₽".replace(",", " "), reply_markup=main_kb())
    except:
        await message.answer("❗ Введите корректное число")


@dp.callback_query(lambda c: c.data == "check_budget")
async def check_budget_status_handler(c: types.CallbackQuery):
    status = await check_budget_status(c.from_user.id)

    if not status:
        await c.message.answer("❌ Бюджет не установлен")
        await c.answer()
        return

    spent = sum(status["spent"].values()) if status["spent"] else 0
    percent = (spent / status["limit"] * 100) if status["limit"] > 0 else 0

    if percent >= 100:
        warning = "🔴 Внимание! Бюджет превышен!"
        emoji = "🔴"
    elif percent >= 90:
        warning = "⚠️ Вы приближаетесь к лимиту бюджета!"
        emoji = "🟡"
    elif percent >= 75:
        warning = "📊 Большая часть бюджета использована"
        emoji = "🟠"
    else:
        warning = "✅ В пределах бюджета"
        emoji = "🟢"

    # Создаём прогресс-бар
    bar_length = 10
    filled = int(percent / 10)
    bar = "█" * filled + "░" * (bar_length - filled)

    text = (
        f"{emoji} Статус бюджета:\n\n"
        f"💰 Лимит: {status['limit']:,.2f} ₽\n"
        f"💸 Потрачено: {spent:,.2f} ₽\n"
        f"✨ Осталось: {status['remaining']:,.2f} ₽\n\n"
        f"📊 Использовано: {percent:.1f}%\n"
        f"[{bar}]\n\n"
        f"{warning}"
    ).replace(",", " ")

    await c.message.answer(text)
    await c.answer()


# ================= ИСТОРИЯ ПЛАТЕЖЕЙ =================

@dp.message(lambda m: m.text == "📜 История платежей")
async def payment_history(message: types.Message):
    history = await get_payment_history(message.from_user.id, limit=20)

    if not history:
        await message.answer("📜 История платежей пуста", reply_markup=main_kb())
        return

    text = "📜 Последние платежи:\n\n"

    for payment in history:
        date = payment["payment_date"].strftime("%d.%m.%Y")

        # Эмодзи для статуса
        status_emoji = {
            "paid": "✅",
            "skipped": "⏭️",
            "pending": "⏳",
            "failed": "❌"
        }.get(payment["status"], "❓")

        text += (
            f"{status_emoji} {payment['subscription_name']}\n"
            f"   💰 {payment['amount']} {payment['currency']}\n"
            f"   📅 {date}\n\n"
        )

    # Если сообщение слишком длинное, разбиваем на части
    if len(text) > 4000:
        parts = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(text)

    # Возвращаем главное меню
    await message.answer("👆 Это история ваших платежей", reply_markup=main_kb())

# ================= NOTIFICATIONS (ОБНОВЛЁННАЯ) =================

async def notification_loop():
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                                        SELECT s.*, u.telegram_id
                                        FROM subscriptions s
                                                 JOIN users u ON u.id = s.user_id
                                        """)

            today = datetime.now().date()

            for r in rows:
                try:
                    delta = (r["next_payment_date"] - today).days
                    pay_date = r["next_payment_date"].strftime("%d.%m.%Y")

                    ideas = "\n".join([f"  • {idea}" for idea in spending_ideas(r["amount"])])

                    text = (
                        f"📌 {r['name']}\n"
                        f"💰 {r['amount']} {r['currency']}\n"
                        f"📅 Списание: {pay_date}\n"
                        f"🔁 Период: {r['period_days']} дней\n\n"
                        f"💡 На что можно потратить эти деньги:\n{ideas}"
                    )

                    # ===== 3 дня =====
                    if delta == 3 and not r["reminded_3d"]:
                        await bot.send_message(r["telegram_id"], "⏳ Через 3 дня спишется:\n\n" + text)

                        await add_notification(r["telegram_id"], r["id"], today, "reminder_3d")

                        async with pool.acquire() as conn2:
                            await conn2.execute("""
                                                UPDATE subscriptions
                                                SET reminded_3d = TRUE
                                                WHERE id = $1
                                                """, r["id"])

                    # ===== 1 день =====
                    if delta == 1 and not r["reminded_1d"]:
                        await bot.send_message(r["telegram_id"], "⏰ Завтра спишется:\n\n" + text)

                        await add_notification(r["telegram_id"], r["id"], today, "reminder_1d")

                        async with pool.acquire() as conn2:
                            await conn2.execute("""
                                                UPDATE subscriptions
                                                SET reminded_1d = TRUE
                                                WHERE id = $1
                                                """, r["id"])

                    # ===== сегодня =====
                    if delta <= 0:
                        await bot.send_message(
                            r["telegram_id"],
                            "💸 Сегодня списание:\n\n" + text,
                            reply_markup=action_kb(r["id"])
                        )
                        await add_payment_record(
                            r["telegram_id"],
                            r["id"],
                            float(r["amount"]),
                            today,
                            "paid"
                        )
                        await add_notification(r["telegram_id"], r["id"], today, "payment_due")

                        new_date = next_payment(r["next_payment_date"], r["period_days"])

                        async with pool.acquire() as conn2:
                            await conn2.execute("""
                                                UPDATE subscriptions
                                                SET next_payment_date=$1,
                                                    reminded_3d= FALSE,
                                                    reminded_1d= FALSE
                                                WHERE id = $2
                                                """, new_date, r["id"])
                except Exception as e:
                    logging.error(f"Error processing subscription {r['id']}: {e}")
                    continue

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Error in notification loop: {e}")
            await asyncio.sleep(60)


# ================= ACTIONS =================


@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        # Получаем название подписки перед удалением
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text(f"❌ Подписка \"{sub['name']}\" удалена")
    await c.answer(f"Подписка {sub['name']} удалена")


@dp.callback_query(lambda c: c.data.startswith("renew_"))
async def renew(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        # Получаем данные подписки
        sub = await conn.fetchrow(
            "SELECT user_id, amount, currency, name FROM subscriptions WHERE id=$1",
            sub_id
        )

        # Обновляем дату следующего платежа

        await conn.execute("""
                           UPDATE subscriptions
                           SET next_payment_date = next_payment_date + period_days * interval '1 day'
                           WHERE id=$1
                           """, sub_id)

        # Добавляем запись о платеже

        if sub:
            await conn.execute("""

                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)

                               VALUES ($1, $2, CURRENT_DATE, 'paid', NOW())

                               """, sub_id, float(sub["amount"]))
    await c.message.edit_text(f"🔁 Подписка \"{sub['name']}\" продлена")
    await c.answer("Подписка продлена")


@dp.callback_query(lambda c: c.data.startswith("skip_"))
async def skip(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        # Получаем данные подписки перед удалением

        sub = await conn.fetchrow(
            "SELECT user_id, amount, currency, name FROM subscriptions WHERE id=$1",
            sub_id
        )

        # Добавляем запись о пропущенном платеже

        if sub:
            await conn.execute("""

                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)

                               VALUES ($1, $2, CURRENT_DATE, 'skipped', NOW())

                               """, sub_id, float(sub["amount"]))
        # Удаляем подписку
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)
    await c.message.edit_text(f"⏭️ Подписка \"{sub['name']}\" завершена")
    await c.answer("Подписка завершена")


# ================= ОБРАБОТЧИК НЕИЗВЕСТНЫХ КОМАНД =================


@dp.message()
async def unknown_message(message: types.Message):
    """Обработчик неизвестных сообщений"""
    await message.answer(
        "❓ Неизвестная команда. Используйте кнопки меню или /start",
        reply_markup=main_kb()

    )


# ================= MAIN =================


async def main():
    await init_db()

    asyncio.create_task(notification_loop())

    print("✅ Бот запущен!")
    print(f"📱 Версия клавиатуры: {KEYBOARD_VERSION}")
    try:
        await dp.start_polling(bot)
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")