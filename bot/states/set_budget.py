from aiogram.fsm.state import StatesGroup, State

class SetBudget(StatesGroup):
    currency = State()
    monthly_limit = State()