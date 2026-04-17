from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot.keyboards.main_kb import main_kb
from bot.database.repositories import UserRepository
from bot.database.db_init import get_pool
from bot.constants import KEYBOARD_VERSION

router = Router()

@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    
    pool = await get_pool()
    user_repo = UserRepository(pool)
    
    await user_repo.add_user(
        message.from_user.id, 
        message.from_user.username, 
        message.from_user.first_name
    )
    
    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"💳 Я бот для управления подписками.\n"
        f"📊 Я помогу тебе отслеживать все твои подписки и не пропустить оплату.\n\n"
        f"✨ Возможности:\n"
        f"• Добавление подписок\n"
        f"• Напоминания об оплате\n"
        f"• Статистика расходов\n"
        f"• Установка бюджета по валютам\n"
        f"• История платежей\n"
        f"• Редактирование подписок\n\n"
        f"👇 Выбери действие в меню:"
    )
    
    await message.answer(welcome_text, reply_markup=main_kb())

@router.message(Command("menu"))
async def show_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("📋 Главное меню:", reply_markup=main_kb())