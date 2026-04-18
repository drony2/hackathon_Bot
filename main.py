import asyncio

from app.bot import bot, dp
from app.db.connection import init_db
import app.db.connection as db
from app.services.notifications import notification_loop


async def main():
    await init_db()
    asyncio.create_task(notification_loop())

    try:
        await dp.start_polling(bot)
    finally:
        await db.pool.close()


if __name__ == "__main__":
    asyncio.run(main())