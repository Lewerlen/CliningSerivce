from aiogram.fsm.state import State, StatesGroup

class RegistrationStates(StatesGroup):
    waiting_for_phone = State()

class OrderStates(StatesGroup):
    choosing_cleaning_type = State()
    # в будущем здесь будут другие состояния: выбор комнат, санузлов и т.д.