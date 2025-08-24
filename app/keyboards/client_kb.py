from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import calendar
from datetime import datetime
from app.common.texts import STATUS_MAPPING

# --- НОВЫЙ БЛОК: ВСЕ ДЛЯ КАЛЕНДАРЯ ---
RUSSIAN_MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

async def create_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    now = datetime.now()
    month_name = RUSSIAN_MONTHS[month - 1]

    # Кнопки навигации по месяцам
    header_buttons = []
    # Кнопка "назад", если это не текущий месяц
    if not (year == now.year and month == now.month):
        header_buttons.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"month_nav:prev:{year}:{month}")
        )
    header_buttons.append(
        InlineKeyboardButton(text=f"{month_name} {year}", callback_data="ignore")
    )
    # Кнопка "вперед"
    header_buttons.append(
        InlineKeyboardButton(text="➡️", callback_data=f"month_nav:next:{year}:{month}")
    )
    builder.row(*header_buttons)

    # Дни недели
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    builder.row(*[InlineKeyboardButton(text=day, callback_data="ignore") for day in days])

    # Дни месяца
    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row_buttons = []
        for day in week:
            # Проверяем, является ли день прошедшим
            is_past_day = year == now.year and month == now.month and day < now.day
            if day == 0 or is_past_day:
                row_buttons.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                row_buttons.append(
                    InlineKeyboardButton(text=str(day), callback_data=f"day:{year}-{month:02d}-{day:02d}"))
        builder.row(*row_buttons)

    # Кнопка "Назад к адресу"
    builder.row(InlineKeyboardButton(text="⬅️ Назад к адресу", callback_data="back_to_address"))

    return builder.as_markup()

def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню."""
    buttons = [
        [KeyboardButton(text="📦 Заказать уборку")],
        [KeyboardButton(text="💬 Мои заказы")],
        [KeyboardButton(text="📞 Поддержка")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_edit_order_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора действия при редактировании заказа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Изменить дату и время", callback_data="edit_datetime")
    builder.button(text="Изменить доп. услуги", callback_data="edit_services")
    builder.button(text="Изменить адрес", callback_data="edit_address")
    builder.button(text="Изменить кол-во комнат/санузлов", callback_data="edit_rooms")
    builder.button(text="⬅️ Назад к заказам", callback_data="back_to_my_orders")
    builder.adjust(1)
    return builder.as_markup()


def get_active_orders_keyboard(orders: list) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком активных заказов и кнопкой архива."""
    builder = InlineKeyboardBuilder()
    for order in orders:
        text = f"Заказ №{order.id} от {order.created_at.strftime('%d.%m.%Y')} - {order.total_price} ₽"
        builder.button(text=text, callback_data=f"view_order:{order.id}")

    builder.button(text="🗂 Архив заказов", callback_data="view_archive")
    builder.adjust(1)  # Все кнопки в один столбец
    return builder.as_markup()

def get_view_order_keyboard(order_id: int, can_be_edited: bool) -> InlineKeyboardMarkup:
    """Создает клавиатуру для просмотра активного заказа."""
    builder = InlineKeyboardBuilder()
    if can_be_edited:
        builder.button(text="✏️ Изменить заказ", callback_data=f"edit_order:{order_id}")
    builder.button(text="❌ Отменить заказ", callback_data=f"cancel_order:{order_id}")
    builder.button(text="⬅️ Назад к заказам", callback_data="back_to_orders_list")
    builder.adjust(1)
    return builder.as_markup()


def get_archive_orders_keyboard(orders: list) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком архивных заказов."""
    builder = InlineKeyboardBuilder()
    for order in orders:
        status_text = STATUS_MAPPING.get(order.status, order.status.value)
        text = f"Заказ №{order.id} от {order.created_at.strftime('%d.%m.%Y')} - {status_text}"
        builder.button(text=text, callback_data=f"view_archive_order:{order.id}")

    builder.button(text="⬅️ Назад к активным заказам", callback_data="back_to_orders_list")
    builder.adjust(1)
    return builder.as_markup()

def get_view_archive_order_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для просмотра архивного заказа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Заказать снова", callback_data=f"repeat_order:{order_id}")
    builder.button(text="⬅️ Назад к архиву", callback_data="view_archive")
    builder.adjust(1)
    return builder.as_markup()

def get_cleaning_type_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора типа уборки."""
    buttons = [
        [KeyboardButton(text="🧽 Поддерживающая")],
        [KeyboardButton(text="🧼 Генеральная")],
        [KeyboardButton(text="🛠 После ремонта")],
        [KeyboardButton(text="⬅️ Назад в меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_room_count_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора количества комнат."""
    buttons = [
        [
            KeyboardButton(text="1"),
            KeyboardButton(text="2"),
            KeyboardButton(text="3"),
            KeyboardButton(text="4"),
            KeyboardButton(text="5+"),
        ],
        [KeyboardButton(text="⬅️ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_bathroom_count_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора количества санузлов."""
    buttons = [
        [
            KeyboardButton(text="1"),
            KeyboardButton(text="2"),
            KeyboardButton(text="3+"),
        ],
        [KeyboardButton(text="⬅️ Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# Словарь с дополнительными услугами и их ценами из ТЗ
ADDITIONAL_SERVICES = {
    "win": "🪞 Мойка окон (+300 ₽/шт)",
    "sofa": "🛋 Химчистка дивана (+1500 ₽)",
    "chair": "🪑 Химчистка стульев (+300 ₽/шт)",
    "plumbing": "🚿 Чистка сантехники (+500 ₽)",
    "bedding": "🛏 Замена постельного белья (+200 ₽)",
    "kitchen": "🧴 Мытье кухонной техники (+600 ₽)",
    "cabinets": "🧼 Чистка шкафчиков внутри (+500₽)",
    "balcony": "🧯 Уборка балкона (+700 ₽)",
    "carpet": "🧹 Чистка ковров (+800 ₽)",
    "pets": "🐾 Удаление шерсти животных (+400 ₽)",
    "fridge": "❄ Мойка холодильника (+700 ₽)",
    "stove": "🍳 Мойка плиты (+500 ₽)",
    "oven": "🔥 Мойка духовки (+700 ₽)",
}


def get_additional_services_keyboard(selected_services: set = None) -> InlineKeyboardMarkup:
    """
    Возвращает inline-клавиатуру для выбора доп. услуг.
    Отмечает галочкой уже выбранные услуги.
    """
    if selected_services is None:
        selected_services = set()

    builder = InlineKeyboardBuilder()  # Используем билдер для удобства
    for key, text in ADDITIONAL_SERVICES.items():
        if key in selected_services:
            button_text = f"✅ {text}"
        else:
            button_text = text
        builder.button(text=button_text, callback_data=f"add_service_{key}")

    # Выстраиваем кнопки услуг в один столбец
    builder.adjust(1)

    # Добавляем кнопки "Назад" и "Готово" в один ряд
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_bathrooms"),
        InlineKeyboardButton(text="✅ Готово", callback_data="done_services")
    )

    return builder.as_markup()

def get_address_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для шага ввода адреса."""
    buttons = [
        [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        [KeyboardButton(text="⬅️ Назад к доп. услугам")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_date_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора даты."""
    buttons = [
        [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
        [KeyboardButton(text="⬅️ Назад к адресу")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_time_keyboard(available_slots: list) -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора времени из доступных слотов."""
    builder = ReplyKeyboardBuilder()
    for slot in available_slots:
        builder.add(KeyboardButton(text=slot))
    # Размещаем кнопки по 2 в ряд
    builder.adjust(2)
    # Кнопку "Назад" добавляем на отдельный ряд
    builder.row(KeyboardButton(text="⬅️ Назад к выбору даты"))
    return builder.as_markup(resize_keyboard=True)

def get_photo_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для шага загрузки фото."""
    buttons = [
        [KeyboardButton(text="➡️ Пропустить")],
        [KeyboardButton(text="⬅️ Назад к выбору времени")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_order_name_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для шага ввода имени."""
    buttons = [
        [KeyboardButton(text="⬅️ Назад к фото")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_order_phone_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для шага ввода телефона."""
    buttons = [
        [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
        [KeyboardButton(text="⬅️ Назад к имени")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_confirmation_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для подтверждения заказа."""
    buttons = [
        [KeyboardButton(text="✅ Все верно, подтвердить")],
        [KeyboardButton(text="⬅️ Отменить и вернуться в меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_payment_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для выбора способа оплаты."""
    buttons = [
        [KeyboardButton(text="💳 Онлайн-оплата")],
        [KeyboardButton(text="💵 Наличными исполнителю")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

def get_address_confirmation_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру для подтверждения адреса."""
    buttons = [
        [KeyboardButton(text="✅ Да, все верно")],
        [KeyboardButton(text="✏️ Ввести вручную")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)