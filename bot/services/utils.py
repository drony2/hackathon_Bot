from datetime import datetime, timedelta
from collections import defaultdict

user_actions = defaultdict(list)

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

def spending_ideas(amount):
    if amount <= 300:
        return ["☕ кофе", "🍫 перекус", "📱 подписка"]
    elif amount <= 1000:
        return ["🍔 еда", "☕ кофе", "🎬 кино"]
    elif amount <= 3000:
        return ["🍕 доставка", "🎮 игры", "📺 сервисы"]
    else:
        return ["✈️ поездка", "🎮 покупки", "🍽 еда"]

def next_payment(date, days):
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d").date()
    return date + timedelta(days=days)