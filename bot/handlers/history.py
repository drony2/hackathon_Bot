from aiogram import Router, F
from aiogram.types import Message

from bot.keyboards.main_kb import main_kb
from bot.database.repositories import PaymentRepository
from bot.database.db_init import get_pool

router = Router()

@router.message(F.text == "📜 История платежей")
async def payment_history(message: Message):
    pool = await get_pool()
    payment_repo = PaymentRepository(pool)
    
    history = await payment_repo.get_history(message.from_user.id, limit=30)
    
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