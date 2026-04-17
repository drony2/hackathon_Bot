from aiogram.fsm.state import StatesGroup, State

class AddSub(StatesGroup):
    name = State()
    amount = State()
    currency = State()
    period = State()
    date = State()