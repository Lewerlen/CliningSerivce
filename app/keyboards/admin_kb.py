from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.database.models import Ticket, User, UserRole, Order, OrderStatus, UserStatus


def get_admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню администратора."""
    buttons = [
        [KeyboardButton(text="📋 Управление заказами"), KeyboardButton(text="🛠️ Управление исполнителями")],
        [KeyboardButton(text="📊 Статистика и отчеты"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="📞 Поддержка")]
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

        author_marker = "👤"  # По умолчанию - клиент
        if user.role == UserRole.executor:
            author_marker = "🛠️"

        # В зависимости от типа списка добавляем нужную иконку для наглядности
        if list_type == 'new':
            button_text = f"📬 {author_marker} №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"
        elif list_type == 'in_progress':
            button_text = f"👤 {author_marker} №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"
        elif list_type == 'answered':
            button_text = f"💬 {author_marker} №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"
        else:  # closed
            button_text = f"✅ {author_marker} №{ticket.id} от {user.name or user.telegram_id} - «{theme}...»"

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

def get_admin_orders_keyboard(counts: dict) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для меню управления заказами."""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🆕 Новые ({counts.get('new', 0)})", callback_data="admin_orders:new")
    builder.button(text=f"⏳ В работе ({counts.get('in_progress', 0)})", callback_data="admin_orders:in_progress")
    builder.button(text=f"✅ Завершенные ({counts.get('completed', 0)})", callback_data="admin_orders:completed")
    builder.button(text=f"❌ Отмененные ({counts.get('cancelled', 0)})", callback_data="admin_orders:cancelled")
    builder.button(text="⬅️ Назад в главное меню", callback_data="admin_main_menu")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_orders_list_keyboard(orders: list[Order], list_type: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру для списка заказов."""
    builder = InlineKeyboardBuilder()
    for order in orders:
        date_str = order.created_at.strftime('%d.%m')
        # Обрезаем длинный адрес, чтобы кнопка не была слишком большой
        address_preview = order.address_text[:20] + '...' if len(order.address_text) > 20 else order.address_text
        test_label = " (ТЕСТ)" if order.is_test else ""
        text = f"№{order.id}{test_label} от {date_str} - {order.total_price} ₽ ({address_preview})"
        builder.button(text=text, callback_data=f"admin_view_order:{order.id}")

    builder.button(text="⬅️ Назад к категориям", callback_data="admin_manage_orders")
    builder.adjust(1)
    return builder.as_markup()

def get_view_order_keyboard_admin(order: Order, list_type: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру для просмотра деталей заказа в админ-панели."""
    builder = InlineKeyboardBuilder()
    order_id = order.id

    # Кнопки для чатов
    builder.button(text=f"💬 Чат с клиентом", callback_data=f"admin_chat:client:{order_id}")
    if order.executor_tg_id:
        builder.button(text=f"💬 Чат с исполнителем", callback_data=f"admin_chat:executor:{order_id}")

    # Контекстные кнопки в зависимости от статуса
    if order.status == OrderStatus.new:
        builder.button(text="👤 Назначить исполнителя", callback_data=f"admin_assign_executor:{order_id}")
    elif order.status in {OrderStatus.accepted, OrderStatus.on_the_way, OrderStatus.in_progress}:
        builder.button(text="🔄 Переназначить исполнителя", callback_data=f"admin_reassign_executor:{order_id}")
        # Кнопка изменения выплаты, если исполнитель назначен
        builder.button(text="✏️ Изменить выплату", callback_data=f"admin_edit_payment:{order_id}")


    edit_button = InlineKeyboardButton(text="✏️ Редактировать заказ", callback_data=f"admin_edit_order:{order_id}")
    cancel_button = InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel_order:{order_id}")

    # Кнопки редактирования и отмены в одном ряду, если заказ не отменен
    if order.status != OrderStatus.cancelled:
        builder.row(edit_button, cancel_button)

    # Кнопка "Назад" всегда внизу и на всю ширину
    builder.row(InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"admin_orders:{list_type}"))

    # Выстраиваем все кнопки, которые были добавлены по одной, в один столбец
    builder.adjust(1)
    return builder.as_markup()

def get_admin_edit_order_keyboard(order_id: int, list_type: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора действия при редактировании заказа админом."""
    builder = InlineKeyboardBuilder()
    # Пока добавим только одну кнопку для примера, остальные реализуем дальше
    builder.button(text="📝 Изменить доп. услуги", callback_data=f"admin_edit_services:{order_id}")
    builder.button(text="📅 Изменить дату и время", callback_data=f"admin_edit_datetime:{order_id}")
    builder.button(text="📍 Изменить адрес", callback_data=f"admin_edit_address:{order_id}")
    builder.button(text="🏠 Изменить кол-во комнат/санузлов", callback_data=f"admin_edit_rooms:{order_id}")
    builder.button(text="⬅️ Назад к заказу", callback_data=f"admin_view_order:{order_id}")
    builder.adjust(1)
    return builder.as_markup()

def get_assign_executor_keyboard(executors: list[User], order_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком исполнителей для назначения с пагинацией.
    """
    builder = InlineKeyboardBuilder()
    items_per_page = 5
    start_index = page * items_per_page
    end_index = start_index + items_per_page
    paginated_executors = executors[start_index:end_index]

    for executor in paginated_executors:
        # Новый формат текста кнопки
        text = (f"{executor.name} (П: {executor.priority}, Р: {executor.average_rating} ⭐, З: {executor.review_count})")
        builder.button(
            text=text,
            callback_data=f"admin_confirm_assign:{order_id}:{executor.telegram_id}"
        )

    # Логика для кнопок пагинации
    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_assign_page:{order_id}:{page - 1}")
        )
    if end_index < len(executors):
        pagination_buttons.append(
            InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_assign_page:{order_id}:{page + 1}")
        )
    if pagination_buttons:
        builder.row(*pagination_buttons)

    # Кнопка возврата к карточке заказа
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"admin_view_order:{order_id}"))
    builder.adjust(1)
    return builder.as_markup()

def get_executors_list_keyboard(executors: list[User], page: int = 0) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком исполнителей с пагинацией.
    """
    builder = InlineKeyboardBuilder()
    items_per_page = 5
    start_index = page * items_per_page
    end_index = start_index + items_per_page
    paginated_executors = executors[start_index:end_index]

    for executor in paginated_executors:
        status_icon = "✅" if executor.status == UserStatus.active else "❌"
        text = f"{status_icon} {executor.name} (П: {executor.priority}, Р: {executor.average_rating} ⭐)"
        builder.button(
            text=text,
            callback_data=f"admin_view_executor:{executor.telegram_id}:{page}"
        )

    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_executors_page:{page - 1}")
        )
    if end_index < len(executors):
        pagination_buttons.append(
            InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_executors_page:{page + 1}")
        )
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.row(InlineKeyboardButton(text="⬅️ Назад в главное меню", callback_data="admin_main_menu"))
    builder.adjust(1)
    return builder.as_markup()

def get_view_executor_keyboard_admin(executor: User, page: int, current_user: User,
                                     supervisor: User | None, owner_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для просмотра и управления профилем исполнителя."""
    builder = InlineKeyboardBuilder()
    executor_id = executor.telegram_id

    # Контекстная кнопка для блокировки/разблокировки
    if executor.status == UserStatus.active:
        builder.button(text="❌ Заблокировать", callback_data=f"admin_block_executor:{executor_id}:{page}")
    else:
        builder.button(text="✅ Активировать", callback_data=f"admin_unblock_executor:{executor_id}:{page}")

    # Другие кнопки управления
    builder.button(text=f"📊 Статистика заказов", callback_data=f"admin_executor_stats:{executor_id}:{page}")
    builder.button(text="📊 Экспорт в Excel", callback_data=f"admin_executor_report:{executor_id}:{page}")
    builder.button(text="✏️ Изменить приоритет", callback_data=f"admin_edit_priority:{executor_id}:{page}")

    # Кнопка управления доступом, видна владельцу и администраторам
    if current_user.role == UserRole.admin or current_user.telegram_id == owner_id:
        builder.button(text="👑 Управлять доступом", callback_data=f"admin_manage_access:{executor_id}:{page}")

    builder.button(text="⬅️ Назад к списку", callback_data=f"admin_executors_page:{page}")
    builder.adjust(1)
    return builder.as_markup()

def get_block_confirmation_keyboard(executor_id: int, page: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения блокировки исполнителя."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Да, заблокировать", callback_data=f"admin_confirm_block:{executor_id}:{page}")
    builder.button(text="⬅️ Отмена", callback_data=f"admin_view_executor:{executor_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()

def get_statistics_menu_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для меню статистики и отчетов."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Общая статистика", callback_data="show_general_statistics")
    builder.button(text="🏆 ТОП исполнителей", callback_data="show_top_executors")
    builder.button(text="➕ ТОП доп. услуг", callback_data="show_top_services")
    builder.button(text="📄 Экспорт заказов (Excel)", callback_data="export_orders_period")
    builder.button(text="⬅️ Назад в главное меню", callback_data="admin_main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_report_period_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора периода отчета."""
    builder = InlineKeyboardBuilder()
    builder.button(text="За сегодня", callback_data="report:today")
    builder.button(text="За неделю", callback_data="report:week")
    builder.button(text="За месяц", callback_data="report:month")
    builder.button(text="За все время", callback_data="report:all_time")
    builder.button(text="⬅️ Назад в меню статистики", callback_data="reports_menu")
    builder.adjust(2, 2, 1)
    return builder.as_markup()

def get_new_order_admin_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для уведомления администратора о новом заказе."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Посмотреть заказ", callback_data=f"admin_view_order:{order_id}")
    return builder.as_markup()


def get_manage_access_keyboard(executor: User, page: int, owner_id: int, current_user_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру для управления доступом и ролями пользователя."""
    builder = InlineKeyboardBuilder()
    executor_id = executor.telegram_id

    # Кнопки управления ролями видит только владелец бота
    if current_user_id == owner_id:
        if executor.role != UserRole.admin:
            builder.button(text="👑 Сделать администратором", callback_data=f"admin_make_admin:{executor_id}:{page}")
        else:
            builder.button(text="🔻 Разжаловать из администраторов", callback_data=f"admin_remove_admin:{executor_id}:{page}")

        if executor.role != UserRole.supervisor:
            builder.button(text="⬆️ Сделать супервайзером", callback_data=f"admin_make_supervisor:{executor_id}:{page}")
        else:
            builder.button(text="⬇️ Снять с супервайзера", callback_data=f"admin_remove_supervisor:{executor_id}:{page}")


    # Кнопки для назначения/снятия супервайзера для данного исполнителя
    builder.button(text="👨‍💼 Назначить супервайзера",
                   callback_data=f"admin_assign_supervisor_start:{executor_id}:{page}")
    if executor.supervisor_id:
        builder.button(text="🗑️ Открепить от супервайзера",
                       callback_data=f"admin_unassign_supervisor:{executor_id}:{page}")

    builder.button(text="⬅️ Назад к профилю", callback_data=f"admin_view_executor:{executor_id}:{page}")
    builder.adjust(1)
    return builder.as_markup()


def get_supervisors_list_keyboard(supervisors: list[User], executor_id_to_assign: int,
                                  page: int = 0) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком супервайзеров для выбора."""
    builder = InlineKeyboardBuilder()

    for supervisor in supervisors:
        text = f"{supervisor.name} (@{supervisor.username})" if supervisor.username else supervisor.name
        builder.button(
            text=text,
            callback_data=f"admin_assign_supervisor_finish:{executor_id_to_assign}:{supervisor.telegram_id}:{page}"
        )

    builder.button(text="⬅️ Назад", callback_data=f"admin_manage_access:{executor_id_to_assign}:{page}")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_settings_keyboard(test_mode_status: str, current_user_id: int, owner_id: int) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру главного меню настроек."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Управление тарифами", callback_data="admin_setting:tariffs")
    builder.button(text="💰 Управление комиссией", callback_data="admin_setting:commission")
    builder.button(text=f"🧪 Тестовый режим ({test_mode_status})", callback_data="admin_setting:test_mode")

    # Кнопка видна только владельцу
    if current_user_id == owner_id:
        builder.button(text="👑 Управление администрацией", callback_data="admin_setting:administration")

    builder.button(text="⬅️ Назад в главное меню", callback_data="admin_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_tariff_management_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора типа тарифа для редактирования."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🧹 Основные типы уборок", callback_data="admin_tariff:main")
    builder.button(text="➕ Дополнительные услуги", callback_data="admin_tariff:additional")
    builder.button(text="⬅️ Назад в настройки", callback_data="admin_settings_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_main_tariffs_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для выбора основного тарифа для редактирования."""
    builder = InlineKeyboardBuilder()
    # Эти ключи должны совпадать с ключами в TARIFFS в price_calculator.py
    builder.button(text="🧽 Поддерживающая", callback_data="admin_edit_tariff:🧽 Поддерживающая")
    builder.button(text="🧼 Генеральная", callback_data="admin_edit_tariff:🧼 Генеральная")
    builder.button(text="🛠 После ремонта", callback_data="admin_edit_tariff:🛠 После ремонта")
    builder.button(text="⬅️ Назад", callback_data="admin_setting:tariffs")
    builder.adjust(1)
    return builder.as_markup()


def get_additional_services_edit_keyboard(additional_services: dict) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора доп. услуги для редактирования цены."""
    builder = InlineKeyboardBuilder()
    for key, text in additional_services.items():
        # Убираем текущую цену из текста для чистоты
        service_name = text.split('(')[0].strip()
        builder.button(text=service_name, callback_data=f"admin_edit_service:{key}")
    builder.button(text="⬅️ Назад", callback_data="admin_setting:tariffs")
    builder.adjust(1)
    return builder.as_markup()

def get_commission_management_keyboard(current_type: str, current_value: float, show_commission: bool) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для управления комиссией."""
    builder = InlineKeyboardBuilder()
    type_text = "Процент (%)" if current_type == "percent" else "Фикс. сумма (₽)"
    show_text = "Да ✅" if show_commission else "Нет ❌"

    builder.button(text=f"Тип комиссии: {type_text}", callback_data="admin_commission:change_type")
    builder.button(text=f"Значение: {current_value}", callback_data="admin_commission:change_value")
    builder.button(text=f"Показывать комиссию: {show_text}", callback_data="admin_commission:toggle_show")
    builder.button(text="⬅️ Назад в настройки", callback_data="admin_settings_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_administration_management_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для меню управления администрацией."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Список администраторов", callback_data="admin_admin:list")
    builder.button(text="➕ Назначить Администратора", callback_data="admin_admin:add_admin")
    builder.button(text="➕ Назначить Супервайзера", callback_data="admin_admin:add_supervisor")
    builder.button(text="⬅️ Назад в настройки", callback_data="admin_settings_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_list_keyboard(admins: list[User]) -> InlineKeyboardMarkup:
    """Создает клавиатуру со списком администраторов и супервайзеров."""
    builder = InlineKeyboardBuilder()
    for admin in admins:
        role_icon = "👑" if admin.role == UserRole.admin else "⬆️"
        text = f"{role_icon} {admin.name} (@{admin.username or admin.telegram_id})"
        # Кнопка для снятия роли
        builder.button(text=text, callback_data=f"admin_admin:remove_role:{admin.telegram_id}")

    builder.button(text="⬅️ Назад", callback_data="admin_setting:administration")
    builder.adjust(1)
    return builder.as_markup()

def get_cancel_editing_tariff_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру с кнопкой отмены для меню редактирования тарифа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Отмена", callback_data="admin_tariff:main")
    return builder.as_markup()
