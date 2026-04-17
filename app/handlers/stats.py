
from aiogram import types

from aiogram.fsm.context import FSMContext
from app.db.connection import pool
from app.bot import dp
from app.keyboards.keyboards import main_kb


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

