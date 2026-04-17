import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BOT_TOKEN, KEYBOARD_VERSION
from bot.database.db_init import init_db, get_pool
from bot.services.notifications import notification_loop
from bot.middlewares import RateLimitMiddleware
from bot.handlers import (
    start_router, add_router, list_router, stats_router,
    budget_router, history_router, edit_router, actions_router, common_router
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Создание бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Подключение middleware (только rate_limit)
dp.message.middleware(RateLimitMiddleware(default_max_actions=10, default_window=60))
dp.callback_query.middleware(RateLimitMiddleware(default_max_actions=20, default_window=60))

# Подключение всех роутеров
dp.include_router(start_router)
dp.include_router(add_router)
dp.include_router(list_router)
dp.include_router(stats_router)
dp.include_router(budget_router)
dp.include_router(history_router)
dp.include_router(edit_router)
dp.include_router(actions_router)
dp.include_router(common_router)

async def main():
    # Инициализация базы данных
    await init_db()
    
    # Запуск цикла уведомлений
    asyncio.create_task(notification_loop())
    
    print("✅ Бот запущен!")
    print(f"📱 Версия клавиатуры: {KEYBOARD_VERSION}")
    
    try:
        await dp.start_polling(bot)
    finally:
        pool = await get_pool()
        await pool.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")