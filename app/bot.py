from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from app.config.settings import BOT_TOKEN

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

import app.handlers.start
import app.handlers.subscriptions
import app.handlers.stats
import app.handlers.budget
import app.handlers.payments
import app.handlers.common

print("✅ Хендлеры импортированы!")