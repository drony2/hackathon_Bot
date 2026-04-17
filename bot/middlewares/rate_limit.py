import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Tuple, Callable, Awaitable, Any

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, Update
from aiogram.dispatcher.flags import get_flag

# Хранилище для rate limiting
user_actions: Dict[str, list] = defaultdict(list)


class RateLimitMiddleware(BaseMiddleware):
    """Middleware для ограничения частоты запросов"""
    
    def __init__(self, default_max_actions: int = 10, default_window: int = 60):
        super().__init__()
        self.default_max_actions = default_max_actions
        self.default_window = default_window
    
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        # Получаем user_id из события
        user_id = self._get_user_id(event)
        
        if user_id is None:
            return await handler(event, data)
        
        # Получаем флаги для rate limit
        max_actions = get_flag(data, "rate_limit_max_actions", self.default_max_actions)
        window = get_flag(data, "rate_limit_window", self.default_window)
        action = get_flag(data, "rate_limit_action", "default")
        
        # Проверяем лимит
        if self._is_rate_limited(user_id, action, max_actions, window):
            await self._send_rate_limit_message(event)
            return
        
        # Выполняем обработчик
        return await handler(event, data)
    
    def _get_user_id(self, event: Update) -> int | None:
        """Извлекает user_id из события"""
        if event.message and event.message.from_user:
            return event.message.from_user.id
        elif event.callback_query and event.callback_query.from_user:
            return event.callback_query.from_user.id
        return None
    
    def _is_rate_limited(self, user_id: int, action: str, max_actions: int, window: int) -> bool:
        """Проверяет, не превышен ли лимит"""
        key = f"{user_id}:{action}"
        now = datetime.now()
        
        # Очищаем старые записи
        user_actions[key] = [
            t for t in user_actions[key] 
            if now - t < timedelta(seconds=window)
        ]
        
        if len(user_actions[key]) >= max_actions:
            return True
        
        user_actions[key].append(now)
        return False
    
    async def _send_rate_limit_message(self, event: Update):
        """Отправляет сообщение о превышении лимита"""
        text = "⚠️ Слишком много запросов! Пожалуйста, подождите немного."
        
        if event.message:
            await event.message.answer(text)
        elif event.callback_query:
            await event.callback_query.message.answer(text)
            await event.callback_query.answer()