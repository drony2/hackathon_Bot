import asyncio
from datetime import datetime
import logging

import app.db.connection as db

from app.bot import bot
from app.db.queries import add_notification
from app.keyboards.keyboards import action_kb
from app.services.utils import spending_ideas


async def notification_loop():
    while True:
        try:
            if db.pool is None:
                logging.error("❌ Pool is None!")
                await asyncio.sleep(5)
                continue

            async with db.pool.acquire() as conn:
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

                    ideas = "\n".join([
                        f"  • {idea}" for idea in spending_ideas(float(r["amount"]))
                    ])

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

                        async with db.pool.acquire() as conn2:
                            await conn2.execute(
                                "UPDATE subscriptions SET reminded_3d = TRUE WHERE id = $1",
                                r["id"]
                            )

                    # 1 день
                    if delta == 1 and not r["reminded_1d"]:
                        await bot.send_message(r["telegram_id"], "⏰ Завтра спишется:\n\n" + text)
                        await add_notification(r["telegram_id"], r["id"], today, "reminder_1d")

                        async with db.pool.acquire() as conn2:
                            await conn2.execute(
                                "UPDATE subscriptions SET reminded_1d = TRUE WHERE id = $1",
                                r["id"]
                            )

                    # Сегодня
                    if delta == 0:
                        if not r.get("reminded_today", False):
                            await bot.send_message(
                                r["telegram_id"],
                                "💸 Сегодня списание:\n\n" + text,
                                reply_markup=action_kb(r["id"])
                            )

                            await add_notification(r["telegram_id"], r["id"], today, "payment_due")

                            async with db.pool.acquire() as conn2:
                                await conn2.execute(
                                    "UPDATE subscriptions SET reminded_today = TRUE WHERE id = $1",
                                    r["id"]
                                )

                except Exception as e:
                    logging.error(f"Error processing subscription {r['id']}: {e}")

            await asyncio.sleep(60)

        except Exception as e:
            logging.error(f"Error in notification loop: {e}")
            await asyncio.sleep(60)