from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.keyboards.main_kb import main_kb

router = Router()

@router.message(F.text == "❌ Отмена")
async def cancel_message(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer("❌ Действие отменено", reply_markup=main_kb())
    else:
        await message.answer("👋 Нет активных действий для отмены", reply_markup=main_kb())

@router.callback_query(lambda c: c.data == "cancel_action")
async def cancel_inline(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.delete()
    await c.message.answer("❌ Действие отменено", reply_markup=main_kb())
    await c.answer()

@router.message()
async def unknown_message(message: Message):
    await message.answer(
        "❓ Неизвестная команда. Используйте кнопки меню или /start",
        reply_markup=main_kb()
    )