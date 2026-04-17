from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.states import EditSub
from bot.keyboards.main_kb import main_kb, cancel_kb
from bot.keyboards.inline_kb import edit_fields_kb, list_action_kb
from bot.services.validators import validate_subscription_name, validate_amount, validate_period
from bot.services.utils import auto_correct_name
from bot.database.repositories import SubscriptionRepository
from bot.database.db_init import get_pool
from bot.constants import SUPPORTED_CURRENCIES

router = Router()

@router.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_subscription(c: CallbackQuery, state: FSMContext):
    sub_id = int(c.data.split("_")[1])
    await state.update_data(edit_sub_id=sub_id)
    await c.message.edit_text(
        "✏️ Выберите поле для редактирования:",
        reply_markup=edit_fields_kb(sub_id)
    )
    await c.answer()

@router.callback_query(lambda c: c.data.startswith("back_to_sub_"))
async def back_to_sub(c: CallbackQuery):
    sub_id = int(c.data.split("_")[3])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    sub = await sub_repo.get_by_id(sub_id)
    
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

@router.callback_query(lambda c: c.data.startswith("editfield_"))
async def edit_field(c: CallbackQuery, state: FSMContext):
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

@router.message(EditSub.new_value)
async def save_edited_field(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Редактирование отменено", reply_markup=main_kb())
    
    data = await state.get_data()
    sub_id = data["edit_sub_id"]
    field = data["edit_field"]
    new_value = message.text
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    
    if field == "name":
        # Валидация названия
        is_valid, error_message = validate_subscription_name(new_value)
        if not is_valid:
            return await message.answer(error_message)
        
        clean_name = auto_correct_name(new_value)
        clean_name = " ".join(clean_name.split())
        
        # Проверка уникальности
        exists = await sub_repo.check_exists(message.from_user.id, clean_name, exclude_id=sub_id)
        
        if exists:
            await state.clear()
            return await message.answer(
                f"❌ У вас уже есть подписка с названием \"{clean_name}\"!\n"
                f"📝 Редактирование отменено.",
                reply_markup=main_kb()
            )
        
        await sub_repo.update_field(sub_id, "name", clean_name)
    
    elif field == "amount":
        is_valid, error_message, amount_value = validate_amount(new_value)
        if not is_valid:
            return await message.answer(error_message)
        await sub_repo.update_field(sub_id, "amount", amount_value)
    
    elif field == "currency":
        new_value = new_value.upper()
        if new_value not in SUPPORTED_CURRENCIES:
            return await message.answer(f"❗ Валюта должна быть: {', '.join(SUPPORTED_CURRENCIES.keys())}")
        await sub_repo.update_field(sub_id, "currency", new_value)
    
    elif field == "period":
        is_valid, error_message, days = validate_period(new_value)
        if not is_valid:
            return await message.answer(error_message)
        await sub_repo.update_field(sub_id, "period_days", days)
    
    await state.clear()
    
    # Показываем обновлённую подписку
    sub = await sub_repo.get_by_id(sub_id)
    
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