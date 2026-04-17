from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.states import ResumeSub
from bot.keyboards.main_kb import main_kb, cancel_kb
from bot.keyboards.inline_kb import confirm_delete_kb, action_kb
from bot.services.validators import parse_date
from bot.services.utils import spending_ideas
from bot.database.repositories import SubscriptionRepository, PaymentRepository, NotificationRepository
from bot.database.db_init import get_pool
from bot.config import BOT_TOKEN

router = Router()

@router.callback_query(lambda c: c.data.startswith("del_"))
async def delete_confirm(c: CallbackQuery):
    sub_id = int(c.data.split("_")[1])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    sub = await sub_repo.get_by_id(sub_id)
    
    await c.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить подписку \"{sub['name']}\"?",
        reply_markup=confirm_delete_kb(sub_id)
    )
    await c.answer()

@router.callback_query(lambda c: c.data.startswith("confirmdel_"))
async def delete_confirmed(c: CallbackQuery):
    sub_id = int(c.data.split("_")[1])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    sub = await sub_repo.get_by_id(sub_id)
    
    await sub_repo.delete(sub_id)
    
    await c.message.edit_text(f"🗑 Подписка \"{sub['name']}\" удалена")
    await c.answer("Подписка удалена")

@router.callback_query(lambda c: c.data.startswith("renew_"))
async def renew(c: CallbackQuery):
    sub_id = int(c.data.split("_")[1])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    payment_repo = PaymentRepository(pool)
    
    sub = await sub_repo.get_by_id(sub_id)
    
    if sub:
        # Добавляем запись о платеже
        await payment_repo.add(sub_id, float(sub["amount"]), datetime.now().date(), "paid")
        
        # Обновляем дату следующего платежа
        new_date = sub["next_payment_date"] + timedelta(days=sub["period_days"])
        
        await sub_repo.update_field(sub_id, "next_payment_date", new_date)
        await sub_repo.update_reminders(sub_id, reminded_3d=False, reminded_1d=False, reminded_today=False)
        await sub_repo.update_field(sub_id, "status", "active")
        
        await c.message.edit_text(
            f"✅ Подписка \"{sub['name']}\" продлена!\n"
            f"📅 Следующее списание: {new_date.strftime('%d.%m.%Y')}"
        )
        await c.answer("Подписка продлена")

@router.callback_query(lambda c: c.data.startswith("pause_"))
async def pause_subscription(c: CallbackQuery):
    sub_id = int(c.data.split("_")[1])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    sub = await sub_repo.get_by_id(sub_id)
    
    if sub:
        await sub_repo.update_field(sub_id, "status", "paused")
        await sub_repo.update_reminders(sub_id, reminded_3d=False, reminded_1d=False, reminded_today=False)
        
        await c.message.edit_text(
            f"⏸ Подписка \"{sub['name']}\" приостановлена!\n"
            f"🔴 Уведомления отключены."
        )
        await c.answer("Подписка приостановлена")

@router.callback_query(lambda c: c.data.startswith("resume_"))
async def resume_subscription_start(c: CallbackQuery, state: FSMContext):
    sub_id = int(c.data.split("_")[1])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    sub = await sub_repo.get_by_id(sub_id)
    
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

@router.message(ResumeSub.waiting_for_date)
async def resume_subscription_finish(message: Message, state: FSMContext):
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
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    sub = await sub_repo.get_by_id(sub_id)
    
    if sub:
        await sub_repo.update_field(sub_id, "status", "active")
        await sub_repo.update_field(sub_id, "next_payment_date", d)
        await sub_repo.update_reminders(sub_id, reminded_3d=False, reminded_1d=False, reminded_today=False)
        
        await message.answer(
            f"▶️ Подписка \"{sub['name']}\" возобновлена!\n"
            f"📅 Следующий платёж: {d.strftime('%d.%m.%Y')}\n"
            f"🟢 Уведомления включены.",
            reply_markup=main_kb()
        )
    
    await state.clear()

@router.callback_query(lambda c: c.data.startswith("skip_"))
async def skip_payment(c: CallbackQuery):
    sub_id = int(c.data.split("_")[1])
    
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    payment_repo = PaymentRepository(pool)
    
    sub = await sub_repo.get_by_id(sub_id)
    
    if sub:
        # Добавляем запись о пропущенном платеже
        await payment_repo.add(sub_id, float(sub["amount"]), datetime.now().date(), "skipped")
        
        # Обновляем дату следующего платежа
        new_date = sub["next_payment_date"] + timedelta(days=sub["period_days"])
        
        await sub_repo.update_field(sub_id, "next_payment_date", new_date)
        await sub_repo.update_reminders(sub_id, reminded_3d=False, reminded_1d=False, reminded_today=False)
        
        await c.message.edit_text(
            f"⏭️ Платёж по подписке \"{sub['name']}\" пропущен!\n"
            f"📅 Следующее списание: {new_date.strftime('%d.%m.%Y')}"
        )
        await c.answer("Платёж пропущен")