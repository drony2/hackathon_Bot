import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from bot.database.db_init import get_pool
from bot.database.repositories import SubscriptionRepository, NotificationRepository
from bot.services.utils import spending_ideas
from bot.keyboards.inline_kb import action_kb
from bot.config import BOT_TOKEN

bot = Bot(token=BOT_TOKEN)

async def notification_loop():
    while True:
        try:
            pool = await get_pool()
            sub_repo = SubscriptionRepository(pool)
            notif_repo = NotificationRepository(pool)
            
            rows = await sub_repo.get_active_with_notifications()
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
                        await notif_repo.add(r["user_id"], r["id"], today, "reminder_3d")
                        await sub_repo.update_reminders(r["id"], reminded_3d=True)
                        print(f"✅ 3 дня: {r['name']}")
                    
                    # 1 день
                    if delta == 1 and not r["reminded_1d"]:
                        await bot.send_message(r["telegram_id"], "⏰ Завтра спишется:\n\n" + text)
                        await notif_repo.add(r["user_id"], r["id"], today, "reminder_1d")
                        await sub_repo.update_reminders(r["id"], reminded_1d=True)
                        print(f"✅ 1 день: {r['name']}")
                    
                    # Сегодня
                    if delta == 0 and not r.get("reminded_today", False):
                        await bot.send_message(
                            r["telegram_id"],
                            "💸 Сегодня списание:\n\n" + text,
                            reply_markup=action_kb(r["id"])
                        )
                        await notif_repo.add(r["user_id"], r["id"], today, "payment_due")
                        await sub_repo.update_reminders(r["id"], reminded_today=True)
                        print(f"✅ Сегодня: {r['name']}")
                
                except Exception as e:
                    logging.error(f"Error processing subscription {r['id']}: {e}")
                    continue
            
            await asyncio.sleep(600)
        
        except Exception as e:
            logging.error(f"Error in notification loop: {e}")
            await asyncio.sleep(600)