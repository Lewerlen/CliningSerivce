from aiogram.fsm.state import State, StatesGroup

class OrderStates(StatesGroup):
    choosing_cleaning_type = State()
    choosing_room_count = State()
    choosing_bathroom_count = State()
    choosing_additional_services = State()
    entering_address = State()
    confirming_address = State()
    choosing_date = State()
    choosing_time = State()
    waiting_for_photo = State()
    entering_order_name = State()
    entering_order_phone = State()
    confirming_order = State()
    choosing_payment_method = State()
    editing_order = State()
    editing_additional_services = State()
    editing_address = State()
    editing_room_count = State()
    editing_bathroom_count = State()
    entering_service_quantity = State()

class RatingStates(StatesGroup):
    waiting_for_rating = State()
    waiting_for_review = State()

class SupportStates(StatesGroup):
    creating_ticket_message = State()
    waiting_for_ticket_photo = State()
    replying_to_ticket = State()

class AdminSupportStates(StatesGroup):
    replying_to_ticket = State()

class ExecutorRegistration(StatesGroup):
    waiting_for_phone = State()
    uploading_photo = State()
    waiting_for_completion_confirmation = State()
    editing_schedule = State()

class ChatStates(StatesGroup):
    in_chat = State()