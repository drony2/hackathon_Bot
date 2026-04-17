from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.states import SetBudget
from bot.keyboards.main_kb import main_kb, cancel_kb
from bot.keyboards.inline_kb import budget_currency_kb, budget_kb
from bot.services.validators import validate_amount
from bot.services.budget_service import check_budget_status
from bot.database.repositories import BudgetRepository
from bot.database.db_init import get_pool

router = Router()

@router.message(F.text == "💰 Бюджет")
async def budget_menu(message: Message):
    pool = await get_pool()
    budget_repo = BudgetRepository(pool)
    
    budgets = await budget_repo.get_budget(message.from_user.id)
    
    if budgets:
        text = "💰 Ваши лимиты:\n\n"
        for currency, limit in budgets.items():
            status = await check_budget_status(pool, message.from_user.id, currency)
            spent = status["spent"] if status else 0
            remaining = status["remaining"] if status else limit
            percent = (spent / limit * 100) if limit > 0 else 0
            text += (
                f"💱 {currency}:\n"
                f"   📊 Лимит: {limit:,.2f}\n"
                f"   💸 Потрачено: {spent:,.2f}\n"
                f"   ✨ Осталось: {remaining:,.2f}\n"
                f"   📈 Использовано: {percent:.1f}%\n\n"
            ).replace(",", " ")
    else:
        text = "💰 Бюджеты не установлены"
    
    await message.answer(text, reply_markup=budget_kb())

@router.callback_query(lambda c: c.data == "set_budget")
async def set_budget_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(SetBudget.currency)
    await c.message.delete()
    await c.message.answer("💱 Выберите валюту для лимита:", reply_markup=budget_currency_kb())
    await c.answer()

@router.callback_query(lambda c: c.data.startswith("budget_cur_"))
async def budget_currency_selected(c: CallbackQuery, state: FSMContext):
    currency = c.data.split("_")[2]
    await state.update_data(budget_currency=currency)
    await state.set_state(SetBudget.monthly_limit)
    await c.message.delete()
    await c.message.answer(
        f"💰 Введите месячный лимит бюджета в {currency}:",
        reply_markup=cancel_kb()
    )
    await c.answer()

@router.message(SetBudget.monthly_limit)
async def set_budget_finish(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        return await message.answer("❌ Установка бюджета отменена", reply_markup=main_kb())
    
    is_valid, error_message, limit = validate_amount(message.text)
    if not is_valid:
        return await message.answer(error_message)
    
    data = await state.get_data()
    currency = data["budget_currency"]
    
    pool = await get_pool()
    budget_repo = BudgetRepository(pool)
    
    await budget_repo.set_budget(message.from_user.id, currency, limit)
    await state.clear()
    await message.answer(
        f"✅ Месячный лимит установлен: {limit:,.2f} {currency}".replace(",", " "),
        reply_markup=main_kb()
    )

@router.callback_query(lambda c: c.data == "check_budget")
async def check_budget_status_start(c: CallbackQuery):
    pool = await get_pool()
    budget_repo = BudgetRepository(pool)
    
    budgets = await budget_repo.get_budget(c.from_user.id)
    
    if not budgets:
        await c.message.answer("❌ Бюджеты не установлены")
        await c.answer()
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💱 {cur}", callback_data=f"checkbudget_{cur}")]
        for cur in budgets.keys()
    ] + [[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]])
    
    await c.message.edit_text("💱 Выберите валюту для проверки:", reply_markup=kb)
    await c.answer()

@router.callback_query(lambda c: c.data.startswith("checkbudget_"))
async def check_budget_status_handler(c: CallbackQuery):
    currency = c.data.split("_")[1]
    pool = await get_pool()
    status = await check_budget_status(pool, c.from_user.id, currency)
    
    if not status:
        await c.message.edit_text(f"❌ Бюджет для {currency} не установлен")
        await c.answer()
        return
    
    spent = status["spent"]
    limit = status["limit"]
    percent = (spent / limit * 100) if limit > 0 else 0
    
    if percent >= 100:
        warning = "🔴 Внимание! Бюджет превышен!"
        emoji = "🔴"
    elif percent >= 90:
        warning = "⚠️ Вы приближаетесь к лимиту бюджета!"
        emoji = "🟡"
    elif percent >= 75:
        warning = "📊 Большая часть бюджета использована"
        emoji = "🟠"
    else:
        warning = "✅ В пределах бюджета"
        emoji = "🟢"
    
    bar_length = 10
    filled = min(int(percent / 10), 10)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    text = (
        f"{emoji} Статус бюджета ({currency}):\n\n"
        f"💰 Лимит: {limit:,.2f} {currency}\n"
        f"💸 Потрачено: {spent:,.2f} {currency}\n"
        f"✨ Осталось: {status['remaining']:,.2f} {currency}\n\n"
        f"📊 Использовано: {percent:.1f}%\n"
        f"[{bar}]\n\n"
        f"{warning}"
    ).replace(",", " ")
    
    await c.message.edit_text(text)
    await c.answer()

@router.callback_query(lambda c: c.data == "list_budgets")
async def list_budgets(c: CallbackQuery):
    pool = await get_pool()
    budget_repo = BudgetRepository(pool)
    
    budgets = await budget_repo.get_budget(c.from_user.id)
    
    if not budgets:
        await c.message.edit_text("❌ Бюджеты не установлены")
        await c.answer()
        return
    
    text = "📋 Все установленные лимиты:\n\n"
    for currency, limit in budgets.items():
        status = await check_budget_status(pool, c.from_user.id, currency)
        spent = status["spent"] if status else 0
        remaining = status["remaining"] if status else limit
        percent = (spent / limit * 100) if limit > 0 else 0
        
        text += (
            f"💱 {currency}:\n"
            f"   📊 Лимит: {limit:,.2f}\n"
            f"   💸 Потрачено: {spent:,.2f}\n"
            f"   ✨ Осталось: {remaining:,.2f}\n"
            f"   📈 Использовано: {percent:.1f}%\n\n"
        ).replace(",", " ")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_budget")]
    ])
    
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()

@router.callback_query(lambda c: c.data == "back_to_budget")
async def back_to_budget(c: CallbackQuery):
    pool = await get_pool()
    budget_repo = BudgetRepository(pool)
    
    budgets = await budget_repo.get_budget(c.from_user.id)
    
    if budgets:
        text = "💰 Ваши лимиты:\n\n"
        for currency, limit in budgets.items():
            status = await check_budget_status(pool, c.from_user.id, currency)
            spent = status["spent"] if status else 0
            remaining = status["remaining"] if status else limit
            text += (
                f"💱 {currency}:\n"
                f"   📊 Лимит: {limit:,.2f}\n"
                f"   💸 Потрачено: {spent:,.2f}\n"
                f"   ✨ Осталось: {remaining:,.2f}\n\n"
            ).replace(",", " ")
    else:
        text = "💰 Бюджеты не установлены"
    
    await c.message.edit_text(text, reply_markup=budget_kb())
    await c.answer()