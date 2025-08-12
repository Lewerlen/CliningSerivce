from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_contact_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой запроса контакта."""
    contact_button = KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)
    return ReplyKeyboardMarkup(keyboard=[[contact_button]], resize_keyboard=True)