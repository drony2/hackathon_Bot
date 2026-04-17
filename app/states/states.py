from aiogram.fsm.state import StatesGroup, State

class AddSub(StatesGroup):
    name = State()
    amount = State()
    currency = State()
    period = State()
    date = State()

class SetBudget(StatesGroup):
    currency = State()
    monthly_limit = State()

class EditSub(StatesGroup):
    sub_id = State()
    field = State()
    new_value = State()

class ResumeSub(StatesGroup):
    waiting_for_date = State()