import os
from dotenv import load_dotenv

load_dotenv()

# Bot settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables")

KEYBOARD_VERSION = "4.0"
MAX_SUBSCRIPTIONS = 50

# Database settings
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "hackathon_bot"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "")
}

# Supported currencies
SUPPORTED_CURRENCIES = {
    "RUB": {"symbol": "₽", "name": "Рубль"},
    "USD": {"symbol": "$", "name": "Доллар"},
    "EUR": {"symbol": "€", "name": "Евро"}
}