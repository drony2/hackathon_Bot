from aiogram.fsm.state import StatesGroup, State

class ResumeSub(StatesGroup):
    waiting_for_date = State()