from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def get_contact_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой запроса контакта."""
    contact_button = KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)
    return ReplyKeyboardMarkup(keyboard=[[contact_button]], resize_keyboard=True)

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню."""
    buttons = [
        [KeyboardButton(text="📦 Заказать уборку")],
        [KeyboardButton(text="💬 Мои заказы")],
        [KeyboardButton(text="📞 Поддержка")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cleaning_type_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора типа уборки."""
    buttons = [
        [KeyboardButton(text="🧽 Поддерживающая")],
        [KeyboardButton(text="🧼 Генеральная")],
        [KeyboardButton(text="🛠 После ремонта")],
        [KeyboardButton(text="⬅️ Назад в меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)