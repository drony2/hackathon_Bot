from aiogram import Router, F
from aiogram.types import Message

from bot.keyboards.main_kb import main_kb
from bot.keyboards.inline_kb import list_action_kb
from bot.database.repositories import SubscriptionRepository
from bot.database.db_init import get_pool

router = Router()

@router.message(F.text == "📋 Список")
async def list_subs(m: Message):
    pool = await get_pool()
    sub_repo = SubscriptionRepository(pool)
    
    rows = await sub_repo.get_all(m.from_user.id)
    
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