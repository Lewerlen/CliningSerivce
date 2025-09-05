import logging
import io
from contextlib import suppress
from openpyxl import Workbook
from openpyxl.styles import Font
import datetime
import json
import re
from aiogram import F, Router, types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.services.yandex_maps_api import get_address_from_coords, get_address_from_text
from app.keyboards.client_kb import (
    get_additional_services_keyboard, create_calendar, get_time_keyboard,
    get_address_keyboard, get_address_confirmation_keyboard,
    get_room_count_keyboard, get_bathroom_count_keyboard, get_exit_chat_keyboard, get_reply_to_chat_keyboard
)
from app.services.price_calculator import calculate_preliminary_cost, calculate_total_cost, calculate_executor_payment
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.keyboards.executor_kb import get_order_changes_confirmation_keyboard
from app.services.db_queries import update_system_settings
from app.database.models import TicketStatus, MessageAuthor, UserRole, OrderStatus, Order, User, UserStatus, OrderLog
from app.common.texts import STATUS_MAPPING, ADDITIONAL_SERVICES, RUSSIAN_MONTHS_GENITIVE
from app.config import Settings
from app.services.db_queries import (
    get_user,update_user_role,
    assign_supervisor_to_executor, get_all_supervisors,
    block_executor_by_admin,
    unblock_executor_by_admin,
    get_tickets_by_status,
    get_ticket_by_id,
    update_ticket_status,
    add_message_to_ticket,
    get_order_counts_by_status,
    get_order_details_for_admin,
    get_order_by_id,
    get_matching_executors,
    assign_executor_to_order,
    get_all_executors,
    unassign_executor_from_order,
    update_order_status,
    update_order_services_and_price,
    update_order_datetime, get_all_admins_and_supervisors,
    update_order_address, get_orders_for_report_for_executor,
    update_order_rooms_and_price,get_orders_by_status_for_supervisor,
    update_executor_payment, get_orders_for_report,
    update_executor_priority,get_executor_statistics, get_general_statistics, get_top_executors,
    get_top_additional_services,
)
from app.handlers.states import AdminSupportStates, AdminOrderStates, ChatStates, AdminExecutorStates, AdminSettingsStates
from app.keyboards.admin_kb import (
    get_executors_list_keyboard,
    get_view_executor_keyboard_admin,
    get_admin_main_keyboard,
    get_administration_management_keyboard,
    get_tickets_list_keyboard,
    get_ticket_work_keyboard,
    get_in_progress_ticket_keyboard,
    get_closed_ticket_keyboard,
    get_answered_ticket_keyboard,
    get_admin_orders_keyboard,
    get_orders_list_keyboard,
    get_view_order_keyboard_admin,
    get_assign_executor_keyboard,
    get_block_confirmation_keyboard,
    get_admin_edit_order_keyboard,
    get_report_period_keyboard,
    get_statistics_menu_keyboard, get_manage_access_keyboard,get_cancel_editing_tariff_keyboard,
    get_supervisors_list_keyboard, get_admin_list_keyboard,
    get_admin_settings_keyboard, get_tariff_management_keyboard, get_main_tariffs_keyboard,
    get_additional_services_edit_keyboard, get_commission_management_keyboard
)
def calculate_price_from_service_string(service_string: str) -> int:
    """Извлекает числовую цену из строки услуги с помощью регулярного выражения."""
    if not service_string:
        return 0
    # Ищем числа внутри скобок
    match = re.search(r'\(.*?\+(\d+)', service_string)
    if match:
        return int(match.group(1))
    return 0

router = Router()

@router.message(CommandStart())
async def cmd_start_admin(message: types.Message, session: AsyncSession, config: Settings):
    user = await get_user(session, message.from_user.id)
    is_owner = message.from_user.id == config.admin_id

    # Проверяем, является ли пользователь владельцем, админом или супервайзером
    if is_owner or (user and user.role in [UserRole.admin, UserRole.supervisor]):
        await message.answer(
            "Добро пожаловать в панель администратора.",
            reply_markup=get_admin_main_keyboard()
        )
    else:
        await message.answer(
            "У вас нет доступа к этой панели.",
            reply_markup=types.ReplyKeyboardRemove()
        )


@router.callback_query(
    F.data.in_({"admin_new_tickets", "admin_in_progress_tickets", "admin_answered_tickets", "admin_closed_tickets"}))
async def list_tickets_by_status(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает списки тикетов в зависимости от нажатой кнопки."""
    query_data = callback.data
    status_map = {
        "admin_new_tickets": (TicketStatus.new, "Новые обращения"),
        "admin_in_progress_tickets": (TicketStatus.in_progress, "Обращения в работе"),
        "admin_answered_tickets": (TicketStatus.answered, "Ожидают ответа клиента"),
        "admin_closed_tickets": (TicketStatus.closed, "Закрытые обращения")
    }

    status, title = status_map.get(query_data)
    list_type = query_data.replace("admin_", "").replace("_tickets", "")

    tickets = await get_tickets_by_status(session, status)

    if not tickets:
        await callback.answer(f"{title} отсутствуют.", show_alert=True)
        return

    text = f"<b>{title}:</b>"
    reply_markup = get_tickets_list_keyboard(tickets, list_type=list_type)

    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(text, reply_markup=reply_markup)
    else:
        await callback.message.edit_text(text, reply_markup=reply_markup)

    await callback.answer()

@router.callback_query(F.data.startswith("admin_view_ticket:"))
async def view_ticket_admin(callback: types.CallbackQuery, session: AsyncSession, bots: dict):
    """Показывает администратору выбранный тикет."""
    ticket_id = int(callback.data.split(":")[1])
    ticket = await get_ticket_by_id(session, ticket_id)

    if not ticket:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    author_role = "клиента"
    if ticket.user.role == UserRole.executor:
        author_role = "исполнителя"

    # Собираем историю переписки
    history = f"<b>Обращение №{ticket.id} от {author_role} {ticket.user.name or ticket.user.tg_id}</b>\n"
    history += f"Статус: <i>{ticket.status.value}</i>\n\n"
    photo_id = None

    first_message = ticket.messages[0] if ticket.messages else None
    if first_message and first_message.photo_file_id:
        photo_id = first_message.photo_file_id

    for message in sorted(ticket.messages, key=lambda m: m.created_at):
        author = "Клиент" if message.author == MessageAuthor.client else "Поддержка"
        time = message.created_at.strftime('%H:%M')
        history += f"<b>{author}</b> ({time}):\n{message.text}\n"
        if message.photo_file_id:
            history += "<i>К сообщению прикреплено фото.</i>\n"
        history += "\n"

        # В зависимости от статуса показываем разную клавиатуру
    reply_markup = None
    if ticket.status == TicketStatus.new:
        reply_markup = get_ticket_work_keyboard(ticket.id)
    elif ticket.status == TicketStatus.in_progress:
        reply_markup = get_in_progress_ticket_keyboard(ticket.id)
    elif ticket.status == TicketStatus.answered:
        reply_markup = get_answered_ticket_keyboard(ticket.id)
    elif ticket.status == TicketStatus.closed:
        reply_markup = get_closed_ticket_keyboard()

    # Если мы нашли фото, скачиваем его через КЛИЕНТ-БОТА и отправляем через АДМИН-БОТА
    if photo_id:
        try:
            client_bot = bots["client"]
            photo_file = await client_bot.get_file(photo_id)
            photo_bytes_io = await client_bot.download_file(photo_file.file_path)
            photo_bytes = photo_bytes_io.read()

            photo_to_send = BufferedInputFile(photo_bytes, filename="photo.jpg")

            await callback.message.delete()
            await callback.message.answer_photo(
                photo=photo_to_send,
                caption=history,
                reply_markup=reply_markup
            )
        except Exception:
            # Если что-то пошло не так, просто отправим текст, чтобы не было ошибки
            await callback.message.answer(history, reply_markup=reply_markup)
    else:
        # Если фото нет, просто редактируем текст
        await callback.message.edit_text(history, reply_markup=reply_markup)

    await callback.answer()


@router.callback_query(F.data.startswith("admin_take_ticket:"))
async def take_ticket_in_work(callback: types.CallbackQuery, session: AsyncSession, bots: dict):
    """Обработчик кнопки 'Взять в работу'."""
    ticket_id = int(callback.data.split(":")[1])

    # 1. Обновляем статус и назначаем админа
    ticket = await update_ticket_status(
        session,
        ticket_id=ticket_id,
        status=TicketStatus.in_progress,
        admin_tg_id=callback.from_user.id
    )
    if not ticket:
        await callback.answer("Не удалось обновить тикет.", show_alert=True)
        return

    # 2. Перезапрашиваем тикет, чтобы подгрузить все связанные данные
    ticket = await get_ticket_by_id(session, ticket_id)

    # 3. Собираем обновленную историю переписки (как в view_ticket_admin)
    history = f"<b>Обращение №{ticket.id} от клиента {ticket.user.name or ticket.user.tg_id}</b>\n"
    history += f"Статус: <i>{ticket.status.value}</i>\n\n"
    for message in sorted(ticket.messages, key=lambda m: m.created_at):
        author = "Клиент" if message.author == MessageAuthor.client else "Поддержка"
        time = message.created_at.strftime('%H:%M')
        history += f"<b>{author}</b> ({time}):\n{message.text}\n"
        if message.photo_file_id:
            history += "<i>К сообщению прикреплено фото.</i>\n"
        history += "\n"

    # 4. Получаем новую клавиатуру для тикета "в работе"
    reply_markup = get_in_progress_ticket_keyboard(ticket_id)

    # 5. Редактируем исходное сообщение (текст или подпись к фото)
    if callback.message.photo:
        await callback.message.edit_caption(caption=history, reply_markup=reply_markup)
    else:
        await callback.message.edit_text(history, reply_markup=reply_markup)

    # 6. Уведомляем клиента, что его обращением занялись
    try:
        await bots["client"].send_message(
            chat_id=ticket.user_tg_id,
            text=f"👤 Сотрудник поддержки взял в работу ваше обращение №{ticket_id}."
        )
    except Exception as e:
        await callback.message.answer(f"⚠️ Не удалось уведомить клиента: {e}")

    await callback.answer("Тикет взят в работу")


@router.callback_query(F.data.startswith("admin_reply_ticket:"))
async def admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс ответа администратора на тикет."""
    ticket_id = int(callback.data.split(":")[1])
    await state.update_data(replying_ticket_id=ticket_id)

    await callback.message.answer(
        "Введите ваш ответ для клиента. Вы можете прикрепить к сообщению одно фото."
    )
    await state.set_state(AdminSupportStates.replying_to_ticket)
    await callback.answer()


@router.message(AdminSupportStates.replying_to_ticket, (F.text | F.photo))
async def admin_reply_finish(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict):
    """Завершает процесс ответа, отправляет сообщение клиенту и обновляет вид для админа."""
    user_data = await state.get_data()
    ticket_id = user_data.get("replying_ticket_id")

    ticket = await get_ticket_by_id(session, ticket_id)
    if not ticket:
        await message.answer("Ошибка: не удалось найти тикет для ответа.")
        await state.clear()
        return

    # 1. Подготовка и отправка ответа клиенту
    reply_text = message.text or message.caption or ""
    original_photo_id = message.photo[-1].file_id if message.photo else None
    new_photo_id_for_db = None

    client_bot = bots["client"]
    admin_bot = bots["admin"]
    client_message_text = f"💬 <b>Получен ответ от поддержки по обращению №{ticket_id}</b>\n\n{reply_text}"
    go_to_ticket_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➡️ К тикету", callback_data=f"view_ticket:{ticket_id}")]
    ])

    try:
        if original_photo_id:
            photo_file = await admin_bot.get_file(original_photo_id)
            photo_bytes_io = await admin_bot.download_file(photo_file.file_path)
            photo_to_send = BufferedInputFile(photo_bytes_io.read(), filename="photo.jpg")

            sent_message = await client_bot.send_photo(
                ticket.user_tg_id, photo=photo_to_send,
                caption=client_message_text, reply_markup=go_to_ticket_keyboard
            )
            new_photo_id_for_db = sent_message.photo[-1].file_id
        else:
            await client_bot.send_message(
                ticket.user_tg_id, text=client_message_text,
                reply_markup=go_to_ticket_keyboard
            )
        await message.answer(f"✅ Ответ на обращение №{ticket_id} отправлен.")

    except Exception as e:
        await message.answer(f"⚠️ Не удалось отправить ответ клиенту: {e}")
        return

    # 2. Сохранение сообщения в БД с правильным file_id
    await add_message_to_ticket(session, ticket_id, MessageAuthor.admin, reply_text, new_photo_id_for_db)

    # 3. Обновление вида для администратора
    await message.answer("Вы можете написать еще одно сообщение или вернуться к списку тикетов.")
    updated_ticket = await get_ticket_by_id(session, ticket_id)

    history = f"<b>Обращение №{updated_ticket.id} от клиента {updated_ticket.user.name or updated_ticket.user.tg_id}</b>\n"
    history += f"Статус: <i>{updated_ticket.status.value}</i>\n\n"
    photo_id_for_admin_view = None

    for msg in sorted(updated_ticket.messages, key=lambda m: m.created_at):
        author = "Клиент" if msg.author == MessageAuthor.client else "Поддержка"
        time = msg.created_at.strftime('%H:%M')
        history += f"<b>{author}</b> ({time}):\n{msg.text}\n"
        if msg.photo_file_id:
            history += "<i>К сообщению прикреплено фото.</i>\n"
            if not photo_id_for_admin_view:
                photo_id_for_admin_view = msg.photo_file_id
        history += "\n"

    reply_markup = get_answered_ticket_keyboard(ticket_id)

    if photo_id_for_admin_view:
        try:
            # Все file_id в базе теперь доступны через client_bot
            photo_file = await client_bot.get_file(photo_id_for_admin_view)
            photo_bytes_io = await client_bot.download_file(photo_file.file_path)
            photo_to_send = BufferedInputFile(photo_bytes_io.read(), filename="photo.jpg")
            await message.answer_photo(photo=photo_to_send, caption=history, reply_markup=reply_markup)
        except TelegramBadRequest:
            await message.answer(history, reply_markup=reply_markup)
    else:
        await message.answer(history, reply_markup=reply_markup)

@router.callback_query(F.data.startswith("admin_close_ticket:"))
async def admin_close_ticket(callback: types.CallbackQuery, session: AsyncSession, bots: dict):
    """Закрывает тикет по инициативе администратора."""
    ticket_id = int(callback.data.split(":")[1])

    updated_ticket = await update_ticket_status(session, ticket_id, TicketStatus.closed)

    if not updated_ticket:
        await callback.answer("Не удалось закрыть тикет.", show_alert=True)
        return

    await callback.message.delete()
    await callback.message.answer(f"Вы закрыли обращение №{ticket_id}.")

    # Уведомляем клиента
    try:
        await bots["client"].send_message(
            chat_id=updated_ticket.user_tg_id,
            text=f"✅ Сотрудник поддержки закрыл ваше обращение №{ticket_id}."
        )
    except Exception as e:
        await callback.message.answer(f"⚠️ Не удалось уведомить клиента о закрытии: {e}")

    await callback.answer()

@router.message(F.text == "📋 Управление заказами")
async def manage_orders(message: types.Message, session: AsyncSession):
    counts = await get_order_counts_by_status(session)
    await message.answer(
        "🗂️ Выберите категорию заказов для просмотра:",
        reply_markup=get_admin_orders_keyboard(counts)
    )


@router.message(F.text == "🛠️ Управление исполнителями")
async def manage_executors(message: types.Message, session: AsyncSession, state: FSMContext, config: Settings):
    await state.clear()

    current_user = await get_user(session, message.from_user.id)

    executors_to_show = []
    # Если запрашивающий - супервайзер, показываем только его исполнителей
    if current_user and current_user.role == UserRole.supervisor:
        executors_to_show = await get_all_executors(session, supervisor_id=current_user.telegram_id)
    # Если администратор или владелец из .env - показываем всех
    elif (current_user and current_user.role == UserRole.admin) or message.from_user.id == config.admin_id:
        executors_to_show = await get_all_executors(session)

    if not executors_to_show:
        await message.answer(
            "В системе пока нет зарегистрированных исполнителей (или в вашей группе).",
            reply_markup=get_admin_main_keyboard()
        )
        return

    await state.set_state(AdminExecutorStates.viewing_executors)
    await state.update_data(executors_list=executors_to_show)

    await message.answer(
        "📋 <b>Список исполнителей:</b>",
        reply_markup=get_executors_list_keyboard(executors_to_show, page=0)
    )

@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_executors_page:"))
async def admin_executors_page(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает переключение страниц в списке исполнителей."""
    page = int(callback.data.split(":")[1])
    user_data = await state.get_data()
    executors = user_data.get("executors_list", [])

    await callback.message.edit_reply_markup(
        reply_markup=get_executors_list_keyboard(executors, page=page)
    )
    await callback.answer()


@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_view_executor:"))
async def view_executor_admin(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, config: Settings):
    """Показывает администратору детальную карточку исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    executor = await get_user(session, executor_id)

    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    # Получаем текущего пользователя (админа/супервайзера)
    current_user = await get_user(session, callback.from_user.id)
    if not current_user:
        await callback.answer("Не удалось идентифицировать вас.", show_alert=True)
        return

    # Получаем супервайзера для данного исполнителя, если он есть
    supervisor = None
    if executor.supervisor_id:
        supervisor = await get_user(session, executor.supervisor_id)

    status_text = "Активен ✅" if executor.status == UserStatus.active else "Заблокирован ❌"
    if executor.status == UserStatus.blocked and executor.blocked_until:
        status_text = f"Заблокирован до {executor.blocked_until.strftime('%d.%m %H:%M')} ❌"

    supervisor_info = f"@{supervisor.username}" if supervisor and supervisor.username else supervisor.telegram_id if supervisor else "Не назначен"

    # Формируем строку с юзернеймом, если он есть
    username_info = f"<b>Username:</b> @{executor.username}\n" if executor.username else ""

    executor_details = (
        f"<b>🛠️ Профиль исполнителя</b>\n\n"
        f"<b>Имя:</b> {executor.name}\n"
        f"{username_info}"
        f"<b>ID:</b> <code>{executor.telegram_id}</code>\n"
        f"<b>Телефон:</b> {executor.phone}\n\n"
        f"<b>Статус:</b> {status_text}\n"
        f"<b>Приоритет:</b> {executor.priority}\n"
        f"<b>Супервайзер:</b> {supervisor_info}\n\n"
        f"<b>Рейтинг:</b> {executor.average_rating} ⭐ ({executor.review_count} оценок)\n"
        f"<b>Баланс (реф.):</b> {executor.referral_balance} ₽\n"
        f"<b>Приглашено:</b> {executor.referrals_count} чел."
    )

    await callback.message.edit_text(
        executor_details,
        reply_markup=get_view_executor_keyboard_admin(
            executor=executor,
            page=page,
            current_user=current_user,
            supervisor=supervisor,
            owner_id=config.admin_id
        )
    )
    await callback.answer()

@router.callback_query(F.data == "show_general_statistics")
async def show_general_statistics(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает общую статистику."""
    stats = await get_general_statistics(session)
    text = (
        "📊 <b>Общая статистика:</b>\n\n"
        f"<b>За сегодня:</b> {stats.get('orders_today') or 0} заказ(ов) на сумму {stats.get('revenue_today') or 0:.2f} ₽\n"
        f"<b>За неделю:</b> {stats.get('orders_week') or 0} заказ(ов) на сумму {stats.get('revenue_week') or 0:.2f} ₽\n"
        f"<b>За месяц:</b> {stats.get('orders_month') or 0} заказ(ов) на сумму {stats.get('revenue_month') or 0:.2f} ₽\n\n"
        f"💰 <b>Средний чек:</b> {stats.get('avg_check') or 0:.2f} ₽\n"
        f"⏱️ <b>Среднее время выполнения:</b> {stats.get('avg_completion_time', 'Нет данных')}"
    )
    await callback.message.edit_text(text, reply_markup=get_statistics_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "show_top_executors")
async def show_top_executors(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает топ исполнителей."""
    top_executors = await get_top_executors(session)
    if not top_executors:
        text = "🏆 <b>Топ исполнителей:</b>\n\nПока нет исполнителей с оценками."
    else:
        executors_list = [
            f"{i + 1}. {user.name} - ⭐ {user.average_rating:.2f} ({user.review_count} отзывов)"
            for i, user in enumerate(top_executors)
        ]
        text = "🏆 <b>Топ исполнителей:</b>\n\n" + "\n".join(executors_list)

    await callback.message.edit_text(text, reply_markup=get_statistics_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "show_top_services")
async def show_top_services(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает топ дополнительных услуг."""
    top_services = await get_top_additional_services(session)
    if not top_services:
        text = "➕ <b>Топ дополнительных услуг:</b>\n\nЕще не было заказано ни одной дополнительной услуги."
    else:
        services_list = [
            f"{i + 1}. {ADDITIONAL_SERVICES.get(key, key).split('(')[0].strip()} - {count} раз"
            for i, (key, count) in enumerate(top_services)
        ]
        text = "➕ <b>Топ дополнительных услуг:</b>\n\n" + "\n".join(services_list)

    await callback.message.edit_text(text, reply_markup=get_statistics_menu_keyboard())
    await callback.answer()

@router.message(F.text == "⚙️ Настройки")
async def view_settings(message: types.Message, state: FSMContext, config: Settings):
    """Отображает главное меню настроек."""
    await state.set_state(AdminSettingsStates.choosing_setting)
    test_mode_status = "Вкл. ✅" if config.system.test_mode_enabled else "Выкл. ❌"
    await message.answer(
        "⚙️ <b>Настройки системы</b>\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_admin_settings_keyboard(
            test_mode_status=test_mode_status,
            current_user_id=message.from_user.id,
            owner_id=config.admin_id
        )
    )

# Добавляем эти новые функции в конец файла
@router.callback_query(F.data == "admin_main_menu")
async def back_to_admin_main_menu(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает в главное reply-меню администратора."""
    await callback.message.delete()
    await state.clear()
    await callback.message.answer(
        "Добро пожаловать в панель администратора.",
        reply_markup=get_admin_main_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_manage_orders")
async def back_to_manage_orders(callback: types.CallbackQuery, session: AsyncSession):
    """Возвращает к меню выбора категорий заказов."""
    counts = await get_order_counts_by_status(session)
    await callback.message.edit_text(
        "🗂️ Выберите категорию заказов для просмотра:",
        reply_markup=get_admin_orders_keyboard(counts)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_orders:"))
async def list_orders_by_status(callback: types.CallbackQuery, session: AsyncSession, config: Settings):
    """Показывает список заказов в зависимости от выбранного статуса и роли."""
    current_user = await get_user(session, callback.from_user.id)
    list_type = callback.data.split(":")[1]

    status_map = {
        "new": ([OrderStatus.new], "🆕 Новые заказы"),
        "in_progress": ([OrderStatus.accepted, OrderStatus.on_the_way, OrderStatus.in_progress], "⏳ Заказы в работе"),
        "completed": ([OrderStatus.completed], "✅ Завершенные заказы"),
        "cancelled": ([OrderStatus.cancelled], "❌ Отмененные заказы")
    }
    statuses, title = status_map.get(list_type, ([], "Неизвестная категория"))

    if not statuses:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return

    orders = []
    # Логика для Супервайзера
    if current_user and current_user.role == UserRole.supervisor:
        if list_type == "new":
            # Супервайзеры видят все новые заказы, чтобы иметь возможность их назначать
            stmt = select(Order).where(Order.status.in_(statuses)).order_by(Order.created_at.desc())
            result = await session.execute(stmt)
            orders = result.scalars().all()
        else:
            # Для остальных статусов - только заказы своей группы
            orders = await get_orders_by_status_for_supervisor(session, supervisor_id=current_user.telegram_id, statuses=statuses)
    # Логика для Админа и Владельца
    elif (current_user and current_user.role == UserRole.admin) or callback.from_user.id == config.admin_id:
        stmt = select(Order).where(Order.status.in_(statuses)).order_by(Order.created_at.desc())
        result = await session.execute(stmt)
        orders = result.scalars().all()

    if not orders:
        await callback.answer(f"{title} отсутствуют.", show_alert=True)
        return

    text = f"<b>{title}:</b>"
    reply_markup = get_orders_list_keyboard(orders, list_type)

    await callback.message.edit_text(text, reply_markup=reply_markup)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_view_order:"))
async def view_order_admin(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает администратору детальную карточку заказа с историей действий."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_details_for_admin(session, order_id)

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    title_text = callback.message.text.split('\n')[0]
    status_map_reverse = {
        "🆕 Новые заказы": "new",
        "⏳ Заказы в работе": "in_progress",
        "✅ Завершенные заказы": "completed",
        "❌ Отмененные заказы": "cancelled"
    }
    list_type = status_map_reverse.get(title_text.strip("<b>:</b>"), "new")

    client_info = "Не найден"
    if order.client:
        identifier = f"@{order.client.username}" if order.client.username else f"ID: {order.client.telegram_id}"
        client_info = f"{order.client.name} ({identifier})"

    executor_info = "Не назначен"
    if order.executor:
        identifier = f"@{order.executor.username}" if order.executor.username else f"ID: {order.executor.telegram_id}"
        executor_info = f"{order.executor.name} ({identifier})"

    services_list = []
    for item in order.items:
        service_name = ADDITIONAL_SERVICES.get(item.service_key, "Неизвестная услуга")
        if "шт" in service_name and item.quantity > 1:
            services_list.append(f"  - {service_name} (x{item.quantity})")
        else:
            services_list.append(f"  - {service_name}")
    services_text = "\n".join(services_list) or "Нет"

    # --- НОВЫЙ БЛОК: Формирование истории заказа ---
    logs_list = []
    if order.logs:
        for log in sorted(order.logs, key=lambda x: x.timestamp):
            logs_list.append(f"  - {log.timestamp.strftime('%d.%m %H:%M')}: {log.message}")
    logs_text = "\n".join(logs_list) or "Нет записей"


    order_details_text = await _get_order_details_text(order)

    reply_markup = get_view_order_keyboard_admin(order, list_type)
    await callback.message.edit_text(order_details_text, reply_markup=reply_markup)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_assign_executor:"))
async def assign_executor_start(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext):
    """Начинает процесс ручного назначения исполнителя."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    # Находим подходящих исполнителей
    executors = await get_matching_executors(session, order.selected_date, order.selected_time)
    if not executors:
        await callback.answer("Подходящих исполнителей не найдено.", show_alert=True)
        return

    # Сохраняем найденных исполнителей в состояние для пагинации
    await state.set_state(AdminOrderStates.assigning_executor)
    await state.update_data(executors_to_assign=executors)

    await callback.message.edit_text(
        f"👤 <b>Выберите исполнителя для заказа №{order_id}:</b>",
        reply_markup=get_assign_executor_keyboard(executors, order_id, page=0)
    )
    await callback.answer()


@router.callback_query(AdminOrderStates.assigning_executor, F.data.startswith("admin_assign_page:"))
async def assign_executor_page(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает переключение страниц в списке исполнителей."""
    _, order_id_str, page_str = callback.data.split(":")
    order_id = int(order_id_str)
    page = int(page_str)

    user_data = await state.get_data()
    executors = user_data.get("executors_to_assign", [])

    await callback.message.edit_reply_markup(
        reply_markup=get_assign_executor_keyboard(executors, order_id, page=page)
    )
    await callback.answer()


@router.callback_query(AdminOrderStates.assigning_executor, F.data.startswith("admin_confirm_assign:"))
async def assign_executor_confirm(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext):
    """Запрашивает подтверждение назначения."""
    _, order_id_str, executor_id_str = callback.data.split(":")
    order_id = int(order_id_str)
    executor_id = int(executor_id_str)

    executor_result = await session.execute(select(User).where(User.telegram_id == executor_id))
    executor = executor_result.scalar_one_or_none()

    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    # Создаем клавиатуру с подтверждением
    confirm_kb = InlineKeyboardBuilder()
    confirm_kb.button(text=f"✅ Да, назначить {executor.name}", callback_data=f"admin_do_assign:{order_id}:{executor_id}")
    confirm_kb.button(text="⬅️ Вернуться к выбору", callback_data=f"admin_assign_executor:{order_id}")
    confirm_kb.adjust(1)


    await callback.message.edit_text(
        f"Вы уверены, что хотите назначить <b>{executor.name}</b> на заказ №{order_id}?",
        reply_markup=confirm_kb.as_markup()
    )
    await callback.answer()


@router.callback_query(AdminOrderStates.assigning_executor, F.data.startswith("admin_do_assign:"))
async def assign_executor_finish(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bots: dict, config: Settings):
    """Окончательно назначает исполнителя и отправляет уведомления."""
    _, order_id_str, executor_id_str = callback.data.split(":")
    order_id = int(order_id_str)
    executor_id = int(executor_id_str)

    order = await get_order_by_id(session, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    payment = calculate_executor_payment(
        total_price=order.total_price,
        commission_type=config.system.commission_type,
        commission_value=config.system.commission_value
    )

    assigned_order = await assign_executor_to_order(session, order_id, executor_id, payment)

    if assigned_order:
        session.add(OrderLog(order_id=order_id, message=f"👤 Администратор @{callback.from_user.username} назначил исполнителя"))
        await session.commit()

        await callback.answer("Исполнитель успешно назначен!", show_alert=True)
        client_bot = bots.get("client")
        executor_bot = bots.get("executor")
        try:
            await client_bot.send_message(
                assigned_order.client_tg_id,
                f"🤝 Отличные новости! На ваш заказ №{order.id} назначен исполнитель."
            )
            await executor_bot.send_message(
                executor_id,
                f"✅ Администратор назначил вас на заказ №{order.id}. Он перемещен в раздел 'Мои заказы'."
            )
        except Exception as e:
            await callback.message.answer(f"Не удалось отправить уведомление: {e}")

        await state.clear()
        await view_order_admin(callback, session)
    else:
        await callback.answer("Не удалось назначить исполнителя. Возможно, заказ уже был принят.", show_alert=True)

@router.callback_query(F.data.startswith("admin_reassign_executor:"))
async def reassign_executor_start_handler(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bots: dict):
    """Начинает процесс переназначения, снимая текущего исполнителя."""
    order_id = int(callback.data.split(":")[1])

    order_for_client_id = await get_order_by_id(session, order_id)
    if not order_for_client_id:
        await callback.answer("Не удалось найти заказ.", show_alert=True)
        return
    client_tg_id = order_for_client_id.client_tg_id


    unassigned_order, previous_executor_id = await unassign_executor_from_order(session, order_id)

    if not unassigned_order:
        await callback.answer("Не удалось обновить заказ.", show_alert=True)
        return

    try:
        await bots["client"].send_message(
            client_tg_id,
            f"🔄 <b>Происходит замена исполнителя по вашему заказу №{order_id}.</b>\n\n"
            "Мы подбираем для вас нового специалиста и скоро пришлем уведомление о назначении."
        )
    except Exception as e:
        logging.warning(f"Не удалось уведомить клиента {client_tg_id} о переназначении исполнителя: {e}")

    # Добавляем лог о действии админа
    session.add(OrderLog(order_id=order_id, message=f"👤 Администратор @{callback.from_user.username} снял исполнителя с заказа"))
    await session.commit()


    # Уведомляем старого исполнителя, если он был
    if previous_executor_id:
        try:
            await bots["executor"].send_message(
                previous_executor_id,
                f"❗️ Администратор снял вас с заказа №{order_id}."
            )
        except Exception as e:
            logging.warning(f"Не удалось уведомить исполнителя {previous_executor_id} о снятии с заказа: {e}")

    # Сразу же запускаем процесс назначения нового
    await assign_executor_start(callback, session, state)

@router.callback_query(F.data.startswith("admin_cancel_order:"))
async def cancel_order_admin_handler(callback: types.CallbackQuery, session: AsyncSession, bots: dict):
    """Обрабатывает отмену заказа со стороны администратора."""
    order_id = int(callback.data.split(":")[1])

    order_to_cancel = await get_order_by_id(session, order_id)
    if not order_to_cancel:
        await callback.answer("Не удалось найти заказ.", show_alert=True)
        return

    # Запоминаем ID исполнителя и клиента до того, как обновить заказ
    executor_id_to_notify = order_to_cancel.executor_tg_id
    client_id_to_notify = order_to_cancel.client_tg_id

    # Меняем статус в БД
    updated_order = await update_order_status(session, order_id, OrderStatus.cancelled)

    if updated_order:
        # Добавляем лог о действии админа
        session.add(OrderLog(order_id=order_id, message=f"👤 Администратор @{callback.from_user.username} отменил заказ"))
        await session.commit()

        await callback.answer("Заказ отменен.", show_alert=True)

        # Обновляем карточку заказа, чтобы показать новый статус
        await view_order_admin(callback, session)

        # Уведомляем клиента
        try:
            await bots["client"].send_message(
                client_id_to_notify,
                f"❗️ Ваш заказ №{order_id} был отменен администратором."
            )
        except Exception as e:
            logging.warning(f"Не удалось уведомить клиента {client_id_to_notify} об отмене заказа {order_id}: {e}")

        # Уведомляем исполнителя, если он был назначен
        if executor_id_to_notify:
            try:
                await bots["executor"].send_message(
                    executor_id_to_notify,
                    f"❗️ Администратор отменил заказ №{order_id}, который был на вас назначен."
                )
            except Exception as e:
                logging.warning(f"Не удалось уведомить исполнителя {executor_id_to_notify} об отмене заказа {order_id}: {e}")
    else:
        await callback.answer("Не удалось обновить статус заказа.", show_alert=True)

@router.callback_query(F.data.startswith("admin_edit_order:"))
async def edit_order_start_admin(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс редактирования заказа для администратора."""
    order_id = int(callback.data.split(":")[1])
    await state.set_state(AdminOrderStates.editing_order)

    # Получаем list_type из текста сообщения, чтобы кнопка "Назад" работала корректно
    title_text = callback.message.text.split('\n')[0]
    status_map_reverse = {
        "🆕 Новые заказы": "new",
        "⏳ Заказы в работе": "in_progress",
        "✅ Завершенные заказы": "completed",
        "❌ Отмененные заказы": "cancelled"
    }
    list_type = status_map_reverse.get(title_text.strip("<b>:</b>"), "new")
    await state.update_data(order_id_to_edit=order_id, list_type=list_type)


    await callback.message.edit_text(
        f"Вы редактируете заказ №{order_id}. Что вы хотите изменить?",
        reply_markup=get_admin_edit_order_keyboard(order_id, list_type)
    )
    await callback.answer()

@router.callback_query(AdminOrderStates.editing_order, F.data.startswith("admin_edit_services:"))
async def edit_services_start_admin(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает процесс изменения доп. услуг для заказа со стороны админа."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)
    if not order:
        await callback.answer("Не удалось найти заказ.", show_alert=True)
        return

    # Рассчитываем базовую стоимость (без доп. услуг), чтобы потом к ней прибавлять новые
    preliminary_cost = calculate_preliminary_cost(
        cleaning_type=order.cleaning_type,
        room_count_str=order.room_count,
        bathroom_count_str=order.bathroom_count
    )

    # Получаем уже выбранные услуги из базы
    selected_services = {item.service_key: item.quantity for item in order.items}

    # Сохраняем все нужные данные в состояние
    await state.set_state(AdminOrderStates.editing_additional_services)
    await state.update_data(
        order_id_to_edit=order_id,
        preliminary_cost=preliminary_cost,
        selected_services=selected_services
    )

    await callback.message.edit_text(
        f"Редактирование доп. услуг для заказа №{order_id}.\n"
        f"Текущая стоимость: <b>{order.total_price} ₽</b>\n\n"
        "Выберите или измените состав услуг:",
        reply_markup=get_additional_services_keyboard(selected_services)
    )
    await callback.answer()


async def update_admin_services_message(bot: Bot, chat_id: int, message_id: int, state: FSMContext):
    """(Админ-панель) Пересчитывает стоимость и обновляет сообщение с доп. услугами."""
    user_data = await state.get_data()
    selected_services = user_data.get("selected_services", {})
    preliminary_cost = user_data.get("preliminary_cost", 0)

    total_cost = calculate_total_cost(preliminary_cost, selected_services)
    await state.update_data(total_cost=total_cost)

    try:
        await bot.edit_message_text(
            text=f"Новая итоговая стоимость: <b>{total_cost} ₽</b>.\n\n"
                 f"Выберите дополнительные услуги или нажмите 'Готово'.",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=get_additional_services_keyboard(selected_services)
        )
    except TelegramBadRequest:
        # Игнорируем ошибку, если сообщение не изменилось
        pass


@router.callback_query(
    AdminOrderStates.editing_additional_services,
    F.data.startswith("add_service_")
)
async def handle_add_service_admin(callback: types.CallbackQuery, state: FSMContext):
    """(Админ-панель) Обрабатывает выбор доп. услуги."""
    service_key = callback.data.split("_")[-1]
    user_data = await state.get_data()
    selected_services = user_data.get("selected_services", {}).copy()

    # Простое включение/выключение услуги (пока без запроса количества)
    if service_key in selected_services:
        del selected_services[service_key]
    else:
        selected_services[service_key] = 1

    await state.update_data(selected_services=selected_services)
    await update_admin_services_message(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        state=state
    )
    await callback.answer()


@router.callback_query(
    AdminOrderStates.editing_additional_services,
    F.data == "done_services"
)
async def done_additional_services_admin(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession,
                                         bots: dict, config: Settings):
    """(Админ-панель) Завершает выбор доп. услуг, сохраняет изменения и уведомляет стороны."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")
    new_services = user_data.get("selected_services", {})
    new_price = user_data.get("total_cost")

    if not order_id or new_price is None:
        await callback.answer("Произошла ошибка, не найден ID заказа или цена.", show_alert=True)
        return

    updated_order = await update_order_services_and_price(
        session, order_id, new_services, new_price,
        admin_id=callback.from_user.id,
        admin_username=callback.from_user.username or "admin"
    )

    if not updated_order:
        await callback.answer("Не удалось обновить заказ в базе данных.", show_alert=True)
        return

    await callback.answer(f"Услуги и цена для заказа №{order_id} обновлены!", show_alert=True)

    await bots["admin"].send_message(
        config.admin_id,
        f"✅ <b>Администратор @{callback.from_user.username} изменил доп. услуги в заказе №{order_id}.</b>\n"
        f"Новая стоимость: {new_price} ₽"
    )

    try:
        await bots["client"].send_message(
            updated_order.client_tg_id,
            f"❗️ Администратор изменил состав услуг в вашем заказе №{order_id}.\n"
            f"Новая итоговая стоимость: <b>{new_price} ₽</b>"
        )
    except Exception as e:
        logging.warning(
            f"Не удалось уведомить клиента {updated_order.client_tg_id} об изменении заказа {order_id}: {e}")

    if updated_order.executor_tg_id:
        await update_order_status(session, order_id, OrderStatus.pending_confirmation)
        try:
            new_executor_payment = calculate_executor_payment(
                total_price=new_price,
                commission_type=config.system.commission_type,
                commission_value=config.system.commission_value
            )
            await bots["executor"].send_message(
                chat_id=updated_order.executor_tg_id,
                text=(
                    f"❗️ <b>Администратор изменил доп. услуги в заказе №{order_id}.</b>\n"
                    f"Новая выплата: {new_executor_payment} ₽\n\n"
                    "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
                ),
                reply_markup=get_order_changes_confirmation_keyboard(order_id)
            )
        except Exception as e:
            logging.error(
                f"Не удалось отправить уведомление исполнителю {updated_order.executor_tg_id} об изменении заказа {order_id}: {e}")

    await callback.message.delete()
    list_type = user_data.get("list_type", "new")
    order_details_text = await _get_order_details_text(updated_order)
    reply_markup = get_view_order_keyboard_admin(updated_order, list_type)
    await callback.message.answer(order_details_text, reply_markup=reply_markup)

    await state.clear()

@router.callback_query(AdminOrderStates.editing_order, F.data.startswith("admin_edit_datetime:"))
async def edit_datetime_start_admin(callback: types.CallbackQuery, state: FSMContext):
    """(Админ-панель) Начинает процесс изменения даты и времени для заказа."""
    await callback.message.delete() # Удаляем старое сообщение с меню
    now = datetime.datetime.now()
    await callback.message.answer(
        "Пожалуйста, выберите новую дату для этого заказа:",
        reply_markup=await create_calendar(now.year, now.month)
    )
    await state.set_state(AdminOrderStates.editing_date)
    await callback.answer()

@router.callback_query(AdminOrderStates.editing_date, F.data.startswith("month_nav:"))
async def process_calendar_navigation_admin(callback: types.CallbackQuery):
    """(Админ-панель) Обрабатывает навигацию по календарю."""
    try:
        _, direction, year_str, month_str = callback.data.split(":")
        year, month = int(year_str), int(month_str)

        if direction == "next":
            month += 1
            if month > 12:
                month = 1; year += 1
        elif direction == "prev":
            month -= 1
            if month < 1:
                month = 12; year -= 1

        await callback.message.edit_reply_markup(reply_markup=await create_calendar(year, month))
    except (ValueError, TelegramBadRequest) as e:
        logging.error(f"Ошибка при навигации по календарю админом: {e}")
    finally:
        await callback.answer()


@router.callback_query(AdminOrderStates.editing_date, F.data.startswith("day:"))
async def process_date_selection_admin(callback: types.CallbackQuery, state: FSMContext):
    """(Админ-панель) Обрабатывает выбор даты и показывает клавиатуру времени."""
    date_str = callback.data.split(":")[1]
    await state.update_data(new_date=date_str)
    await callback.message.delete()
    await callback.message.answer(
        f"Вы выбрали новую дату: {date_str}. Теперь выберите временной интервал:",
        reply_markup=get_time_keyboard(["9:00 - 12:00", "12:00 - 15:00", "15:00 - 18:00", "18:00 - 21:00"])
    )
    await state.set_state(AdminOrderStates.editing_time)
    await callback.answer()


@router.message(AdminOrderStates.editing_time, F.text)
async def handle_time_selection_admin(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """(Админ-панель) Сохраняет время, обновляет заказ и отправляет уведомления."""
    await state.update_data(new_time=message.text)
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")
    new_date = user_data.get("new_date")
    new_time = user_data.get("new_time")

    updated_order = await update_order_datetime(
        session, order_id, new_date, new_time,
        admin_id=message.from_user.id,
        admin_username=message.from_user.username or "admin"
    )

    if not updated_order:
        await message.answer("Произошла ошибка при обновлении заказа.", reply_markup=get_admin_main_keyboard())
        await state.clear()
        return

    # Красиво форматируем дату для уведомлений
    try:
        selected_date = datetime.datetime.strptime(new_date, "%Y-%m-%d")
        formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
    except (ValueError, KeyError, TypeError):
        formatted_date = new_date

    await message.answer(
        f"✅ Дата и время для заказа №{order_id} успешно изменены!",
        reply_markup=get_admin_main_keyboard() # Возвращаем основную клавиатуру
    )

    # Уведомляем админа, клиента и исполнителя
    await bots["admin"].send_message(
        config.admin_id,
        f"✅ Администратор @{message.from_user.username} изменил дату/время в заказе №{order_id}.\n"
        f"Новая дата: {formatted_date}, новое время: {new_time}"
    )
    await bots["client"].send_message(
        updated_order.client_tg_id,
        f"❗️ Администратор изменил дату/время в вашем заказе №{order_id}.\n"
        f"Новая дата: <b>{formatted_date}</b>\nНовое время: <b>{new_time}</b>"
    )

    if updated_order.executor_tg_id:
        await update_order_status(session, order_id, OrderStatus.pending_confirmation)
        await bots["executor"].send_message(
            chat_id=updated_order.executor_tg_id,
            text=(
                f"❗️ <b>Администратор изменил дату/время в заказе №{order_id}.</b>\n"
                f"Новая дата: {formatted_date}\nНовое время: {new_time}\n\n"
                "Пожалуйста, подтвердите, что вы готовы выполнить заказ в это время."
            ),
            reply_markup=get_order_changes_confirmation_keyboard(order_id)
        )

    await state.clear()

@router.callback_query(AdminOrderStates.editing_order, F.data.startswith("admin_edit_address:"))
async def edit_address_start_admin(callback: types.CallbackQuery, state: FSMContext):
    """(Админ-панель) Начинает процесс изменения адреса."""
    await callback.message.delete()
    await callback.message.answer(
        "Пожалуйста, введите новый адрес или отправьте геолокацию.",
        reply_markup=get_address_keyboard()
    )
    await state.set_state(AdminOrderStates.editing_address)
    await callback.answer()


@router.message(AdminOrderStates.editing_address, F.location)
async def handle_address_location_admin(message: types.Message, state: FSMContext, config: Settings):
    """(Админ-панель) Обрабатывает геолокацию, получает адрес и просит подтверждения."""
    lat, lon = message.location.latitude, message.location.longitude
    address_text = await get_address_from_coords(lat, lon, config.api_keys.yandex_api_key)

    if address_text:
        await state.update_data(new_address_lat=lat, new_address_lon=lon, new_address_text=address_text)
        await message.answer(
            f"Определен адрес: <b>{address_text}</b>.\nВсе верно?",
            reply_markup=get_address_confirmation_keyboard()
        )
        await state.set_state(AdminOrderStates.confirming_edited_address)
    else:
        await message.answer("Не удалось определить адрес. Пожалуйста, введите его вручную.")


@router.message(AdminOrderStates.editing_address, F.text)
async def handle_address_text_admin(message: types.Message, state: FSMContext, config: Settings):
    """(Админ-панель) Обрабатывает текстовый адрес, проверяет его и просит подтверждения."""
    validated_address = await get_address_from_text(message.text, config.api_keys.yandex_api_key)
    if validated_address:
        await state.update_data(new_address_text=validated_address, new_address_lat=None, new_address_lon=None)
        await message.answer(
            f"Мы уточнили адрес: <b>{validated_address}</b>.\nВсе верно?",
            reply_markup=get_address_confirmation_keyboard()
        )
        await state.set_state(AdminOrderStates.confirming_edited_address)
    else:
        await message.answer("Не удалось найти такой адрес. Попробуйте еще раз или отправьте геолокацию.")


@router.message(AdminOrderStates.confirming_edited_address, F.text == "✅ Да, все верно")
async def handle_address_confirmation_admin(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """(Админ-панель) Сохраняет новый адрес и уведомляет стороны."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")
    new_address = user_data.get("new_address_text")
    new_lat = user_data.get("new_address_lat")
    new_lon = user_data.get("new_address_lon")

    updated_order = await update_order_address(
        session, order_id, new_address, new_lat, new_lon,
        admin_id=message.from_user.id,
        admin_username=message.from_user.username or "admin"
    )

    if not updated_order:
        await message.answer("Произошла ошибка при обновлении заказа.", reply_markup=get_admin_main_keyboard())
        await state.clear()
        return

    await message.answer(
        f"✅ Адрес для заказа №{order_id} успешно изменен!",
        reply_markup=get_admin_main_keyboard()
    )

    # Уведомляем всех
    await bots["admin"].send_message(
        config.admin_id,
        f"✅ Администратор @{message.from_user.username} изменил адрес в заказе №{order_id}.\n"
        f"Новый адрес: {new_address}"
    )
    await bots["client"].send_message(
        updated_order.client_tg_id,
        f"❗️ Администратор изменил адрес в вашем заказе №{order_id}.\n"
        f"Новый адрес: <b>{new_address}</b>"
    )

    if updated_order.executor_tg_id:
        await update_order_status(session, order_id, OrderStatus.pending_confirmation)
        await bots["executor"].send_message(
            chat_id=updated_order.executor_tg_id,
            text=(
                f"❗️ <b>Администратор изменил адрес в заказе №{order_id}.</b>\n"
                f"Новый адрес: {new_address}\n\n"
                "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
            ),
            reply_markup=get_order_changes_confirmation_keyboard(order_id)
        )

    await state.clear()


@router.callback_query(AdminOrderStates.editing_order, F.data.startswith("admin_edit_rooms:"))
async def edit_rooms_start_admin(callback: types.CallbackQuery, state: FSMContext):
    """(Админ-панель) Начинает процесс изменения комнат/санузлов."""
    await callback.message.delete()
    await callback.message.answer(
        "Пожалуйста, выберите новое количество комнат:",
        reply_markup=get_room_count_keyboard()
    )
    await state.set_state(AdminOrderStates.editing_room_count)
    await callback.answer()


@router.message(AdminOrderStates.editing_room_count, F.text.in_({"1", "2", "3", "4", "5+", "⬅️ Назад"}))
async def handle_edit_room_count_admin(message: types.Message, state: FSMContext):
    """(Админ-панель) Обрабатывает выбор нового количества комнат."""
    if message.text == "⬅️ Назад":
        user_data = await state.get_data()
        order_id = user_data.get("order_id_to_edit")
        # Возвращаемся в меню редактирования
        await message.answer(
            f"Вы редактируете заказ №{order_id}. Что вы хотите изменить?",
            reply_markup=get_admin_edit_order_keyboard(order_id, "unknown")
        )
        await state.set_state(AdminOrderStates.editing_order)
        return

    await state.update_data(new_room_count=message.text)
    await message.answer(
        "Отлично. Теперь выберите новое количество санузлов:",
        reply_markup=get_bathroom_count_keyboard()
    )
    await state.set_state(AdminOrderStates.editing_bathroom_count)


@router.message(AdminOrderStates.editing_bathroom_count, F.text.in_({"1", "2", "3+", "⬅️ Назад"}))
async def handle_edit_bathroom_count_admin(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """(Админ-панель) Сохраняет санузлы, пересчитывает стоимость и обновляет заказ."""
    if message.text == "⬅️ Назад":
        await message.answer(
            "Возвращаемся к выбору комнат. Пожалуйста, выберите новое количество комнат:",
            reply_markup=get_room_count_keyboard()
        )
        await state.set_state(AdminOrderStates.editing_room_count)
        return

    await state.update_data(new_bathroom_count=message.text)
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")
    new_room_count = user_data.get("new_room_count")
    new_bathroom_count = user_data.get("new_bathroom_count")

    order = await get_order_by_id(session, order_id)

    if not order:
        await message.answer("Ошибка, заказ не найден.", reply_markup=get_admin_main_keyboard())
        await state.clear()
        return

    new_preliminary_cost = calculate_preliminary_cost(
        cleaning_type=order.cleaning_type,
        room_count_str=new_room_count,
        bathroom_count_str=new_bathroom_count
    )

    selected_services = {item.service_key: item.quantity for item in order.items}
    new_total_price = calculate_total_cost(new_preliminary_cost, selected_services)

    updated_order = await update_order_rooms_and_price(
        session, order_id=order_id,
        new_room_count=new_room_count,
        new_bathroom_count=new_bathroom_count,
        new_total_price=new_total_price,
        admin_id=message.from_user.id,
        admin_username=message.from_user.username or "admin"
    )

    if updated_order:
        await message.answer(
            f"✅ Параметры заказа №{order_id} обновлены!",
            reply_markup=get_admin_main_keyboard()
        )

        await bots["admin"].send_message(
            config.admin_id,
            f"✅ Администратор @{message.from_user.username} изменил параметры в заказе №{order_id}.\n"
            f"Новые параметры: {new_room_count} ком., {new_bathroom_count} с/у.\n"
            f"Новая стоимость: {new_total_price} ₽"
        )
        await bots["client"].send_message(
            updated_order.client_tg_id,
            f"❗️ Администратор изменил параметры в вашем заказе №{order_id}.\n"
            f"Новые параметры: <b>{new_room_count} ком., {new_bathroom_count} с/у</b>.\n"
            f"Новая итоговая стоимость: <b>{new_total_price} ₽</b>"
        )
        if updated_order.executor_tg_id:
            await update_order_status(session, order_id, OrderStatus.pending_confirmation)
            new_executor_payment = calculate_executor_payment(
                total_price=new_total_price,
                commission_type=config.system.commission_type,
                commission_value=config.system.commission_value
            )
            await bots["executor"].send_message(
                chat_id=updated_order.executor_tg_id,
                text=(
                    f"❗️ <b>Администратор изменил параметры в заказе №{order_id}.</b>\n"
                    f"Новые параметры: {new_room_count} ком., {new_bathroom_count} с/у.\n"
                    f"Новая выплата: {new_executor_payment} ₽\n\n"
                    "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
                ),
                reply_markup=get_order_changes_confirmation_keyboard(order_id)
            )
    else:
        await message.answer("Произошла ошибка при обновлении заказа.", reply_markup=get_admin_main_keyboard())

    await state.clear()

@router.callback_query(AdminOrderStates.assigning_executor, F.data.startswith("admin_view_executor:"))
async def view_executor_admin_from_assigning(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, config: Settings):
    """Показывает администратору детальную карточку исполнителя."""
    executor_id = int(callback.data.split(":")[1])
    executor = await get_user(session, executor_id)

    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    page = 0
    if callback.message.reply_markup:
        for row in callback.message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data.startswith("admin_executors_page:"):
                    try:
                        page = int(button.callback_data.split(":")[1])
                        break
                    except (ValueError, IndexError):
                        continue
            if page:
                break

    current_user = await get_user(session, callback.from_user.id)
    supervisor = None
    if executor.supervisor_id:
        supervisor = await get_user(session, executor.supervisor_id)

    status_text = "Активен ✅" if executor.status == UserStatus.active else f"Заблокирован до {executor.blocked_until.strftime('%d.%m %H:%M')}"
    supervisor_info = f"@{supervisor.username}" if supervisor and supervisor.username else supervisor.telegram_id if supervisor else "Не назначен"

    # Формируем строку с юзернеймом, если он есть
    username_info = f"<b>Username:</b> @{executor.username}\n" if executor.username else ""

    executor_details = (
        f"<b>Имя:</b> {executor.name}\n"
        f"{username_info}"
        f"<b>ID:</b> <code>{executor.telegram_id}</code>\n"
        f"<b>Телефон:</b> {executor.phone}\n\n"
        f"<b>Статус:</b> {status_text}\n"
        f"<b>Приоритет:</b> {executor.priority}\n\n"
        f"<b>Рейтинг:</b> {executor.average_rating} ⭐ ({executor.review_count} оценок)\n"
        f"<b>Баланс (реф.):</b> {executor.referral_balance} ₽\n"
        f"<b>Приглашено:</b> {executor.referrals_count} чел."
    )

    await callback.message.edit_text(
        executor_details,
        reply_markup=get_view_executor_keyboard_admin(
            executor=executor,
            page=page,
            current_user=current_user,
            supervisor=supervisor,
            owner_id=config.admin_id
        )
    )
    await callback.answer()

@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_block_executor:"))
async def block_executor_confirm_handler(callback: types.CallbackQuery, session: AsyncSession):
    """Запрашивает подтверждение на блокировку исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)
    executor = await get_user(session, executor_id)
    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        f"Вы уверены, что хотите заблокировать исполнителя <b>{executor.name}</b>?\n\n"
        f"Он не сможет видеть новые заказы и входить в систему.",
        reply_markup=get_block_confirmation_keyboard(executor_id, page)
    )
    await callback.answer()


@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_confirm_block:"))
async def block_executor_finish_handler(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bots: dict):
    """Окончательно блокирует исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    blocked_executor = await block_executor_by_admin(session, executor_id)

    if blocked_executor:
        await callback.answer("Исполнитель заблокирован.", show_alert=True)
        try:
            await bots["executor"].send_message(
                executor_id,
                "❗️ Ваш аккаунт был заблокирован администратором."
            )
        except Exception as e:
            logging.warning(f"Не удалось уведомить исполнителя {executor_id} о блокировке: {e}")

        # Обновляем список исполнителей и возвращаемся к нему
        executors = await get_all_executors(session)
        await state.update_data(executors_list=executors)
        await callback.message.edit_text(
            "📋 <b>Список исполнителей:</b>",
            reply_markup=get_executors_list_keyboard(executors, page=page)
        )


@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_unblock_executor:"))
async def unblock_executor_handler(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bots: dict):
    """Активирует (разблокирует) исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)
    unblocked_executor = await unblock_executor_by_admin(session, executor_id)

    if unblocked_executor:
        await callback.answer("Исполнитель активирован.", show_alert=True)
        try:
            await bots["executor"].send_message(
                executor_id,
                "✅ Ваш аккаунт был активирован администратором. Вы снова можете принимать заказы."
            )
        except Exception as e:
            logging.warning(f"Не удалось уведомить исполнителя {executor_id} о разблокировке: {e}")

        # Обновляем список исполнителей и возвращаемся к нему
        executors = await get_all_executors(session)
        await state.update_data(executors_list=executors)

        await callback.message.edit_text(
            "📋 <b>Список исполнителей:</b>",
            reply_markup=get_executors_list_keyboard(executors, page=page)
        )
    else:
        await callback.answer("Не удалось активировать исполнителя.", show_alert=True)

@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_executor_stats:"))
async def view_executor_stats_admin(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает статистику по конкретному исполнителю."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    executor = await get_user(session, executor_id)
    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    stats = await get_executor_statistics(session, executor_id)

    stats_text = (
        f"<b>📊 Статистика исполнителя: {executor.name}</b>\n\n"
        f"<b>Рейтинг:</b> {executor.average_rating} ⭐ ({executor.review_count} оценок)\n\n"
        f"✅ <b>Завершено заказов:</b> {stats['completed_count']}\n"
        f"💰 <b>Заработано (всего):</b> {stats['total_earnings']:.2f} ₽\n\n"
        f"⏳ <b>Сейчас в работе:</b> {stats['in_progress_count']}\n"
        f"❌ <b>Отменено заказов:</b> {stats['cancelled_count']}\n"
    )

    # Создаем простую клавиатуру для возврата назад
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к профилю", callback_data=f"admin_view_executor:{executor_id}:{page}")
    reply_markup = builder.as_markup()

    await callback.message.edit_text(stats_text, reply_markup=reply_markup)
    await callback.answer()

@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_edit_priority:"))
async def edit_priority_start(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает процесс изменения приоритета исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    executor = await get_user(session, executor_id)
    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    await state.set_state(AdminExecutorStates.editing_priority)
    await state.update_data(
        executor_id_to_edit=executor_id,
        page=page,
        message_to_delete_id=callback.message.message_id
    )

    await callback.message.edit_text(
        f"Текущий приоритет для <b>{executor.name}</b>: <code>{executor.priority}</code>\n\n"
        "Введите новое числовое значение приоритета. Чем выше число, тем выше приоритет при автоназначении заказов."
    )
    await callback.answer()


@router.message(AdminExecutorStates.editing_priority, F.text)
async def edit_priority_finish(message: types.Message, state: FSMContext, session: AsyncSession, config: Settings):
    """Завершает процесс изменения приоритета."""
    try:
        new_priority = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректное целое число.")
        return

    user_data = await state.get_data()
    executor_id = user_data.get("executor_id_to_edit")
    page = user_data.get("page", 0)
    message_to_delete_id = user_data.get("message_to_delete_id")

    updated_executor = await update_executor_priority(session, executor_id, new_priority)

    if updated_executor:
        # Сначала удаляем все промежуточные сообщения
        await message.delete()
        if message_to_delete_id:
            with suppress(TelegramBadRequest):
                await message.bot.delete_message(message.chat.id, message_to_delete_id)

        await message.answer(f"✅ Приоритет для {updated_executor.name} успешно изменен на {new_priority}.")

        # Получаем все данные, необходимые для клавиатуры
        current_user = await get_user(session, message.from_user.id)
        supervisor = None
        if updated_executor.supervisor_id:
            supervisor = await get_user(session, updated_executor.supervisor_id)

        status_text = "Активен ✅" if updated_executor.status == UserStatus.active else "Заблокирован ❌"
        if updated_executor.status == UserStatus.blocked and updated_executor.blocked_until:
            status_text = f"Заблокирован до {updated_executor.blocked_until.strftime('%d.%m %H:%M')} ❌"

        supervisor_info = f"@{supervisor.username}" if supervisor and supervisor.username else supervisor.telegram_id if supervisor else "Не назначен"

        executor_details = (
            f"<b>🛠️ Профиль исполнителя</b>\n\n"
            f"<b>Имя:</b> {updated_executor.name}\n"
            f"<b>ID:</b> <code>{updated_executor.telegram_id}</code>\n"
            f"<b>Телефон:</b> {updated_executor.phone}\n\n"
            f"<b>Статус:</b> {status_text}\n"
            f"<b>Приоритет:</b> {updated_executor.priority}\n"
            f"<b>Супервайзер:</b> {supervisor_info}\n\n"
            f"<b>Рейтинг:</b> {updated_executor.average_rating} ⭐ ({updated_executor.review_count} оценок)\n"
            f"<b>Баланс (реф.):</b> {updated_executor.referral_balance} ₽\n"
            f"<b>Приглашено:</b> {updated_executor.referrals_count} чел."
        )

        await message.answer(
            executor_details,
            reply_markup=get_view_executor_keyboard_admin(
                executor=updated_executor,
                page=page,
                current_user=current_user,
                supervisor=supervisor,
                owner_id=config.admin_id
            )
        )
    else:
        await message.answer("Не удалось обновить приоритет. Исполнитель не найден.")

    await state.clear()


# --- БЛОК УПРАВЛЕНИЯ РОЛЯМИ И ДОСТУПОМ ---

@router.callback_query(AdminExecutorStates.viewing_executors, F.data.startswith("admin_manage_access:"))
async def manage_access_menu(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Отображает меню управления доступом для пользователя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    executor = await get_user(session, executor_id)
    if not executor:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    await state.set_state(AdminExecutorStates.managing_access)
    await state.update_data(managed_user_id=executor_id, page=page)

    text = f"👑 <b>Управление доступом для: {executor.name}</b>\n\n" \
           f"Текущая роль: <code>{executor.role.value}</code>"

    await callback.message.edit_text(
        text,
        reply_markup=get_manage_access_keyboard(
            executor=executor,
            page=page,
            owner_id=config.admin_id,
            current_user_id=callback.from_user.id
        )
    )
    await callback.answer()


@router.callback_query(AdminExecutorStates.managing_access, F.data.startswith("admin_make_supervisor:"))
async def make_supervisor_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    updated_user = await update_user_role(session, executor_id, UserRole.supervisor)
    if updated_user:
        try:
            admin_bot_info = await bots["admin"].get_me()
            admin_username = admin_bot_info.username
            await bots["executor"].send_message(
                chat_id=executor_id,
                text=f"⬆️ Вам предоставлена роль Супервайзера.\n\n"
                     f"Для доступа к панели управления, пожалуйста, перейдите в бот: @{admin_username}"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление о назначении супервайзером пользователю {executor_id}: {e}")

        await callback.answer("✅ Роль успешно изменена на 'Супервайзер'.", show_alert=True)
        # Обновляем меню
        await manage_access_menu(callback, state, session, config)
    else:
        await callback.answer("❌ Не удалось обновить роль.", show_alert=True)


@router.callback_query(AdminExecutorStates.managing_access, F.data.startswith("admin_remove_supervisor:"))
async def remove_supervisor_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Возвращает пользователю роль исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)

    updated_user = await update_user_role(session, executor_id, UserRole.executor)
    if updated_user:
        await callback.answer("✅ Роль возвращена на 'Исполнитель'.", show_alert=True)
        await manage_access_menu(callback, state, session, config)
    else:
        await callback.answer("❌ Не удалось обновить роль.", show_alert=True)


@router.callback_query(AdminExecutorStates.managing_access, F.data.startswith("admin_assign_supervisor_start:"))
async def choose_supervisor_start(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Показывает список супервайзеров для назначения."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    supervisors = await get_all_supervisors(session)
    if not supervisors:
        await callback.answer("В системе нет ни одного супервайзера.", show_alert=True)
        return

    await state.set_state(AdminExecutorStates.choosing_supervisor)
    await callback.message.edit_text(
        "Выберите супервайзера из списка:",
        reply_markup=get_supervisors_list_keyboard(supervisors, executor_id, page)
    )
    await callback.answer()


@router.callback_query(AdminExecutorStates.choosing_supervisor, F.data.startswith("admin_assign_supervisor_finish:"))
async def assign_supervisor_finish(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """Завершает процесс назначения супервайзера."""
    _, executor_id_str, supervisor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    supervisor_id = int(supervisor_id_str)
    page = int(page_str)

    updated_executor = await assign_supervisor_to_executor(session, executor_id, supervisor_id)
    if updated_executor:
        # Уведомляем исполнителя о назначении
        try:
            supervisor = await get_user(session, supervisor_id)
            if supervisor:
                supervisor_mention = f"@{supervisor.username}" if supervisor.username else f"c ID {supervisor.telegram_id}"
                await bots["executor"].send_message(
                    chat_id=executor_id,
                    text=f"👨‍💼 Вам назначен супервайзер: {supervisor.name} ({supervisor_mention}).\n\n"
                         f"По рабочим вопросам вы можете обращаться к нему."
                )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление о назначении супервайзера исполнителю {executor_id}: {e}")

        await callback.answer("✅ Супервайзер успешно назначен.", show_alert=True)
        # Создаем "фейковый" callback, чтобы вернуться к карточке исполнителя
        fake_callback = types.CallbackQuery(
            id="fake_callback_back_to_profile",
            from_user=callback.from_user,
            chat_instance=callback.chat_instance,
            message=callback.message,
            data=f"admin_view_executor:{executor_id}:{page}"
        )
        await state.set_state(AdminExecutorStates.viewing_executors)
        await view_executor_admin(fake_callback, session, state, config)
    else:
        await callback.answer("❌ Не удалось назначить супервайзера.", show_alert=True)


@router.callback_query(AdminExecutorStates.managing_access, F.data.startswith("admin_unassign_supervisor:"))
async def unassign_supervisor_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Открепляет исполнителя от его текущего супервайзера."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)
    page = int(page_str)

    updated_executor = await assign_supervisor_to_executor(session, executor_id, None)  # Передаем None для снятия
    if updated_executor:
        await callback.answer("✅ Супервайзер откреплен.", show_alert=True)
        # Создаем "фейковый" callback, чтобы вернуться к карточке исполнителя
        fake_callback = types.CallbackQuery(
            id="fake_callback_back_to_profile_unassign",
            from_user=callback.from_user,
            chat_instance=callback.chat_instance,
            message=callback.message,
            data=f"admin_view_executor:{executor_id}:{page}"
        )
        await state.set_state(AdminExecutorStates.viewing_executors)
        await view_executor_admin(fake_callback, session, state, config)
    else:
        await callback.answer("❌ Не удалось открепить супервайзера.", show_alert=True)

@router.callback_query(F.data.startswith("admin_edit_payment:"))
async def edit_payment_start(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает процесс ручного изменения выплаты исполнителю."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order or not order.executor_payment:
        await callback.answer("Невозможно изменить выплату для этого заказа.", show_alert=True)
        return

    await state.set_state(AdminOrderStates.editing_executor_payment)
    await state.update_data(
        order_id_to_edit=order_id,
        message_to_edit_id=callback.message.message_id  # Сохраняем ID сообщения
    )

    await callback.message.edit_text(
        f"Текущая выплата исполнителю по заказу №{order_id}: <b>{order.executor_payment} ₽</b>.\n\n"
        f"Введите новую сумму выплаты (только число):"
    )
    await callback.answer()


@router.message(AdminOrderStates.editing_executor_payment, F.text)
async def edit_payment_finish(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict):
    """Завершает процесс изменения выплаты."""
    try:
        new_payment = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число.")
        return

    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")
    message_to_edit_id = user_data.get("message_to_edit_id")  # Это ID исходного сообщения-карточки

    updated_order = await update_executor_payment(
        session,
        order_id=order_id,
        new_payment=new_payment,
        admin_id=message.from_user.id,
        admin_username=message.from_user.username or "admin"
    )

    if updated_order:
        # Удаляем сообщение с просьбой ввести сумму и сообщение с самой суммой
        await message.delete()
        if message_to_edit_id:
            try:
                await message.bot.delete_message(message.chat.id, message_to_edit_id)
            except TelegramBadRequest:
                pass

        # Отправляем сообщение об успехе
        await message.answer(f"✅ Выплата для заказа №{order_id} успешно изменена на <b>{new_payment} ₽</b>.")

        # Уведомляем исполнителя
        try:
            await bots["executor"].send_message(
                chat_id=updated_order.executor_tg_id,
                text=f"💰 Администратор изменил вашу выплату по заказу №{order_id}.\n"
                     f"Новая сумма: <b>{new_payment} ₽</b>."
            )
        except Exception as e:
            logging.warning(f"Не удалось уведомить исполнителя {updated_order.executor_tg_id} об изменении выплаты: {e}")

        # Получаем свежие данные по заказу и отправляем новую карточку
        order_details_obj = await get_order_details_for_admin(session, order_id)
        if order_details_obj:
            order_details_text = await _get_order_details_text(order_details_obj)
            # Статус заказа не меняется, поэтому list_type можно условно определить или взять из состояния
            list_type = "in_progress" # Предполагаем, что меняем выплату у заказов в работе
            reply_markup = get_view_order_keyboard_admin(order_details_obj, list_type)
            await message.answer(order_details_text, reply_markup=reply_markup)

    else:
        await message.answer("Не удалось обновить выплату. Возможно, с заказа сняли исполнителя.")

    await state.clear()

@router.message(F.text == "📊 Статистика и отчеты")
async def reports_menu(message: types.Message):
    """Показывает меню для выбора отчета."""
    await message.answer(
        "Выберите период, за который вы хотите сформировать отчет по заказам:",
        reply_markup=get_report_period_keyboard()
    )


@router.callback_query(F.data.startswith("report:"))
async def generate_report(callback: types.CallbackQuery, session: AsyncSession):
    """Генерирует и отправляет отчет по заказам в формате Excel."""
    period = callback.data.split(":")[1]
    end_date = datetime.datetime.now()
    start_date = None

    if period == "today":
        start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = end_date - datetime.timedelta(days=7)
    elif period == "month":
        start_date = end_date - datetime.timedelta(days=30)
    elif period == "all_time":
        start_date = datetime.datetime.min

    if not start_date:
        await callback.answer("Некорректный период.", show_alert=True)
        return

    await callback.answer("Начал формировать отчет...")

    orders = await get_orders_for_report(session, start_date, end_date)

    if not orders:
        await callback.message.answer("За выбранный период нет заказов для отчета.")
        return

    # Создание Excel файла в памяти
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Отчет по заказам"

    # Заголовки
    headers = [
        "ID Заказа", "Дата создания", "Статус", "Клиент", "ID клиента",
        "Исполнитель", "ID исполнителя", "Адрес", "Сумма заказа", "Выплата исполнителю"
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    # Данные
    for order in orders:
        row = [
            order.id,
            order.created_at.strftime("%d.%m.%Y %H:%M"),
            STATUS_MAPPING.get(order.status, order.status.value),
            order.client.name if order.client else "N/A",
            order.client_tg_id,
            order.executor.name if order.executor else "Не назначен",
            order.executor_tg_id,
            order.address_text,
            order.total_price,
            order.executor_payment
        ]
        sheet.append(row)

    # Сохранение файла в байтовый поток
    file_stream = io.BytesIO()
    workbook.save(file_stream)
    file_stream.seek(0) # Перемещаем курсор в начало файла

    report_file = BufferedInputFile(file_stream.read(), filename=f"report_{period}_{end_date.strftime('%Y-%m-%d')}.xlsx")
    await callback.message.answer_document(report_file, caption=f"Отчет по заказам за выбранный период.")

@router.callback_query(F.data.startswith("admin_executor_report:"))
async def generate_executor_report(callback: types.CallbackQuery, session: AsyncSession):
    """Генерирует и отправляет отчет по заказам для конкретного исполнителя."""
    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)

    executor = await get_user(session, executor_id)
    if not executor:
        await callback.answer("Исполнитель не найден.", show_alert=True)
        return

    await callback.answer(f"Формирую отчет для {executor.name}...")

    # Выбираем все заказы за все время
    start_date = datetime.datetime.min
    end_date = datetime.datetime.now()

    orders = await get_orders_for_report_for_executor(session, start_date, end_date, executor_id)

    if not orders:
        await callback.message.answer(f"У исполнителя {executor.name} нет заказов для отчета.")
        return

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = f"Отчет по {executor.name}"

    headers = [
        "ID Заказа", "Дата создания", "Статус", "Клиент", "ID клиента",
        "Адрес", "Сумма заказа", "Выплата исполнителю"
    ]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for order in orders:
        row = [
            order.id,
            order.created_at.strftime("%d.%m.%Y %H:%M"),
            STATUS_MAPPING.get(order.status, order.status.value),
            order.client.name if order.client else "N/A",
            order.client_tg_id,
            order.address_text,
            order.total_price,
            order.executor_payment
        ]
        sheet.append(row)

    file_stream = io.BytesIO()
    workbook.save(file_stream)
    file_stream.seek(0)

    report_file = BufferedInputFile(
        file_stream.read(),
        filename=f"report_{executor.telegram_id}_{end_date.strftime('%Y-%m-%d')}.xlsx"
    )
    await callback.message.answer_document(report_file, caption=f"Отчет по заказам для исполнителя {executor.name}.")

# --- БЛОК: ЧАТ АДМИНА С ПОЛЬЗОВАТЕЛЯМИ ---

@router.callback_query(F.data.startswith("admin_chat:"))
async def start_admin_chat(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает чат админа с клиентом или исполнителем."""
    _, target_role, order_id_str = callback.data.split(":")
    order_id = int(order_id_str)

    order = await get_order_by_id(session, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    partner_id = None
    role_display_name = ""  # Переменная для красивого названия роли
    if target_role == "client":
        partner_id = order.client_tg_id
        role_display_name = "клиентом"
    elif target_role == "executor":
        partner_id = order.executor_tg_id
        role_display_name = "исполнителем"

    if not partner_id:
        await callback.answer("Не удалось найти контакт для чата.", show_alert=True)
        return

    await state.set_state(ChatStates.in_chat)
    await state.update_data(
        chat_partner_id=partner_id,
        order_id=order_id,
        partner_role=target_role  # Запоминаем, с кем чат
    )

    await callback.message.answer(
        f"Вы вошли в чат с {role_display_name} по заказу №{order.id}.\n"
        "Все сообщения будут пересланы. Для выхода нажмите кнопку.",
        reply_markup=get_exit_chat_keyboard()
    )
    await callback.answer()


@router.message(ChatStates.in_chat, F.text == "⬅️ Выйти из чата")
async def exit_admin_chat(message: types.Message, state: FSMContext):
    """Обрабатывает выход из чата для админа."""
    await state.clear()
    await message.answer(
        "Вы вышли из чата. Возвращаю в главное меню.",
        reply_markup=get_admin_main_keyboard()
    )


@router.message(ChatStates.in_chat)
async def forward_message_from_admin(message: types.Message, state: FSMContext, bots: dict):
    """Пересылает сообщение от админа клиенту или исполнителю."""
    user_data = await state.get_data()
    partner_id = user_data.get("chat_partner_id")
    order_id = user_data.get("order_id")
    partner_role = user_data.get("partner_role")

    if not all([partner_id, order_id, partner_role]):
        await message.answer("Ошибка чата. Попробуйте начать заново.")
        return

    target_bot = bots.get(partner_role)
    if not target_bot:
        await message.answer(f"Ошибка конфигурации: бот для роли '{partner_role}' не найден.")
        return

    # Если пользователь пытается отправить альбом, вежливо просим этого не делать
    if message.media_group_id:
        await message.answer("Пожалуйста, отправляйте фотографии по одной за раз.")
        return

    prefix = f"💬 <b>[Администратор | Заказ №{order_id}]:</b>\n"
    reply_keyboard = get_reply_to_chat_keyboard(order_id)

    try:
        if message.text:
            await target_bot.send_message(
                chat_id=partner_id,
                text=f"{prefix}{message.text}",
                reply_markup=reply_keyboard
            )
        elif message.photo:
            # Скачиваем файл через текущего бота (админского)
            photo_file = await message.bot.get_file(message.photo[-1].file_id)
            photo_bytes_io = await message.bot.download_file(photo_file.file_path)
            photo_to_send = BufferedInputFile(photo_bytes_io.read(), filename="photo.jpg")

            # И отправляем через целевого бота (клиентского или исполнительского)
            await target_bot.send_photo(
                chat_id=partner_id,
                photo=photo_to_send,
                caption=f"{prefix}{message.caption or ''}",
                reply_markup=reply_keyboard
            )

        await message.answer("✅ Сообщение отправлено.")

    except Exception as e:
        logging.error(f"Ошибка пересылки сообщения от админа к {partner_role} {partner_id}: {e}")
        await message.answer("Не удалось доставить сообщение. Попробуйте позже.")

# --- КОНЕЦ БЛОКА ---

async def _get_order_details_text(order: Order) -> str:
    """Вспомогательная функция для формирования текста с деталями заказа."""
    client_info = "Не найден"
    if order.client:
        identifier = f"@{order.client.username}" if order.client.username else f"ID: {order.client.telegram_id}"
        client_info = f"{order.client.name} ({identifier})"

    executor_info = "Не назначен"
    if order.executor:
        identifier = f"@{order.executor.username}" if order.executor.username else f"ID: {order.executor.telegram_id}"
        executor_info = f"{order.executor.name} ({identifier})"

    services_list = []
    for item in order.items:
        service_name = ADDITIONAL_SERVICES.get(item.service_key, "Неизвестная услуга")
        if "шт" in service_name and item.quantity > 1:
            services_list.append(f"  - {service_name} (x{item.quantity})")
        else:
            services_list.append(f"  - {service_name}")
    services_text = "\n".join(services_list) or "Нет"

    logs_list = []
    if order.logs:
        for log in sorted(order.logs, key=lambda x: x.timestamp):
            logs_list.append(f"  - {log.timestamp.strftime('%d.%m %H:%M')}: {log.message}")
    logs_text = "\n".join(logs_list) or "Нет записей"

    test_label = " (ТЕСТ)" if order.is_test else ""
    order_details = (
        f"📋 <b>Детали заказа №{order.id}{test_label} от {order.created_at.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
        f"<b>Статус:</b> {STATUS_MAPPING.get(order.status, 'Неизвестен')}\n\n"
        f"👤 <b>Клиент:</b> {client_info}\n"
        f"📞 <b>Телефон:</b> {order.order_phone}\n\n"
        f"🛠️ <b>Исполнитель:</b> {executor_info}\n\n"
        f"📍 <b>Адрес:</b> {order.address_text}\n"
        f"📅 <b>Дата и время:</b> {order.selected_date} {order.selected_time}\n\n"
        f"🧹 <b>Состав заказа:</b>\n"
        f"  - {order.cleaning_type} ({order.room_count} ком., {order.bathroom_count} с/у)\n"
        f"{services_text}\n\n"
        f"💰 <b>Сумма заказа:</b> {order.total_price} ₽\n"
        f"💸 <b>Выплата исполнителю:</b> {order.executor_payment or '—'} ₽\n\n"
        f"📜 <b>История действий:</b>\n{logs_text}"
    )
    return order_details

@router.callback_query(AdminExecutorStates.managing_access, F.data.startswith("admin_make_admin:"))
async def make_admin_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings, bots: dict):
    if callback.from_user.id != config.admin_id:
        await callback.answer("У вас нет прав для выполнения этого действия.", show_alert=True)
        return

    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)

    updated_user = await update_user_role(session, executor_id, UserRole.admin)
    if updated_user:
        try:
            admin_bot_info = await bots["admin"].get_me()
            admin_username = admin_bot_info.username
            await bots["executor"].send_message(
                chat_id=executor_id,
                text=f"👑 Вам предоставлена роль Администратора.\n\n"
                     f"Для доступа к панели управления, пожалуйста, перейдите в бот: @{admin_username}"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление о назначении админом пользователю {executor_id}: {e}")

        await callback.answer("✅ Роль успешно изменена на 'Администратор'.", show_alert=True)
        await manage_access_menu(callback, state, session, config) # Обновляем меню
    else:
        await callback.answer("❌ Не удалось обновить роль.", show_alert=True)


@router.callback_query(AdminExecutorStates.managing_access, F.data.startswith("admin_remove_admin:"))
async def remove_admin_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Возвращает администратору роль исполнителя (только для владельца)."""
    if callback.from_user.id != config.admin_id:
        await callback.answer("У вас нет прав для выполнения этого действия.", show_alert=True)
        return

    _, executor_id_str, page_str = callback.data.split(":")
    executor_id = int(executor_id_str)

    # Защита от снятия роли с самого себя
    if executor_id == callback.from_user.id:
        await callback.answer("Вы не можете снять роль администратора с самого себя.", show_alert=True)
        return

    updated_user = await update_user_role(session, executor_id, UserRole.executor)
    if updated_user:
        await callback.answer("✅ Роль возвращена на 'Исполнитель'.", show_alert=True)
        await manage_access_menu(callback, state, session, config) # Обновляем меню
    else:
        await callback.answer("❌ Не удалось обновить роль.", show_alert=True)

@router.callback_query(F.data == "admin_settings_menu")
async def back_to_settings_menu(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Возвращает в главное меню настроек."""
    await state.set_state(AdminSettingsStates.choosing_setting)
    test_mode_status = "Вкл. ✅" if config.system.test_mode_enabled else "Выкл. ❌"
    reply_markup = get_admin_settings_keyboard(
        test_mode_status=test_mode_status,
        current_user_id=callback.from_user.id,
        owner_id=config.admin_id
    )

    # Используем try-except, так как сообщение может быть уже удалено
    with suppress(TelegramBadRequest):
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(
                "⚙️ <b>Настройки системы</b>\n\n"
                "Выберите раздел для управления:",
                reply_markup=reply_markup
            )
        else:
            await callback.message.edit_text(
                "⚙️ <b>Настройки системы</b>\n\n"
                "Выберите раздел для управления:",
                reply_markup=reply_markup
            )
    await callback.answer()


@router.callback_query(StateFilter(AdminSettingsStates.choosing_setting, AdminSettingsStates.choosing_tariff_type, AdminSettingsStates.choosing_additional_service), F.data == "admin_setting:tariffs")
async def manage_tariffs_menu(callback: types.CallbackQuery, state: FSMContext):
    """Показывает меню управления тарифами."""
    await state.set_state(AdminSettingsStates.choosing_setting)
    await callback.message.edit_text(
        "📊 <b>Управление тарифами</b>\n\n"
        "Здесь вы можете изменить стоимость основных типов уборок и дополнительных услуг.",
        reply_markup=get_tariff_management_keyboard()
    )
    await callback.answer()


@router.callback_query(StateFilter(AdminSettingsStates.choosing_setting, AdminSettingsStates.choosing_tariff_type, AdminSettingsStates.editing_tariff), F.data == "admin_tariff:main")
async def manage_main_tariffs(callback: types.CallbackQuery, state: FSMContext):
    """Показывает клавиатуру для выбора основного тарифа для редактирования."""
    await state.set_state(AdminSettingsStates.choosing_tariff_type)
    await callback.message.edit_text(
        "Выберите тип уборки, для которого хотите изменить цены:",
        reply_markup=get_main_tariffs_keyboard()
    )


@router.callback_query(AdminSettingsStates.choosing_tariff_type, F.data.startswith("admin_edit_tariff:"))
async def edit_main_tariff_start(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Начинает процесс редактирования основного тарифа."""
    tariff_name = callback.data.split(":")[1]
    current_tariff = config.system.tariffs.get(tariff_name)

    if not current_tariff:
        await callback.answer("Тариф не найден.", show_alert=True)
        return

    await state.set_state(AdminSettingsStates.editing_tariff)
    await state.update_data(
        editing_tariff_name=tariff_name,
        prompt_message_id=callback.message.message_id  # Запоминаем ID сообщения для редактирования
    )

    await callback.message.edit_text(
        f"Редактирование тарифа: <b>{tariff_name}</b>\n\n"
        f"Текущие значения:\n"
        f"- Базовая цена: {current_tariff['base']} ₽\n"
        f"- За доп. комнату: {current_tariff['per_room']} ₽\n"
        f"- За доп. санузел: {current_tariff['per_bathroom']} ₽\n\n"
        "Отправьте новые значения в формате: <b>База Комната Санузел</b>\n"
        "Например: <code>1200 600 400</code>",
        reply_markup=get_cancel_editing_tariff_keyboard()
    )
    await callback.answer()


@router.message(AdminSettingsStates.editing_tariff, F.text)
async def edit_main_tariff_finish(message: types.Message, state: FSMContext, session: AsyncSession, config: Settings, bot: Bot):
    """Завершает редактирование основного тарифа и сохраняет в БД."""
    try:
        base, per_room, per_bathroom = map(int, message.text.split())
    except ValueError:
        await message.answer("Неверный формат. Пожалуйста, введите три числа через пробел, например: <b>1200 600 400</b>")
        return

    user_data = await state.get_data()
    tariff_name = user_data.get("editing_tariff_name")
    prompt_message_id = user_data.get("prompt_message_id")

    # Обновляем тарифы в объекте конфига и сохраняем в БД
    config.system.tariffs[tariff_name] = {"base": base, "per_room": per_room, "per_bathroom": per_bathroom}
    await update_system_settings(session, {"tariffs": json.dumps(config.system.tariffs)})

    # Удаляем старые сообщения
    if prompt_message_id:
        with suppress(TelegramBadRequest):
            await bot.delete_message(message.chat.id, prompt_message_id)
    await message.delete()

    # Отправляем новое сообщение с обновленным меню
    test_mode_status = "Вкл. ✅" if config.system.test_mode_enabled else "Выкл. ❌"
    await message.answer(
        f"✅ Тариф <b>{tariff_name}</b> успешно обновлен!\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_admin_settings_keyboard(test_mode_status=test_mode_status,
                                                 current_user_id=message.from_user.id,
                                                 owner_id=config.admin_id)
    )
    await state.set_state(AdminSettingsStates.choosing_setting)


@router.callback_query(F.data == "admin_tariff:additional")
async def manage_additional_services(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Показывает клавиатуру для выбора дополнительной услуги для редактирования."""
    await state.set_state(AdminSettingsStates.choosing_additional_service)
    await callback.message.edit_text(
        "Выберите дополнительную услугу для изменения цены:",
        reply_markup=get_additional_services_edit_keyboard(config.system.additional_services)
    )


@router.callback_query(AdminSettingsStates.choosing_additional_service, F.data.startswith("admin_edit_service:"))
async def edit_additional_service_start(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Начинает процесс изменения цены на доп. услугу."""
    service_key = callback.data.split(":")[1]
    service_name = config.system.additional_services.get(service_key, "Неизвестная услуга").split('(')[0].strip()
    current_price = calculate_price_from_service_string(config.system.additional_services.get(service_key, ""))

    await state.set_state(AdminSettingsStates.editing_additional_service_price)
    await state.update_data(
        editing_service_key=service_key,
        editing_service_name=service_name,
        prompt_message_id=callback.message.message_id # Запоминаем ID
    )

    await callback.message.edit_text(
        f"Редактирование услуги: <b>{service_name}</b>\n\n"
        f"Текущая цена: {current_price} ₽\n\n"
        "Введите новую цену (только число):"
    )
    await callback.answer()


@router.message(AdminSettingsStates.editing_additional_service_price, F.text)
async def edit_additional_service_finish(message: types.Message, state: FSMContext, session: AsyncSession, config: Settings, bot: Bot):
    """Завершает изменение цены на доп. услугу и сохраняет в БД."""
    try:
        new_price = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число.")
        return

    user_data = await state.get_data()
    service_key = user_data.get("editing_service_key")
    service_name = user_data.get("editing_service_name")
    prompt_message_id = user_data.get("prompt_message_id")

    # Обновляем цену в словаре в конфиге
    base_text = service_name
    if "шт" in config.system.additional_services[service_key]:
        config.system.additional_services[service_key] = f"{base_text} (+{new_price} ₽/шт)"
    else:
        config.system.additional_services[service_key] = f"{base_text} (+{new_price} ₽)"

    await update_system_settings(session, {"additional_services": json.dumps(config.system.additional_services)})

    # Удаляем старые сообщения
    if prompt_message_id:
        with suppress(TelegramBadRequest):
            await bot.delete_message(message.chat.id, prompt_message_id)
    await message.delete()

    # Отправляем новое сообщение с обновленным меню
    test_mode_status = "Вкл. ✅" if config.system.test_mode_enabled else "Выкл. ❌"
    await message.answer(
        f"✅ Цена для услуги <b>'{service_name}'</b> изменена на <b>{new_price} ₽</b>.\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_admin_settings_keyboard(test_mode_status=test_mode_status,
                                                 current_user_id=message.from_user.id,
                                                 owner_id=config.admin_id)
    )
    await state.set_state(AdminSettingsStates.choosing_setting)


@router.callback_query(AdminSettingsStates.choosing_setting, F.data == "admin_setting:commission")
async def manage_commission_menu(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Показывает меню управления комиссией."""
    await state.set_state(AdminSettingsStates.editing_commission_type)
    await callback.message.edit_text(
        "💰 <b>Управление комиссией</b>\n\n"
        "Здесь вы можете настроить комиссию, которая взимается с исполнителей.",
        reply_markup=get_commission_management_keyboard(
            current_type=config.system.commission_type,
            current_value=config.system.commission_value,
            show_commission=config.system.show_commission_to_executor
        )
    )
    await callback.answer()


@router.callback_query(AdminSettingsStates.editing_commission_type, F.data == "admin_commission:change_type")
async def change_commission_type(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Переключает тип комиссии и сохраняет в БД."""
    current_type = config.system.commission_type
    new_type = "fixed" if current_type == "percent" else "percent"
    config.system.commission_type = new_type
    await update_system_settings(session, {"commission_type": new_type})

    await callback.message.edit_reply_markup(
        reply_markup=get_commission_management_keyboard(
            current_type=config.system.commission_type,
            current_value=config.system.commission_value,
            show_commission=config.system.show_commission_to_executor
        )
    )
    await callback.answer(f"Тип комиссии изменен на '{new_type}'.")


@router.callback_query(AdminSettingsStates.editing_commission_type, F.data == "admin_commission:change_value")
async def change_commission_value_start(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Начинает процесс изменения значения комиссии."""
    await state.set_state(AdminSettingsStates.editing_commission_value)
    # Запоминаем ID сообщения для последующего удаления
    await state.update_data(prompt_message_id=callback.message.message_id)

    type_text = "процент" if config.system.commission_type == "percent" else "фиксированную сумму в рублях"
    await callback.message.edit_text(
        f"Текущее значение: <b>{config.system.commission_value}</b>\n\n"
        f"Введите новый {type_text} (только число):"
    )
    await callback.answer()


@router.message(AdminSettingsStates.editing_commission_value, F.text)
async def change_commission_value_finish(message: types.Message, state: FSMContext, session: AsyncSession, config: Settings, bot: Bot):
    """Завершает изменение значения комиссии и сохраняет в БД."""
    try:
        new_value = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число.")
        return

    user_data = await state.get_data()
    prompt_message_id = user_data.get("prompt_message_id")

    config.system.commission_value = new_value
    await update_system_settings(session, {"commission_value": new_value})

    # Удаляем старые сообщения (приглашение и ответ пользователя)
    if prompt_message_id:
        with suppress(TelegramBadRequest):
            await bot.delete_message(message.chat.id, prompt_message_id)
    await message.delete()

    # Отправляем новое сообщение с обновленным меню
    test_mode_status = "Вкл. ✅" if config.system.test_mode_enabled else "Выкл. ❌"
    await message.answer(
        f"✅ Значение комиссии изменено на <b>{new_value}</b>.\n\n"
        "Выберите раздел для управления:",
        reply_markup=get_admin_settings_keyboard(test_mode_status=test_mode_status,
                                                 current_user_id=message.from_user.id,
                                                 owner_id=config.admin_id)
    )
    await state.set_state(AdminSettingsStates.choosing_setting)

@router.callback_query(AdminSettingsStates.editing_commission_type, F.data == "admin_commission:toggle_show")
async def toggle_show_commission(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Переключает флаг показа комиссии для исполнителя."""
    # Инвертируем текущее значение
    new_status = not config.system.show_commission_to_executor
    config.system.show_commission_to_executor = new_status
    await update_system_settings(session, {"show_commission_to_executor": new_status})

    # Обновляем клавиатуру с новым состоянием
    await callback.message.edit_reply_markup(
        reply_markup=get_commission_management_keyboard(
            current_type=config.system.commission_type,
            current_value=config.system.commission_value,
            show_commission=new_status
        )
    )
    status_text = "включен" if new_status else "выключен"
    await callback.answer(f"Показ комиссии для исполнителей {status_text}.")

# --- Тестовый режим ---

@router.callback_query(AdminSettingsStates.choosing_setting, F.data == "admin_setting:test_mode")
async def toggle_test_mode(callback: types.CallbackQuery, session: AsyncSession, config: Settings):
    """Включает/выключает тестовый режим."""
    new_status = not config.system.test_mode_enabled
    config.system.test_mode_enabled = new_status
    await update_system_settings(session, {"test_mode_enabled": new_status})

    status_text = "Вкл. ✅" if new_status else "Выкл. ❌"
    await callback.answer(f"Тестовый режим: {status_text}", show_alert=True)

    # Обновляем клавиатуру, чтобы показать новый статус
    await callback.message.edit_reply_markup(
        reply_markup=get_admin_settings_keyboard(
            test_mode_status=status_text,
            current_user_id=callback.from_user.id,
            owner_id=config.admin_id
        )
    )

# --- БЛОК: УПРАВЛЕНИЕ АДМИНИСТРАЦИЕЙ (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ---

@router.callback_query(StateFilter(AdminSettingsStates.choosing_setting, AdminSettingsStates.managing_administration), F.data == "admin_setting:administration")
async def manage_administration_menu(callback: types.CallbackQuery, state: FSMContext, config: Settings):
    """Показывает меню управления администрацией."""
    if callback.from_user.id != config.admin_id:
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await state.set_state(AdminSettingsStates.managing_administration)
    await callback.message.edit_text(
        "👑 <b>Управление администрацией</b>\n\n"
        "Здесь вы можете назначать и снимать роли администраторов и супервайзеров.",
        reply_markup=get_administration_management_keyboard()
    )
    await callback.answer()

@router.callback_query(AdminSettingsStates.managing_administration, F.data == "admin_admin:list")
async def list_admins_handler(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает список всех администраторов и супервайзеров."""
    admins = await get_all_admins_and_supervisors(session)
    if not admins:
        await callback.answer("В системе нет администраторов или супервайзеров.", show_alert=True)
        return

    await callback.message.edit_text(
        "<b>Список администраторов и супервайзеров:</b>\n\n"
        "Нажмите на пользователя, чтобы снять с него роль.",
        reply_markup=get_admin_list_keyboard(admins)
    )
    await callback.answer()

@router.callback_query(AdminSettingsStates.managing_administration, F.data.startswith("admin_admin:remove_role:"))
async def remove_admin_role_handler(callback: types.CallbackQuery, session: AsyncSession, config: Settings, bots: dict):
    """Снимает с пользователя роль (Админ/Супервайзер), возвращая его к роли Исполнителя."""
    user_id_to_demote = int(callback.data.split(":")[2])

    if user_id_to_demote == config.admin_id:
        await callback.answer("Нельзя снять роль с владельца бота.", show_alert=True)
        return

    # Возвращаем роль исполнителя. Если юзер был клиентом, он все равно станет исполнителем.
    updated_user = await update_user_role(session, user_id_to_demote, UserRole.executor)
    if updated_user:
        try:
            # 1. Уведомляем пользователя о снятии роли через бота для исполнителей
            await bots["executor"].send_message(
                chat_id=user_id_to_demote,
                text="🔻 С вас сняты права администратора/супервайзера. Ваша роль изменена на 'Исполнитель'."
            )
            # 2. Уведомляем владельца
            await bots["admin"].send_message(
                config.admin_id,
                f"✅ Доступ закрыт.\n\n"
                f"С пользователя {updated_user.name} (@{updated_user.username or user_id_to_demote}) "
                f"сняты права. Роль изменена на: <b>Исполнитель</b>."
            )
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление о снятии роли пользователю {user_id_to_demote}: {e}")

        await callback.answer(f"Роль для {updated_user.name} успешно снята.", show_alert=True)
        # Обновляем список
        await list_admins_handler(callback, session)
    else:
        await callback.answer("Не удалось найти пользователя или обновить роль.", show_alert=True)

@router.callback_query(AdminSettingsStates.managing_administration, F.data.startswith("admin_admin:add_"))
async def add_admin_role_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс назначения новой роли."""
    role_to_add = "admin" if "add_admin" in callback.data else "supervisor"
    role_text = "Администратора" if role_to_add == "admin" else "Супервайзера"

    await state.update_data(role_to_add=role_to_add)
    if role_to_add == "admin":
        await state.set_state(AdminSettingsStates.adding_admin_id)
    else:
        await state.set_state(AdminSettingsStates.adding_supervisor_id)

    await callback.message.edit_text(
        f"Введите Telegram ID пользователя, которого вы хотите назначить на роль <b>{role_text}</b>."
    )
    await callback.answer()

@router.message(F.text, StateFilter(AdminSettingsStates.adding_admin_id, AdminSettingsStates.adding_supervisor_id))
async def add_admin_role_finish(message: types.Message, state: FSMContext, session: AsyncSession, config: Settings, bots: dict):
    """Завершает процесс назначения роли."""
    try:
        user_id_to_promote = int(message.text)
    except ValueError:
        await message.answer("Пожалуйста, введите корректный числовой ID.")
        return

    user_to_promote = await get_user(session, user_id_to_promote)
    if not user_to_promote:
        await message.answer("Пользователь с таким ID не найден в базе данных. Он должен хотя бы раз запустить одного из ботов.")
        return

    user_data = await state.get_data()
    role_to_add_str = user_data.get("role_to_add")
    new_role = UserRole[role_to_add_str]
    role_text_rus = "Администратора" if new_role == UserRole.admin else "Супервайзера"

    updated_user = await update_user_role(session, user_id_to_promote, new_role)

    if updated_user:
        await message.answer(f"✅ Пользователь {updated_user.name} успешно назначен на роль <b>{role_text_rus}</b>.")
        try:
            admin_bot_info = await bots["admin"].get_me()
            admin_username = admin_bot_info.username

            # 1. Отправляем уведомление ИСПОЛНИТЕЛЮ через бота для исполнителей
            await bots["executor"].send_message(
                chat_id=user_id_to_promote,
                text=f"👑 Вам предоставлена роль {role_text_rus}.\n\n"
                     f"Для доступа к панели управления, пожалуйста, перейдите в бот: @{admin_username}"
            )

            # 2. Отправляем уведомление ВЛАДЕЛЬЦУ о смене роли
            await bots["admin"].send_message(
                config.admin_id,
                f"✅ Доступ открыт.\n\n"
                f"Пользователю {updated_user.name} (@{updated_user.username or user_id_to_promote}) "
                f"успешно присвоена роль: <b>{role_text_rus}</b>."
            )
        except Exception:
            await message.answer("❗️Не удалось отправить уведомление пользователю. Возможно, он заблокировал бота.")

        # Возвращаемся в меню настроек
        await state.set_state(AdminSettingsStates.choosing_setting)
        test_mode_status = "Вкл. ✅" if config.system.test_mode_enabled else "Выкл. ❌"
        await message.answer(
            "⚙️ <b>Настройки системы</b>\n\n"
            "Выберите раздел для управления:",
            reply_markup=get_admin_settings_keyboard(
                test_mode_status=test_mode_status,
                current_user_id=message.from_user.id,
                owner_id=config.admin_id
            )
        )
    else:
        await message.answer("❌ Произошла ошибка при обновлении роли.")
