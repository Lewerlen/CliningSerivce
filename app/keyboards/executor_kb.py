from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from app.database.models import OrderStatus, Order
import urllib.parse

def get_executor_main_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню для исполнителя."""
    buttons = [
        [KeyboardButton(text="🆕 Новые заказы"), KeyboardButton(text="📋 Мои заказы")],
        [KeyboardButton(text="🗓️ График работы"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="⭐ Мой рейтинг"), KeyboardButton(text="👥 Реферальная программа")],
        [KeyboardButton(text="🆘 Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_phone_request_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой запроса номера телефона."""
    buttons = [
        [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True)

def get_new_orders_keyboard(orders: list) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком новых заказов."""
    builder = InlineKeyboardBuilder()
    if orders:
        for order in orders:
            text = f"Заказ №{order.id} от {order.created_at.strftime('%d.%m')} - {order.total_price} ₽"
            builder.button(text=text, callback_data=f"executor_view_order:{order.id}")
    builder.adjust(1)
    return builder.as_markup()

def get_order_confirmation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Клавиатура с кнопками 'Принять' / 'Отказаться' для исполнителя."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"executor_accept_order:{order_id}")
    builder.button(text="⛔️ Отказаться", callback_data=f"executor_decline_order:{order_id}")
    return builder.as_markup()


def get_my_orders_keyboard(orders: list) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком принятых заказов."""
    builder = InlineKeyboardBuilder()
    if orders:
        for order in orders:
            if order.status == OrderStatus.in_progress:
                status_icon = "🧼"
            elif order.status == OrderStatus.on_the_way:
                status_icon = "🚗"
            else:  # accepted
                status_icon = "✅"

            text = f"{status_icon} Заказ №{order.id} от {order.created_at.strftime('%d.%m')} - {order.total_price} ₽"
            builder.button(text=text, callback_data=f"executor_view_my_order:{order.id}")
    builder.adjust(1)
    return builder.as_markup()


def get_work_in_progress_keyboard(order: Order) -> InlineKeyboardMarkup:
    """Клавиатура для управления статусом принятого заказа."""
    builder = InlineKeyboardBuilder()
    order_id = order.id
    current_status = order.status

    if current_status == OrderStatus.accepted:
        builder.button(text="🚀 В пути", callback_data=f"executor_status_ontheway:{order_id}")

    if current_status == OrderStatus.on_the_way:
        builder.button(text="✅ Начать уборку", callback_data=f"executor_status_inprogress:{order_id}")

    # Добавляем кнопку просмотра фото, если они есть
    if order.photo_file_ids:
        builder.button(text="🖼️ Фото квартиры", callback_data=f"view_order_photos:{order_id}")

    if current_status == OrderStatus.in_progress:
        builder.button(text="📸 Загрузить фото «после»", callback_data=f"executor_upload_photo:{order_id}")
        builder.button(text="✅ Завершить", callback_data=f"executor_complete_order:{order_id}")

    builder.button(text="💬 Чат с клиентом", callback_data=f"start_chat:{order_id}")

    builder.adjust(1)
    return builder.as_markup()

def get_new_order_notification_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для уведомления о новом заказе."""
    builder = InlineKeyboardBuilder()
    builder.button(text="➡️ Перейти к заказу", callback_data=f"executor_view_order:{order_id}")
    return builder.as_markup()


# Константы для дней недели и слотов, чтобы не дублировать код
WEEKDAYS = {
    "monday": "Понедельник",
    "tuesday": "Вторник",
    "wednesday": "Среда",
    "thursday": "Четверг",
    "friday": "Пятница",
    "saturday": "Суббота",
    "sunday": "Воскресенье",
}
TIME_SLOTS = ["9:00 - 12:00", "12:00 - 15:00", "15:00 - 18:00", "18:00 - 21:00"]


def get_schedule_menu_keyboard() -> InlineKeyboardMarkup:
    """Создает клавиатуру для главного меню редактирования графика."""
    builder = InlineKeyboardBuilder()

    # Добавляем первые 6 дней недели, по 2 в ряд
    days = list(WEEKDAYS.items())
    for i in range(0, 6, 2):
        builder.row(
            InlineKeyboardButton(text=days[i][1], callback_data=f"edit_schedule_day:{days[i][0]}"),
            InlineKeyboardButton(text=days[i + 1][1], callback_data=f"edit_schedule_day:{days[i + 1][0]}")
        )

    # Добавляем Воскресенье и кнопку Удалить в один ряд
    builder.row(
        InlineKeyboardButton(text=WEEKDAYS["sunday"], callback_data="edit_schedule_day:sunday"),
        InlineKeyboardButton(text="🗑️ Удалить всё", callback_data="clear_schedule")
    )

    # Добавляем кнопку Сохранить в отдельный ряд
    builder.row(InlineKeyboardButton(text="✅ Сохранить и выйти", callback_data="save_schedule"))

    return builder.as_markup()


def get_day_schedule_keyboard(day_code: str, selected_slots: list) -> InlineKeyboardMarkup:
    """Создает клавиатуру для редактирования слотов на конкретный день."""
    builder = InlineKeyboardBuilder()
    for slot in TIME_SLOTS:
        text = f"✅ {slot}" if slot in selected_slots else slot
        builder.button(text=text, callback_data=f"toggle_schedule_slot:{day_code}:{slot}")

    builder.button(text="⬅️ Назад к выбору дня", callback_data="back_to_schedule_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_balance_orders_keyboard(orders: list[Order]) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком последних начислений."""
    builder = InlineKeyboardBuilder()
    if orders:
        for order in orders:
            # Дата в формате ДД.ММ
            date_str = order.created_at.strftime('%d.%m')
            text = f"Заказ №{order.id} от {date_str} - {order.executor_payment} ₽"
            # Для кнопок истории можно не делать callback, либо вести на просмотр заказа
            builder.button(text=text, callback_data=f"executor_view_my_order:{order.id}")

    builder.button(text="⬅️ Назад к балансу", callback_data="back_to_balance")
    builder.adjust(1)
    return builder.as_markup()


def get_referral_program_keyboard(referral_link: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру для раздела реферальной программы."""
    builder = InlineKeyboardBuilder()

    text_to_share = urllib.parse.quote(
        f"Привет! Присоединяйся к нашей команде исполнителей клининга и начни зарабатывать. "
        f"Регистрируйся по моей ссылке и получи бонус после первого заказа!"
    )

    share_url = f"https://t.me/share/url?url={urllib.parse.quote(referral_link)}&text={text_to_share}"

    builder.button(text="🔗 Поделиться", url=share_url)
    return builder.as_markup()

def get_exit_chat_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой выхода из чата."""
    buttons = [
        [KeyboardButton(text="⬅️ Выйти из чата")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_reply_to_chat_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопкой 'Ответить' для чата."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить", callback_data=f"start_chat:{order_id}")
    return builder.as_markup()

def get_finish_upload_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой 'Готово' для завершения загрузки фото."""
    buttons = [
        [KeyboardButton(text="✅ Готово")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_order_changes_confirmation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения изменений в заказе."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять изменения", callback_data=f"executor_accept_changes:{order_id}")
    builder.button(text="❌ Отказаться от заказа", callback_data=f"executor_decline_changes:{order_id}")
    builder.adjust(1)
    return builder.as_markup()