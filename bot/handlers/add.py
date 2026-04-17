from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.states import AddSub
from bot.keyboards.main_kb import main_kb, cancel_kb
from bot.keyboards.inline_kb import currency_kb
from bot.services.validators import validate_subscription_name, validate_amount, validate_period, parse_date
from bot.services.utils import rate_limit, auto_correct_name
from bot.database.repositories import UserRepository, SubscriptionRepository
from bot.database.db_init import get_pool
from bot.constants import MAX_SUBSCRIPTIONS

router = Router()

@router.message(F.text == "➕ Добавить")
async def add_subscription_start(message: Message, state: FSMContext):
    # Проверка rate limit
    if rate_limit(message.from_user.id, "add_sub", max_actions=10, window=60):
        await message.answer("⚠️ Слишком много попыток! Подождите минуту.")
        return
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    
    # Проверка лимита подписок
    count = await sub_repo.get_count(message.from_user.id)
    if count >= MAX_SUBSCRIPTIONS:
        await message.answer(
            f"⚠️ Достигнут лимит подписок ({MAX_SUBSCRIPTIONS})!\n"
            f"Удалите ненужные подписки чтобы добавить новые.",
            reply_markup=main_kb()
        )
        return
    
    await state.set_state(AddSub.name)
    
    # Показываем существующие подписки
    rows = await sub_repo.get_all(message.from_user.id)
    
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

@router.message(AddSub.name)
async def process_name(m: Message, state: FSMContext):
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
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    exists = await sub_repo.check_exists(m.from_user.id, clean_name)
    
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

@router.message(AddSub.amount)
async def process_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Добавление отменено", reply_markup=main_kb())
    
    is_valid, error_message, amount_value = validate_amount(message.text)
    if not is_valid:
        return await message.answer(error_message)
    
    await state.update_data(amount=amount_value)
    await state.set_state(AddSub.currency)
    await message.answer("💱 Выберите валюту:", reply_markup=currency_kb())

@router.callback_query(lambda c: c.data.startswith("cur_"))
async def process_currency(c: CallbackQuery, state: FSMContext):
    await state.update_data(currency=c.data.split("_")[1])
    await state.set_state(AddSub.period)
    await c.message.delete()
    await c.message.answer("📅 Введите период (количество дней):", reply_markup=cancel_kb())
    await c.answer()

@router.message(AddSub.period)
async def process_period(m: Message, state: FSMContext):
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

@router.message(AddSub.date)
async def process_date(m: Message, state: FSMContext):
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
    
    pool = await get_pool()
    user_repo = UserRepository(pool)
    sub_repo = SubscriptionRepository(pool)
    
    user_id = await user_repo.get_user_id(m.from_user.id)
    await sub_repo.add(user_id, data)
    await state.clear()
    
    details = (
        f"✅ Подписка добавлена!\n\n"
        f"📌 Название: {data['name']}\n"
        f"💰 Сумма: {data['amount']} {data['currency']}\n"
        f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
        f"🔁 Период: {data['period']} дней"
    )
    
    await m.answer(details, reply_markup=main_kb())