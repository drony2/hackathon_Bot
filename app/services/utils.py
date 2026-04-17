from datetime import datetime, timedelta
import re
from collections import defaultdict

def parse_date(text):
    formats = ["%Y-%m-%d", "%d.%m.%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except:
            pass
    return None
def validate_amount(text: str) -> tuple:
    """Проверяет корректность суммы"""
    text = text.strip().replace(",", ".")

    if not text:
        return False, "❗ Сумма не может быть пустой", 0

    if any(c.isalpha() for c in text):
        return False, "❗ Сумма не должна содержать буквы", 0

    if text.count('.') > 1:
        return False, "❗ Неверный формат числа (слишком много точек)", 0

    try:
        amount = float(text)

        if amount < 0.01:
            return False, "❗ Минимальная сумма: 0.01", 0

        if amount > 1_000_000:
            return False, "❗ Максимальная сумма: 1 000 000", 0

        if '.' in text:
            decimal_places = len(text.split('.')[1])
            if decimal_places > 2:
                return False, "❗ Максимум 2 знака после запятой", 0

        return True, "", round(amount, 2)

    except ValueError:
        return False, "❗ Введите корректное число", 0


def validate_period(text: str) -> tuple:
    """Проверяет корректность периода"""
    text = text.strip()

    if not text:
        return False, "❗ Период не может быть пустым", 0

    if not text.isdigit():
        return False, "❗ Период должен быть целым числом", 0

    try:
        days = int(text)

        if days < 1:
            return False, "❗ Период должен быть минимум 1 день", 0

        if days > 365:
            return False, "❗ Период не может быть больше 365 дней", 0

        return True, "", days

    except ValueError:
        return False, "❗ Некорректное число", 0


def rate_limit(user_id: int, action: str, max_actions: int = 10, window: int = 60) -> bool:
    """Проверяет, не превысил ли пользователь лимит действий"""
    key = f"{user_id}:{action}"
    now = datetime.now()

    user_actions[key] = [t for t in user_actions[key] if now - t < timedelta(seconds=window)]

    if len(user_actions[key]) >= max_actions:
        return True

    user_actions[key].append(now)
    return False


def auto_correct_name(name: str) -> str:
    """Автоматически исправляет частые ошибки в названиях"""
    corrections = {
        "netflix": "Netflix",
        "spotify": "Spotify",
        "youtube": "YouTube",
        "яндекс": "Яндекс",
        "гугл": "Google",
        "вк": "VK",
    }

    name_lower = name.lower()
    if name_lower in corrections:
        return corrections[name_lower]

    words = name.split()
    if words:
        words[0] = words[0].capitalize()

    return " ".join(words)

def validate_subscription_name(name: str) -> tuple:
    """Проверяет корректность названия подписки"""
    name = name.strip()

    if not name:
        return False, "❗ Название не может быть пустым"

    if name.isspace():
        return False, "❗ Название не может состоять только из пробелов"

    if not re.search(r'[a-zA-Zа-яА-Я0-9]', name):
        return False, "❗ Название должно содержать хотя бы одну букву или цифру"

    invalid_start_chars = ['.', ',', '!', '?', '-', '_', '=', '+', '*', '/', '\\', '|', '@', '#', '$', '%', '^', '&',
                           '(', ')', '[', ']', '{', '}', '<', '>', '~', '`', '"', "'", ';', ':']
    if name[0] in invalid_start_chars:
        return False, f"❗ Название не может начинаться с символа '{name[0]}'"

    if len(name) < 2:
        return False, "❗ Название должно содержать минимум 2 символа"

    if len(name) > 100:
        return False, "❗ Название слишком длинное (макс. 100 символов)"

    allowed_pattern = r'^[a-zA-Zа-яА-Я0-9\s\.\-_&()+!@#$%^*,;:]+$'
    if not re.match(allowed_pattern, name):
        return False, "❗ Название содержит недопустимые символы"

    if re.search(r'[^\w\s]{5,}', name):
        return False, "❗ Слишком много специальных символов подряд"

    words = name.lower().split()
    for word in words:
        if len(word) > 1 and name.lower().count(word) > 3:
            return False, f"❗ Слово '{word}' повторяется слишком много раз"

    return True, ""

def spending_ideas(amount):
    if amount <= 300:
        return ["☕ кофе", "🍫 перекус", "📱 подписка"]
    elif amount <= 1000:
        return ["🍔 еда", "☕ кофе", "🎬 кино"]
    elif amount <= 3000:
        return ["🍕 доставка", "🎮 игры", "📺 сервисы"]
    else:
        return ["✈️ поездка", "🎮 покупки", "🍽 еда"]



user_actions = defaultdict(list)

SUPPORTED_CURRENCIES = {
    "RUB": {"symbol": "₽", "name": "Рубль"},
    "USD": {"symbol": "$", "name": "Доллар"},
    "EUR": {"symbol": "€", "name": "Евро"}
}

def next_payment(date, days):
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d").date()
    return date + timedelta(days=days)