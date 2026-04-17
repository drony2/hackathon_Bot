from app.bot import dp
from aiogram import types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.db.queries import force_update_keyboard, add_user
from app.keyboards.keyboards import main_kb


@dp.message(Command("menu"))
async def show_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("📋 Главное меню:", reply_markup=main_kb())


@dp.message(Command("update"))
async def force_update(message: types.Message):
    await force_update_keyboard(message.from_user.id)
    await message.answer("✅ Клавиатура обновлена!", reply_markup=main_kb())

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await force_update_keyboard(message.from_user.id)

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
