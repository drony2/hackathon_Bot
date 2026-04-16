import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from collections import defaultdict

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

KEYBOARD_VERSION = "4.0"
MAX_SUBSCRIPTIONS = 50

# Хранилище для rate limiting
user_actions = defaultdict(list)


# ================= DB INIT =================

async def init_db():
    global pool
    pool = await asyncpg.create_pool(**DB_CONFIG)

    async with pool.acquire() as conn:
        # Таблица настроек пользователя
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

        # Обновляем таблицу budgets для поддержки валют
        try:
            await conn.execute("""
                               ALTER TABLE budgets
                                   ADD COLUMN IF NOT EXISTS currency VARCHAR (10) DEFAULT 'RUB'
                               """)
        except Exception as e:
            logging.warning(f"Could not add currency column: {e}")

        # Добавляем колонку reminded_today
        try:
            await conn.execute("""
                               ALTER TABLE subscriptions
                                   ADD COLUMN IF NOT EXISTS reminded_today BOOLEAN DEFAULT FALSE
                               """)
        except Exception as e:
            logging.warning(f"Could not add reminded_today column: {e}")

        # Создаём уникальный индекс на user_id + name
        try:
            await conn.execute("""
                               CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_user_name
                                   ON subscriptions (user_id, LOWER (name))
                               """)
        except Exception as e:
            logging.warning(f"Could not create unique index: {e}")


# ================= FSM =================

class AddSub(StatesGroup):
    name = State()
    amount = State()
    currency = State()
    period = State()
    date = State()


class SetBudget(StatesGroup):
    currency = State()
    monthly_limit = State()


class EditSub(StatesGroup):
    sub_id = State()
    field = State()
    new_value = State()


class ResumeSub(StatesGroup):
    waiting_for_date = State()


# ================= UTILS =================

SUPPORTED_CURRENCIES = {
    "RUB": {"symbol": "₽", "name": "Рубль"},
    "USD": {"symbol": "$", "name": "Доллар"},
    "EUR": {"symbol": "€", "name": "Евро"}
}


def parse_date(text):
    formats = ["%Y-%m-%d", "%d.%m.%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except:
            pass
    return None


def next_payment(date, days):
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d").date()
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


def validate_subscription_name(name: str) -> tuple:
    """Проверяет корректность названия подписки"""
    name = name.strip()

    if not name:
        return False, "❗ Название не может быть пустым"

    if name.isspace():
        return False, "❗ Название не может состоять только из пробелов"

    if not re.search(r'[a-zA-Zа-яА-Я0-9]', name):
        return False, "❗ Название должно содержать хотя бы одну букву или цифру"

    invalid_start_chars = ['.', ',', '!', '?', '-', '_', '=', '+', '*', '/', '\\', '|', '@', '#', '$', '%', '^', '&',
                           '(', ')', '[', ']', '{', '}', '<', '>', '~', '`', '"', "'", ';', ':']
    if name[0] in invalid_start_chars:
        return False, f"❗ Название не может начинаться с символа '{name[0]}'"

    if len(name) < 2:
        return False, "❗ Название должно содержать минимум 2 символа"

    if len(name) > 100:
        return False, "❗ Название слишком длинное (макс. 100 символов)"

    allowed_pattern = r'^[a-zA-Zа-яА-Я0-9\s\.\-_&()+!@#$%^*,;:]+$'
    if not re.match(allowed_pattern, name):
        return False, "❗ Название содержит недопустимые символы"

    if re.search(r'[^\w\s]{5,}', name):
        return False, "❗ Слишком много специальных символов подряд"

    words = name.lower().split()
    for word in words:
        if len(word) > 1 and name.lower().count(word) > 3:
            return False, f"❗ Слово '{word}' повторяется слишком много раз"

    return True, ""


def validate_amount(text: str) -> tuple:
    """Проверяет корректность суммы"""
    text = text.strip().replace(",", ".")

    if not text:
        return False, "❗ Сумма не может быть пустой", 0

    if any(c.isalpha() for c in text):
        return False, "❗ Сумма не должна содержать буквы", 0

    if text.count('.') > 1:
        return False, "❗ Неверный формат числа (слишком много точек)", 0

    try:
        amount = float(text)

        if amount < 0.01:
            return False, "❗ Минимальная сумма: 0.01", 0

        if amount > 1_000_000:
            return False, "❗ Максимальная сумма: 1 000 000", 0

        if '.' in text:
            decimal_places = len(text.split('.')[1])
            if decimal_places > 2:
                return False, "❗ Максимум 2 знака после запятой", 0

        return True, "", round(amount, 2)

    except ValueError:
        return False, "❗ Введите корректное число", 0


def validate_period(text: str) -> tuple:
    """Проверяет корректность периода"""
    text = text.strip()

    if not text:
        return False, "❗ Период не может быть пустым", 0

    if not text.isdigit():
        return False, "❗ Период должен быть целым числом", 0

    try:
        days = int(text)

        if days < 1:
            return False, "❗ Период должен быть минимум 1 день", 0

        if days > 365:
            return False, "❗ Период не может быть больше 365 дней", 0

        return True, "", days

    except ValueError:
        return False, "❗ Некорректное число", 0


def rate_limit(user_id: int, action: str, max_actions: int = 10, window: int = 60) -> bool:
    """Проверяет, не превысил ли пользователь лимит действий"""
    key = f"{user_id}:{action}"
    now = datetime.now()

    user_actions[key] = [t for t in user_actions[key] if now - t < timedelta(seconds=window)]

    if len(user_actions[key]) >= max_actions:
        return True

    user_actions[key].append(now)
    return False


def auto_correct_name(name: str) -> str:
    """Автоматически исправляет частые ошибки в названиях"""
    corrections = {
        "netflix": "Netflix",
        "spotify": "Spotify",
        "youtube": "YouTube",
        "яндекс": "Яндекс",
        "гугл": "Google",
        "вк": "VK",
    }

    name_lower = name.lower()
    if name_lower in corrections:
        return corrections[name_lower]

    words = name.split()
    if words:
        words[0] = words[0].capitalize()

    return " ".join(words)


# ================= ФУНКЦИИ КЛАВИАТУРЫ =================

async def check_and_update_keyboard(user_tg_id: int, message: types.Message = None):
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


# ================= DB =================

async def add_user(tg_id, username, first_name):
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
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1",
            tg_id
        )

        await conn.execute("""
                           INSERT INTO subscriptions
                           (user_id, name, amount, currency, next_payment_date, period_days,
                            reminded_3d, reminded_1d, reminded_today, status)
                           VALUES ($1, $2, $3, $4, $5, $6, FALSE, FALSE, FALSE, 'active')
                           """,
                           user["id"],
                           data["name"],
                           data["amount"],
                           data["currency"],
                           data["date"],
                           data["period"]
                           )


async def check_subscription_exists(tg_id, name):
    """Проверяет, существует ли у пользователя подписка с таким названием"""
    clean_name = " ".join(name.split())

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
    """Проверяет, не превышен ли лимит подписок"""
    async with pool.acquire() as conn:
        count = await conn.fetchval("""
                                    SELECT COUNT(*)
                                    FROM subscriptions s
                                             JOIN users u ON u.id = s.user_id
                                    WHERE u.telegram_id = $1
                                    """, tg_id)
        return count >= MAX_SUBSCRIPTIONS


# ================= PAYMENTS =================

async def add_payment_record(tg_id, subscription_id, amount, payment_date, status="paid"):
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


async def get_payment_history(tg_id, limit=20):
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


async def get_monthly_spending(tg_id, currency=None, year=None, month=None):
    if year is None or month is None:
        today = datetime.now()
        year = today.year
        month = today.month

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


# ================= BUDGETS =================

async def set_budget(tg_id, currency, monthly_limit):
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


# ================= NOTIFICATIONS =================

async def add_notification(tg_id, subscription_id, notify_date, notify_type):
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


# ================= UI =================

def main_kb():
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
                InlineKeyboardButton(text="$ USD", callback_data="cur_USD"),
                InlineKeyboardButton(text="€ EUR", callback_data="cur_EUR")
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
        ]
    )


def budget_currency_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="₽ RUB", callback_data="budget_cur_RUB"),
                InlineKeyboardButton(text="$ USD", callback_data="budget_cur_USD"),
                InlineKeyboardButton(text="€ EUR", callback_data="budget_cur_EUR")
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
        ]
    )


def action_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Продлить", callback_data=f"renew_{sub_id}"),
            InlineKeyboardButton(text="⏸ Приостановить", callback_data=f"pause_{sub_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{sub_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{sub_id}")
        ]
    ])


def list_action_kb(sub_id, status="active"):
    if status == "active":
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Продлить", callback_data=f"renew_{sub_id}"),
                InlineKeyboardButton(text="⏸ Приостановить", callback_data=f"pause_{sub_id}")
            ],
            [
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{sub_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{sub_id}")
            ]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"resume_{sub_id}")
            ],
            [
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{sub_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{sub_id}")
            ]
        ])


def edit_fields_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Название", callback_data=f"editfield_{sub_id}_name")],
        [InlineKeyboardButton(text="💰 Сумма", callback_data=f"editfield_{sub_id}_amount")],
        [InlineKeyboardButton(text="💱 Валюта", callback_data=f"editfield_{sub_id}_currency")],
        [InlineKeyboardButton(text="📅 Период (дней)", callback_data=f"editfield_{sub_id}_period")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_sub_{sub_id}")]
    ])


def budget_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Установить лимит", callback_data="set_budget")],
        [InlineKeyboardButton(text="📊 Проверить статус", callback_data="check_budget")],
        [InlineKeyboardButton(text="📋 Все лимиты", callback_data="list_budgets")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]
    ])


def confirm_delete_kb(sub_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirmdel_{sub_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"back_to_sub_{sub_id}")
        ]
    ])


# ================= ОТМЕНА =================

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
    await add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await force_update_keyboard(message.from_user.id)

    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"💳 Я бот для управления подписками.\n"
        f"📊 Я помогу тебе отслеживать все твои подписки и не пропустить оплату.\n\n"
        f"✨ Возможности:\n"
        f"• Добавление подписок\n"
        f"• Напоминания об оплате\n"
        f"• Статистика расходов\n"
        f"• Установка бюджета по валютам\n"
        f"• История платежей\n"
        f"• Редактирование подписок\n\n"
        f"👇 Выбери действие в меню:"
    )

    await message.answer(welcome_text, reply_markup=main_kb())


@dp.message(Command("menu"))
async def show_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("📋 Главное меню:", reply_markup=main_kb())


@dp.message(Command("update"))
async def force_update(message: types.Message):
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
    # Проверка rate limit
    if rate_limit(message.from_user.id, "add_sub", max_actions=10, window=60):
        await message.answer("⚠️ Слишком много попыток! Подождите минуту.")
        return

    # Проверка лимита подписок
    if await check_subscription_limit(message.from_user.id):
        await message.answer(
            f"⚠️ Достигнут лимит подписок ({MAX_SUBSCRIPTIONS})!\n"
            f"Удалите ненужные подписки чтобы добавить новые.",
            reply_markup=main_kb()
        )
        return

    await state.set_state(AddSub.name)

    # Показываем существующие подписки
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT name
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY name
                                """, message.from_user.id)

    if rows:
        existing = "\n".join([f"• {r['name']}" for r in rows])
        text = (
            f"📝 Введите название подписки:\n\n"
            f"📋 Уже есть:\n{existing}\n\n"
            f"❌ Отмена - для отмены"
        )
    else:
        text = "📝 Введите название подписки:"

    await message.answer(text, reply_markup=cancel_kb())


@dp.message(AddSub.name)
async def name(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    # Валидация названия
    is_valid, error_message = validate_subscription_name(m.text)
    if not is_valid:
        return await m.answer(error_message)

    # Автокоррекция и очистка
    clean_name = auto_correct_name(m.text)
    clean_name = " ".join(clean_name.split())

    # Проверка уникальности
    exists = await check_subscription_exists(m.from_user.id, clean_name)
    if exists:
        return await m.answer(
            f"❌ У вас уже есть подписка с названием \"{clean_name}\"!\n"
            f"📝 Пожалуйста, введите другое название:"
        )

    await state.update_data(name=clean_name)
    await state.set_state(AddSub.amount)
    await m.answer(
        f"✅ Название: {clean_name}\n\n"
        f"💰 Введите сумму (например: 199.99 или 199):",
        reply_markup=cancel_kb()
    )


@dp.message(AddSub.amount)
async def amount(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Добавление отменено", reply_markup=main_kb())

    is_valid, error_message, amount_value = validate_amount(message.text)
    if not is_valid:
        return await message.answer(error_message)

    await state.update_data(amount=amount_value)
    await state.set_state(AddSub.currency)
    await message.answer("💱 Выберите валюту:", reply_markup=currency_kb())


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

    is_valid, error_message, days = validate_period(m.text)
    if not is_valid:
        return await m.answer(error_message)

    await state.update_data(period=days)
    await state.set_state(AddSub.date)

    default_date = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
    await m.answer(
        f"📅 Введите дату следующего платежа (YYYY-MM-DD или DD.MM.YYYY):\n"
        f"💡 Например: {default_date}",
        reply_markup=cancel_kb()
    )


@dp.message(AddSub.date)
async def date(m: types.Message, state: FSMContext):
    if m.text == "❌ Отмена":
        await state.clear()
        return await m.answer("❌ Добавление отменено", reply_markup=main_kb())

    d = parse_date(m.text)
    if not d:
        return await m.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или DD.MM.YYYY")

    today = datetime.now().date()
    if d < today:
        return await m.answer(
            f"❌ Нельзя указать прошедшую дату!\n"
            f"📅 Сегодня: {today.strftime('%d.%m.%Y')}\n"
            f"📅 Вы ввели: {d.strftime('%d.%m.%Y')}\n\n"
            f"Пожалуйста, введите будущую дату:"
        )

    max_date = today + timedelta(days=365 * 5)
    if d > max_date:
        return await m.answer(
            f"❌ Дата слишком далеко!\n"
            f"📅 Максимум: {max_date.strftime('%d.%m.%Y')}"
        )

    data = await state.get_data()
    data["date"] = d

    await add_subscription(m.from_user.id, data)
    await state.clear()

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
                                SELECT s.id, s.name, s.amount, s.currency, s.next_payment_date, s.period_days, s.status
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                ORDER BY CASE WHEN s.status = 'active' THEN 0 ELSE 1 END,
                                         s.next_payment_date
                                """, m.from_user.id)

    if not rows:
        await m.answer("📭 У вас пока нет подписок", reply_markup=main_kb())
        return

    active_subs = [r for r in rows if r["status"] == "active"]
    paused_subs = [r for r in rows if r["status"] != "active"]

    if active_subs:
        await m.answer("🟢 **АКТИВНЫЕ ПОДПИСКИ:**")
        for r in active_subs:
            date = r["next_payment_date"].strftime("%d.%m.%Y")
            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Следующий платёж: {date}\n"
                f"🔁 Период: {r['period_days']} дней\n"
                f"🟢 Статус: Активна"
            )
            await m.answer(text, reply_markup=list_action_kb(r['id'], r['status']))

    if paused_subs:
        await m.answer("🔴 **ПРИОСТАНОВЛЕННЫЕ ПОДПИСКИ:**")
        for r in paused_subs:
            date = r["next_payment_date"].strftime("%d.%m.%Y")
            text = (
                f"📌 {r['name']}\n"
                f"💰 {r['amount']} {r['currency']}\n"
                f"📅 Платёж был: {date}\n"
                f"🔁 Период: {r['period_days']} дней\n"
                f"🔴 Статус: Приостановлена"
            )
            await m.answer(text, reply_markup=list_action_kb(r['id'], r['status']))

    await m.answer("👆 Это все ваши подписки", reply_markup=main_kb())


# ================= РЕДАКТИРОВАНИЕ ПОДПИСКИ =================

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_subscription(c: types.CallbackQuery, state: FSMContext):
    sub_id = int(c.data.split("_")[1])
    await state.update_data(edit_sub_id=sub_id)
    await c.message.edit_text(
        "✏️ Выберите поле для редактирования:",
        reply_markup=edit_fields_kb(sub_id)
    )
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("back_to_sub_"))
async def back_to_sub(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[3])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
                                  SELECT name, amount, currency, next_payment_date, period_days, status
                                  FROM subscriptions
                                  WHERE id = $1
                                  """, sub_id)

    if sub:
        date = sub["next_payment_date"].strftime("%d.%m.%Y")
        status_text = "Активна" if sub["status"] == "active" else "Приостановлена"
        status_emoji = "🟢" if sub["status"] == "active" else "🔴"

        text = (
            f"📌 {sub['name']}\n"
            f"💰 {sub['amount']} {sub['currency']}\n"
            f"📅 Следующий платёж: {date}\n"
            f"🔁 Период: {sub['period_days']} дней\n"
            f"{status_emoji} Статус: {status_text}"
        )
        await c.message.edit_text(text, reply_markup=list_action_kb(sub_id, sub['status']))
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("editfield_"))
async def edit_field(c: types.CallbackQuery, state: FSMContext):
    parts = c.data.split("_")
    sub_id = int(parts[1])
    field = parts[2]

    await state.update_data(edit_sub_id=sub_id, edit_field=field)
    await state.set_state(EditSub.new_value)

    field_names = {
        "name": "название",
        "amount": "сумму",
        "currency": "валюту (RUB/USD/EUR)",
        "period": "период в днях"
    }

    await c.message.delete()
    await c.message.answer(
        f"✏️ Введите новое {field_names[field]}:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@dp.message(EditSub.new_value)
async def save_edited_field(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Редактирование отменено", reply_markup=main_kb())

    data = await state.get_data()
    sub_id = data["edit_sub_id"]
    field = data["edit_field"]
    new_value = message.text

    async with pool.acquire() as conn:
        if field == "name":
            # Валидация названия
            is_valid, error_message = validate_subscription_name(new_value)
            if not is_valid:
                return await message.answer(error_message)

            clean_name = auto_correct_name(new_value)
            clean_name = " ".join(clean_name.split())

            # Проверка уникальности
            user_id = await conn.fetchval("""
                                          SELECT u.telegram_id
                                          FROM users u
                                                   JOIN subscriptions s ON s.user_id = u.id
                                          WHERE s.id = $1
                                          """, sub_id)

            exists = await conn.fetchval("""
                                         SELECT EXISTS(SELECT 1
                                                       FROM subscriptions s
                                                                JOIN users u ON u.id = s.user_id
                                                       WHERE u.telegram_id = $1
                                                         AND LOWER(s.name) = LOWER($2)
                                                         AND s.id != $3)
                                         """, user_id, clean_name, sub_id)

            if exists:
                await state.clear()
                return await message.answer(
                    f"❌ У вас уже есть подписка с названием \"{clean_name}\"!\n"
                    f"📝 Редактирование отменено.",
                    reply_markup=main_kb()
                )

            await conn.execute("UPDATE subscriptions SET name = $1 WHERE id = $2", clean_name, sub_id)

        elif field == "amount":
            is_valid, error_message, amount_value = validate_amount(new_value)
            if not is_valid:
                return await message.answer(error_message)
            await conn.execute("UPDATE subscriptions SET amount = $1 WHERE id = $2", amount_value, sub_id)

        elif field == "currency":
            new_value = new_value.upper()
            if new_value not in SUPPORTED_CURRENCIES:
                return await message.answer(f"❗ Валюта должна быть: {', '.join(SUPPORTED_CURRENCIES.keys())}")
            await conn.execute("UPDATE subscriptions SET currency = $1 WHERE id = $2", new_value, sub_id)

        elif field == "period":
            is_valid, error_message, days = validate_period(new_value)
            if not is_valid:
                return await message.answer(error_message)
            await conn.execute("UPDATE subscriptions SET period_days = $1 WHERE id = $2", days, sub_id)

    await state.clear()

    # Показываем обновлённую подписку
    async with pool.acquire() as conn:
        sub = await conn.fetchrow("""
                                  SELECT name, amount, currency, next_payment_date, period_days, status
                                  FROM subscriptions
                                  WHERE id = $1
                                  """, sub_id)

    date = sub["next_payment_date"].strftime("%d.%m.%Y")
    status_text = "Активна" if sub["status"] == "active" else "Приостановлена"
    status_emoji = "🟢" if sub["status"] == "active" else "🔴"

    text = (
        f"✅ Подписка обновлена!\n\n"
        f"📌 {sub['name']}\n"
        f"💰 {sub['amount']} {sub['currency']}\n"
        f"📅 Следующий платёж: {date}\n"
        f"🔁 Период: {sub['period_days']} дней\n"
        f"{status_emoji} Статус: {status_text}"
    )

    await message.answer(text, reply_markup=main_kb())


# ================= STATS =================

@dp.message(lambda m: m.text == "📊 Статистика")
async def stats(message: types.Message, state: FSMContext):
    if await state.get_state():
        await message.answer("⚠️ Сначала завершите текущее действие", reply_markup=main_kb())
        return

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
                                SELECT name, amount, currency, period_days, status
                                FROM subscriptions s
                                         JOIN users u ON u.id = s.user_id
                                WHERE u.telegram_id = $1
                                """, message.from_user.id)

    if not rows:
        await message.answer("❌ Нет подписок для статистики", reply_markup=main_kb())
        return

    active_rows = [r for r in rows if r["status"] == "active"]
    paused_rows = [r for r in rows if r["status"] != "active"]

    text = "📊 Статистика подписок:\n\n"

    if active_rows:
        text += "🟢 **АКТИВНЫЕ:**\n"
        by_currency = {}
        for r in active_rows:
            cur = r["currency"]
            if cur not in by_currency:
                by_currency[cur] = {"total": 0, "items": [], "monthly": 0, "yearly": 0}
            by_currency[cur]["total"] += r["amount"]
            by_currency[cur]["items"].append(
                f"• {r['name']} — {r['amount']} {cur} (каждые {r['period_days']} дн.)"
            )
            monthly_cost = (r["amount"] * 30) / r["period_days"]
            yearly_cost = (r["amount"] * 365) / r["period_days"]
            by_currency[cur]["monthly"] += monthly_cost
            by_currency[cur]["yearly"] += yearly_cost

        for cur, data in by_currency.items():
            text += f"\n💱 {cur}:\n"
            text += "\n".join(data["items"])
            text += f"\n📊 Итоги: {round(data['total'], 2)} {cur}"
            text += f"\n📅 В месяц: ~{round(data['monthly'], 2)} {cur}"
            text += f"\n📆 В год: ~{round(data['yearly'], 2)} {cur}\n"

    if paused_rows:
        text += "\n🔴 **ПРИОСТАНОВЛЕННЫЕ:**\n"
        paused_total = {}
        for r in paused_rows:
            cur = r["currency"]
            paused_total[cur] = paused_total.get(cur, 0) + r["amount"]
            text += f"• {r['name']} — {r['amount']} {cur}\n"

        for cur, total in paused_total.items():
            text += f"💸 Заморожено в {cur}: {round(total, 2)} {cur}\n"

    await message.answer(text, reply_markup=main_kb())


# ================= БЮДЖЕТ =================

@dp.message(lambda m: m.text == "💰 Бюджет")
async def budget_menu(message: types.Message):
    budgets = await get_budget(message.from_user.id)

    if budgets:
        text = "💰 Ваши лимиты:\n\n"
        for currency, limit in budgets.items():
            status = await check_budget_status(message.from_user.id, currency)
            spent = status["spent"] if status else 0
            remaining = status["remaining"] if status else limit
            percent = (spent / limit * 100) if limit > 0 else 0
            text += (
                f"💱 {currency}:\n"
                f"   📊 Лимит: {limit:,.2f}\n"
                f"   💸 Потрачено: {spent:,.2f}\n"
                f"   ✨ Осталось: {remaining:,.2f}\n"
                f"   📈 Использовано: {percent:.1f}%\n\n"
            ).replace(",", " ")
    else:
        text = "💰 Бюджеты не установлены"

    await message.answer(text, reply_markup=budget_kb())


@dp.callback_query(lambda c: c.data == "set_budget")
async def set_budget_start(c: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetBudget.currency)
    await c.message.delete()
    await c.message.answer("💱 Выберите валюту для лимита:", reply_markup=budget_currency_kb())
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("budget_cur_"))
async def budget_currency_selected(c: types.CallbackQuery, state: FSMContext):
    currency = c.data.split("_")[2]
    await state.update_data(budget_currency=currency)
    await state.set_state(SetBudget.monthly_limit)
    await c.message.delete()
    await c.message.answer(
        f"💰 Введите месячный лимит бюджета в {currency}:",
        reply_markup=cancel_kb()
    )
    await c.answer()


@dp.message(SetBudget.monthly_limit)
async def set_budget_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Установка бюджета отменена", reply_markup=main_kb())

    is_valid, error_message, limit = validate_amount(message.text)
    if not is_valid:
        return await message.answer(error_message)

    data = await state.get_data()
    currency = data["budget_currency"]

    await set_budget(message.from_user.id, currency, limit)
    await state.clear()
    await message.answer(
        f"✅ Месячный лимит установлен: {limit:,.2f} {currency}".replace(",", " "),
        reply_markup=main_kb()
    )


@dp.callback_query(lambda c: c.data == "check_budget")
async def check_budget_status_start(c: types.CallbackQuery):
    budgets = await get_budget(c.from_user.id)

    if not budgets:
        await c.message.answer("❌ Бюджеты не установлены")
        await c.answer()
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
                                                  [InlineKeyboardButton(text=f"💱 {cur}",
                                                                        callback_data=f"checkbudget_{cur}")]
                                                  for cur in budgets.keys()
                                              ] + [[InlineKeyboardButton(text="❌ Отмена",
                                                                         callback_data="cancel_action")]])

    await c.message.edit_text("💱 Выберите валюту для проверки:", reply_markup=kb)
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("checkbudget_"))
async def check_budget_status_handler(c: types.CallbackQuery):
    currency = c.data.split("_")[1]
    status = await check_budget_status(c.from_user.id, currency)

    if not status:
        await c.message.edit_text(f"❌ Бюджет для {currency} не установлен")
        await c.answer()
        return

    spent = status["spent"]
    limit = status["limit"]
    percent = (spent / limit * 100) if limit > 0 else 0

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

    bar_length = 10
    filled = min(int(percent / 10), 10)
    bar = "█" * filled + "░" * (bar_length - filled)

    text = (
        f"{emoji} Статус бюджета ({currency}):\n\n"
        f"💰 Лимит: {limit:,.2f} {currency}\n"
        f"💸 Потрачено: {spent:,.2f} {currency}\n"
        f"✨ Осталось: {status['remaining']:,.2f} {currency}\n\n"
        f"📊 Использовано: {percent:.1f}%\n"
        f"[{bar}]\n\n"
        f"{warning}"
    ).replace(",", " ")

    await c.message.edit_text(text)
    await c.answer()


@dp.callback_query(lambda c: c.data == "list_budgets")
async def list_budgets(c: types.CallbackQuery):
    budgets = await get_budget(c.from_user.id)

    if not budgets:
        await c.message.edit_text("❌ Бюджеты не установлены")
        await c.answer()
        return

    text = "📋 Все установленные лимиты:\n\n"
    for currency, limit in budgets.items():
        status = await check_budget_status(c.from_user.id, currency)
        spent = status["spent"] if status else 0
        remaining = status["remaining"] if status else limit
        percent = (spent / limit * 100) if limit > 0 else 0

        text += (
            f"💱 {currency}:\n"
            f"   📊 Лимит: {limit:,.2f}\n"
            f"   💸 Потрачено: {spent:,.2f}\n"
            f"   ✨ Осталось: {remaining:,.2f}\n"
            f"   📈 Использовано: {percent:.1f}%\n\n"
        ).replace(",", " ")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_budget")]
    ])

    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@dp.callback_query(lambda c: c.data == "back_to_budget")
async def back_to_budget(c: types.CallbackQuery):
    budgets = await get_budget(c.from_user.id)

    if budgets:
        text = "💰 Ваши лимиты:\n\n"
        for currency, limit in budgets.items():
            status = await check_budget_status(c.from_user.id, currency)
            spent = status["spent"] if status else 0
            remaining = status["remaining"] if status else limit
            text += (
                f"💱 {currency}:\n"
                f"   📊 Лимит: {limit:,.2f}\n"
                f"   💸 Потрачено: {spent:,.2f}\n"
                f"   ✨ Осталось: {remaining:,.2f}\n\n"
            ).replace(",", " ")
    else:
        text = "💰 Бюджеты не установлены"

    await c.message.edit_text(text, reply_markup=budget_kb())
    await c.answer()


# ================= ИСТОРИЯ ПЛАТЕЖЕЙ =================

@dp.message(lambda m: m.text == "📜 История платежей")
async def payment_history(message: types.Message):
    history = await get_payment_history(message.from_user.id, limit=30)

    if not history:
        await message.answer("📜 История платежей пуста", reply_markup=main_kb())
        return

    text = "📜 Последние платежи:\n\n"

    seen_payments = set()
    for payment in history:
        payment_key = f"{payment['subscription_id']}_{payment['payment_date']}_{payment['amount']}"

        if payment_key in seen_payments:
            continue
        seen_payments.add(payment_key)

        date = payment["payment_date"].strftime("%d.%m.%Y")

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

    if len(text) > 4000:
        parts = [text[i:i + 4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(text)

    await message.answer("👆 Это история ваших платежей", reply_markup=main_kb())


# ================= NOTIFICATIONS =================

async def notification_loop():
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                                        SELECT s.*, u.telegram_id
                                        FROM subscriptions s
                                                 JOIN users u ON u.id = s.user_id
                                        WHERE s.status = 'active'
                                        """)

            today = datetime.now().date()
            print(f"\n🔍 Проверка уведомлений. Сегодня: {today}")

            for r in rows:
                try:
                    delta = (r["next_payment_date"] - today).days
                    pay_date = r["next_payment_date"].strftime("%d.%m.%Y")

                    ideas = "\n".join([f"  • {idea}" for idea in spending_ideas(float(r["amount"]))])

                    text = (
                        f"📌 {r['name']}\n"
                        f"💰 {r['amount']} {r['currency']}\n"
                        f"📅 Списание: {pay_date}\n"
                        f"🔁 Период: {r['period_days']} дней\n"
                        f"🟢 Статус: Активна\n\n"
                        f"💡 На что можно потратить эти деньги:\n{ideas}"
                    )

                    # 3 дня
                    if delta == 3 and not r["reminded_3d"]:
                        await bot.send_message(r["telegram_id"], "⏳ Через 3 дня спишется:\n\n" + text)
                        await add_notification(r["telegram_id"], r["id"], today, "reminder_3d")
                        async with pool.acquire() as conn2:
                            await conn2.execute("UPDATE subscriptions SET reminded_3d = TRUE WHERE id = $1", r["id"])
                        print(f"✅ 3 дня: {r['name']}")

                    # 1 день
                    if delta == 1 and not r["reminded_1d"]:
                        await bot.send_message(r["telegram_id"], "⏰ Завтра спишется:\n\n" + text)
                        await add_notification(r["telegram_id"], r["id"], today, "reminder_1d")
                        async with pool.acquire() as conn2:
                            await conn2.execute("UPDATE subscriptions SET reminded_1d = TRUE WHERE id = $1", r["id"])
                        print(f"✅ 1 день: {r['name']}")

                    # Сегодня
                    if delta == 0:
                        reminded_today = r.get("reminded_today", False)
                        if not reminded_today:
                            await bot.send_message(
                                r["telegram_id"],
                                "💸 Сегодня списание:\n\n" + text,
                                reply_markup=action_kb(r["id"])
                            )
                            await add_notification(r["telegram_id"], r["id"], today, "payment_due")
                            async with pool.acquire() as conn2:
                                await conn2.execute("UPDATE subscriptions SET reminded_today = TRUE WHERE id = $1",
                                                    r["id"])
                            print(f"✅ Сегодня: {r['name']}")

                except Exception as e:
                    logging.error(f"Error processing subscription {r['id']}: {e}")
                    continue

            await asyncio.sleep(600)

        except Exception as e:
            logging.error(f"Error in notification loop: {e}")
            await asyncio.sleep(600)


# ================= ACTIONS =================

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def delete_confirm(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить подписку \"{sub['name']}\"?",
        reply_markup=confirm_delete_kb(sub_id)
    )
    await c.answer()


@dp.callback_query(lambda c: c.data.startswith("confirmdel_"))
async def delete_confirmed(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)
        await conn.execute("DELETE FROM subscriptions WHERE id=$1", sub_id)

    await c.message.edit_text(f"🗑 Подписка \"{sub['name']}\" удалена")
    await c.answer("Подписка удалена")


@dp.callback_query(lambda c: c.data.startswith("renew_"))
async def renew(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, amount, currency, period_days, next_payment_date FROM subscriptions WHERE id=$1",
            sub_id
        )

        if sub:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, CURRENT_DATE, 'paid', NOW())
                               """, sub_id, float(sub["amount"]))

            new_date = sub["next_payment_date"] + timedelta(days=sub["period_days"])

            await conn.execute("""
                               UPDATE subscriptions
                               SET next_payment_date = $1,
                                   reminded_3d       = FALSE,
                                   reminded_1d       = FALSE,
                                   reminded_today    = FALSE,
                                   status            = 'active'
                               WHERE id = $2
                               """, new_date, sub_id)

            await c.message.edit_text(
                f"✅ Подписка \"{sub['name']}\" продлена!\n"
                f"📅 Следующее списание: {new_date.strftime('%d.%m.%Y')}"
            )
            await c.answer("Подписка продлена")


@dp.callback_query(lambda c: c.data.startswith("pause_"))
async def pause_subscription(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)

        if sub:
            await conn.execute("""
                               UPDATE subscriptions
                               SET status         = 'paused',
                                   reminded_3d    = FALSE,
                                   reminded_1d    = FALSE,
                                   reminded_today = FALSE
                               WHERE id = $1
                               """, sub_id)

            await c.message.edit_text(
                f"⏸ Подписка \"{sub['name']}\" приостановлена!\n"
                f"🔴 Уведомления отключены."
            )
            await c.answer("Подписка приостановлена")


@dp.callback_query(lambda c: c.data.startswith("resume_"))
async def resume_subscription_start(c: types.CallbackQuery, state: FSMContext):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, period_days FROM subscriptions WHERE id=$1",
            sub_id
        )

    if sub:
        await state.update_data(resume_sub_id=sub_id, resume_period=sub["period_days"])
        await state.set_state(ResumeSub.waiting_for_date)

        default_date = (datetime.now() + timedelta(days=sub["period_days"])).strftime("%d.%m.%Y")

        await c.message.delete()
        await c.message.answer(
            f"▶️ Возобновление подписки \"{sub['name']}\"\n\n"
            f"📅 Введите дату следующего платежа (YYYY-MM-DD или DD.MM.YYYY):\n"
            f"💡 Например: {default_date}",
            reply_markup=cancel_kb()
        )
    else:
        await c.answer("Подписка не найдена")

    await c.answer()


@dp.message(ResumeSub.waiting_for_date)
async def resume_subscription_finish(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Возобновление отменено", reply_markup=main_kb())

    d = parse_date(message.text)
    if not d:
        return await message.answer("❌ Неверный формат даты. Используйте YYYY-MM-DD или DD.MM.YYYY")

    today = datetime.now().date()
    if d < today:
        return await message.answer(
            f"❌ Нельзя указать прошедшую дату!\n"
            f"📅 Сегодня: {today.strftime('%d.%m.%Y')}\n"
            f"Пожалуйста, введите будущую дату:"
        )

    data = await state.get_data()
    sub_id = data["resume_sub_id"]

    async with pool.acquire() as conn:
        sub = await conn.fetchrow("SELECT name FROM subscriptions WHERE id=$1", sub_id)

        if sub:
            await conn.execute("""
                               UPDATE subscriptions
                               SET status            = 'active',
                                   next_payment_date = $1,
                                   reminded_3d       = FALSE,
                                   reminded_1d       = FALSE,
                                   reminded_today    = FALSE
                               WHERE id = $2
                               """, d, sub_id)

            await message.answer(
                f"▶️ Подписка \"{sub['name']}\" возобновлена!\n"
                f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
                f"🟢 Уведомления включены.",
                reply_markup=main_kb()
            )

    await state.clear()


@dp.callback_query(lambda c: c.data.startswith("skip_"))
async def skip(c: types.CallbackQuery):
    sub_id = int(c.data.split("_")[1])

    async with pool.acquire() as conn:
        sub = await conn.fetchrow(
            "SELECT name, amount, currency, period_days, next_payment_date FROM subscriptions WHERE id=$1",
            sub_id
        )

        if sub:
            await conn.execute("""
                               INSERT INTO payments (subscription_id, amount, payment_date, status, created_at)
                               VALUES ($1, $2, CURRENT_DATE, 'skipped', NOW())
                               """, sub_id, float(sub["amount"]))

            new_date = sub["next_payment_date"] + timedelta(days=sub["period_days"])

            await conn.execute("""
                               UPDATE subscriptions
                               SET next_payment_date = $1,
                                   reminded_3d       = FALSE,
                                   reminded_1d       = FALSE,
                                   reminded_today    = FALSE
                               WHERE id = $2
                               """, new_date, sub_id)

            await c.message.edit_text(
                f"⏭️ Платёж по подписке \"{sub['name']}\" пропущен!\n"
                f"📅 Следующее списание: {new_date.strftime('%d.%m.%Y')}"
            )
            await c.answer("Платёж пропущен")


# ================= ОБРАБОТЧИК НЕИЗВЕСТНЫХ КОМАНД =================

@dp.message()
async def unknown_message(message: types.Message):
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