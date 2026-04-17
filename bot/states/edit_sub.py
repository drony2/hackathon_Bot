from aiogram.fsm.state import StatesGroup, State

class EditSub(StatesGroup):
    sub_id = State()
    field = State()
    new_value = State()