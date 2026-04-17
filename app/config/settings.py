import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден!")

KEYBOARD_VERSION = "4.0"
MAX_SUBSCRIPTIONS = 50

