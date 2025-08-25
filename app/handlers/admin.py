from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TicketStatus, MessageAuthor
from app.handlers.states import AdminSupportStates
from app.services.db_queries import (
    get_tickets_by_status,
    get_ticket_by_id,
    update_ticket_status,
    add_message_to_ticket
)
from app.keyboards.admin_kb import (
    get_admin_main_keyboard,
    get_admin_support_keyboard,
    get_tickets_list_keyboard,
    get_ticket_work_keyboard,
    get_in_progress_ticket_keyboard,
    get_closed_ticket_keyboard,
    get_answered_ticket_keyboard,
)

router = Router()

@router.message(CommandStart())
async def cmd_start_admin(message: types.Message):
    await message.answer(
        "Добро пожаловать в панель администратора.",
        reply_markup=get_admin_main_keyboard()
    )

@router.message(F.text == "📞 Поддержка")
async def support_menu(message: types.Message, session: AsyncSession):
    """Показывает главное меню поддержки для администратора."""
    # Собираем счетчики для каждого статуса
    counts = {
        'new': len(await get_tickets_by_status(session, TicketStatus.new)),
        'in_progress': len(await get_tickets_by_status(session, TicketStatus.in_progress)),
        'answered': len(await get_tickets_by_status(session, TicketStatus.answered)),
        'closed': len(await get_tickets_by_status(session, TicketStatus.closed)),
    }
    await message.answer(
        "Меню управления обращениями:",
        reply_markup=get_admin_support_keyboard(counts=counts)
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

    # Собираем историю переписки
    history = f"<b>Обращение №{ticket.id} от клиента {ticket.user.name or ticket.user_tg_id}</b>\n"
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