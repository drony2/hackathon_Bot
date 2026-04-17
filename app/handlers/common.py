from aiogram import types
from aiogram.fsm.context import FSMContext
from app.bot import dp
from app.keyboards.keyboards import main_kb


@dp.message(lambda m: m.text == "❌ Отмена")
async def cancel_message(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer("❌ Действие отменено", reply_markup=main_kb())
    else:
        await message.answer("👋 Нет активных действий для отмены", reply_markup=main_kb())

@dp.message()
async def unknown_message(message: types.Message):
    await message.answer(
        "❓ Неизвестная команда. Используйте кнопки меню или /start",
        reply_markup=main_kb()
    )

@dp.callback_query(lambda c: c.data == "cancel_action")
async def cancel_inline(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.delete()
    await c.message.answer("❌ Действие отменено", reply_markup=main_kb())
    await c.answer()
