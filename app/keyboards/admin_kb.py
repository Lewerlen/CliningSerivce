from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.database.models import Ticket, User

def get_admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню администратора."""
    buttons = [
        [KeyboardButton(text="📞 Поддержка")],
        # Сюда можно будет добавлять другие кнопки админки
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_support_keyboard(counts: dict) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для меню поддержки со счетчиками для всех статусов."""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📬 Новые обращения ({counts.get('new', 0)})", callback_data="admin_new_tickets")
    builder.button(text=f"👤 В работе ({counts.get('in_progress', 0)})", callback_data="admin_in_progress_tickets")
    builder.button(text=f"💬 Ожидают ответа ({counts.get('answered', 0)})", callback_data="admin_answered_tickets")
    builder.button(text=f"✅ Закрытые ({counts.get('closed', 0)})", callback_data="admin_closed_tickets")
    builder.adjust(1)
    return builder.as_markup()

def get_tickets_list_keyboard(tickets: list[Ticket], list_type: str) -> InlineKeyboardMarkup:
    """Создает универсальную клавиатуру для любого списка тикетов."""
    builder = InlineKeyboardBuilder()
    for ticket in tickets:
        user: User = ticket.user
        theme = ticket.messages[0].text[:20] if ticket.messages else "Без темы"

        # В зависимости от типа списка добавляем нужную иконку для наглядности
        if list_type == 'new':
            button_text = f"📬 №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"
        elif list_type == 'in_progress':
            button_text = f"👤 №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"
        elif list_type == 'answered':
            button_text = f"💬 №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"
        else:  # closed
            button_text = f"✅ №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"

        builder.button(text=button_text, callback_data=f"admin_view_ticket:{ticket.id}")

    builder.button(text="⬅️ Назад в меню поддержки", callback_data="admin_support_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_ticket_work_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для нового тикета с кнопкой 'Взять в работу'."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Взять в работу", callback_data=f"admin_take_ticket:{ticket_id}")
    builder.button(text="⬅️ К списку новых", callback_data="admin_new_tickets")
    builder.adjust(1)
    return builder.as_markup()

def get_in_progress_ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для тикета в работе."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить клиенту", callback_data=f"admin_reply_ticket:{ticket_id}")
    builder.button(text="✅ Закрыть обращение", callback_data=f"admin_close_ticket:{ticket_id}")
    builder.button(text="⬅️ К списку 'В работе'", callback_data="admin_in_progress_tickets")
    builder.adjust(1)
    return builder.as_markup()

def get_closed_ticket_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для закрытого тикета."""
    builder = InlineKeyboardBuilder()
    # Для закрытых тикетов можно добавить кнопку "Переоткрыть", но пока просто назад
    builder.button(text="⬅️ К списку закрытых", callback_data="admin_closed_tickets")
    return builder.as_markup()


def get_answered_ticket_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для тикета, ожидающего ответа клиента."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Ответить еще раз", callback_data=f"admin_reply_ticket:{ticket_id}")
    builder.button(text="✅ Закрыть обращение", callback_data=f"admin_close_ticket:{ticket_id}")
    builder.button(text="⬅️ К списку 'Ожидают ответа'", callback_data="admin_answered_tickets")
    builder.adjust(1)
    return builder.as_markup()
