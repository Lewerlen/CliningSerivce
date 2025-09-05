import logging
import datetime
from aiogram import F, Router, types
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List
from app.config import Settings
from app.database.models import UserRole, OrderStatus, UserStatus, DeclinedOrder, MessageAuthor
from app.handlers.states import ExecutorRegistration, ChatStates, ExecutorSupportStates
from app.common.texts import ADDITIONAL_SERVICES, STATUS_MAPPING
from app.services.price_calculator import calculate_executor_payment
from app.services.db_queries import (
    get_user,
    register_executor,
    get_orders_by_status,
    get_order_by_id,
    assign_executor_to_order,
    get_executor_active_orders,
    update_order_status,
    add_photo_to_order,
    get_executor_schedule,
    update_executor_schedule,
    get_executor_completed_orders, get_user_by_referral_code,
    credit_referral_bonus, get_executor_orders_with_reviews,
    unassign_executor_from_order, increment_and_get_declines, reset_consecutive_declines, block_user_temporarily,
    unblock_user, add_declined_order, decline_active_offer, get_matching_executors, create_ticket, get_user_tickets,
    get_ticket_by_id
)
from app.handlers.client import find_and_notify_executors
from app.keyboards.executor_kb import (
    get_executor_main_keyboard, get_exit_chat_keyboard,
    get_phone_request_keyboard, get_reply_to_chat_keyboard,
    get_new_orders_keyboard, get_finish_upload_keyboard,
    get_order_confirmation_keyboard,
    get_my_orders_keyboard,
    get_work_in_progress_keyboard,
    get_schedule_menu_keyboard,
    get_day_schedule_keyboard,
    WEEKDAYS, get_balance_orders_keyboard,
    get_referral_program_keyboard,
    get_executor_support_menu_keyboard,
    get_executor_my_tickets_keyboard,
    get_executor_view_ticket_keyboard,
    get_executor_skip_photo_keyboard
)

router = Router()


@router.message(CommandStart())
async def cmd_start_executor(message: types.Message, session: AsyncSession, state: FSMContext, command: CommandObject):
    """
    Обрабатывает команду /start.
    Если исполнитель уже зарегистрирован - показывает меню.
    Если нет - начинает процесс регистрации.
    Также обрабатывает реферальные ссылки.
    """
    await state.clear()

    # --- ОБРАБОТКА РЕФЕРАЛЬНОЙ ССЫЛКИ ---
    referral_code = command.args
    if referral_code:
        referrer = await get_user_by_referral_code(session, referral_code)
        if referrer:
            # Сохраняем ID пригласившего в состояние, чтобы использовать при регистрации
            await state.update_data(referred_by=referrer.telegram_id)
            await message.answer(f"Вы перешли по приглашению от пользователя {referrer.name}.")

    user = await get_user(session, message.from_user.id)
    if user and user.role == UserRole.executor:
        await message.answer(
            f"С возвращением, {user.name}!",
            reply_markup=get_executor_main_keyboard()
        )
    else:
        await message.answer(
            "Здравствуйте! Для начала работы в качестве исполнителя, пожалуйста, "
            "подтвердите ваш номер телефона, нажав на кнопку ниже.",
            reply_markup=get_phone_request_keyboard()
        )
        await state.set_state(ExecutorRegistration.waiting_for_phone)


@router.message(ExecutorRegistration.waiting_for_phone, (F.contact | F.text))
async def register_phone_received(message: types.Message, session: AsyncSession, state: FSMContext):
    """
    Принимает номер телефона и регистрирует исполнителя, учитывая реферала.
    """
    phone_number = message.contact.phone_number if message.contact else message.text
    user_data = await state.get_data()
    referred_by = user_data.get("referred_by")

    if not phone_number or not phone_number.replace("+", "").isdigit():
        await message.answer("Пожалуйста, отправьте корректный номер телефона или нажмите на кнопку.")
        return

    new_executor = await register_executor(
        session=session,
        telegram_id=message.from_user.id,
        name=message.from_user.full_name,
        username=message.from_user.username,
        phone=phone_number,
        referred_by=referred_by  # Передаем ID пригласившего
    )

    await message.answer(
        f"Спасибо, {new_executor.name}! Вы успешно зарегистрированы как исполнитель. Добро пожаловать!",
        reply_markup=get_executor_main_keyboard()
    )
    await state.clear()


@router.message(F.text == "🆕 Новые заказы")
async def show_new_orders(message: types.Message, session: AsyncSession):
    """Показывает список всех заказов со статусом 'new'."""
    user = await get_user(session, message.from_user.id)

    # Проверяем, не заблокирован ли пользователь
    if user and user.status == UserStatus.blocked:
        # Если время блокировки уже прошло, разблокируем
        if user.blocked_until and user.blocked_until < datetime.datetime.now():
            await unblock_user(session, user.telegram_id)
            await message.answer("✅ Срок вашей временной блокировки истек. Вы снова можете принимать заказы.")
            # Продолжаем выполнение функции, чтобы показать заказы
        else:
            await message.answer(
                f"❌ <b>Ваш аккаунт временно заблокирован.</b>\n\n"
                f"Вы не можете просматривать новые заказы. "
                f"Доступ будет восстановлен {user.blocked_until.strftime('%d.%m.%Y в %H:%M')}."
            )
            return

    new_orders = await get_orders_by_status(session, OrderStatus.new, executor_tg_id=message.from_user.id)

    if not new_orders:
        await message.answer("На данный момент новых заказов нет.")
        return

    await message.answer(
        "Доступные для взятия заказы:",
        reply_markup=get_new_orders_keyboard(new_orders)
    )


@router.callback_query(F.data.startswith("executor_view_order:"))
async def executor_view_order(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, config: Settings):
    """Показывает детали заказа исполнителю."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    # --- Блок форматирования даты ---
    try:
        formatted_date = datetime.datetime.strptime(order.selected_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        formatted_date = order.selected_date  # Если формат некорректен, показываем как есть

    executor_payment = round(order.total_price * 0.85)
    services_list = []
    for item in order.items:
        service_name = ADDITIONAL_SERVICES.get(item.service_key, "Неизвестная услуга")
        # Для услуг, измеряемых в штуках, добавляем количество
        if "шт" in service_name and item.quantity > 1:
            services_list.append(f"  - {service_name} (x{item.quantity})")
        else:
            services_list.append(f"  - {service_name}")
    services_text = "\n".join(services_list) or "Нет"

    # Внедряем новую переменную для финансового блока
    financial_block = ""
    if config.system.show_commission_to_executor:
        financial_block = (
            f"<b>Цена для клиента:</b> {order.total_price} ₽\n"
            f"💰 <b>Ваша выплата:</b> {executor_payment} ₽"
        )
    else:
        financial_block = f"💰 <b>Вознаграждение:</b> {executor_payment} ₽"

    order_details = (
        f"📝 <b>Детали заказа №{order.id}</b>\n\n"
        f"<b>Тип:</b> {order.cleaning_type}\n"
        f"<b>Комнат:</b> {order.room_count}, <b>Санузлов:</b> {order.bathroom_count}\n"
        f"<b>Адрес:</b> {order.address_text}\n"
        f"<b>Дата/время:</b> {formatted_date}, {order.selected_time}\n\n"
        f"<b>Доп. услуги:</b>\n{services_text}\n\n"
        f"{financial_block}"
    )

    await state.update_data({f"payment_{order_id}": executor_payment})

    await callback.message.answer(order_details, reply_markup=get_order_confirmation_keyboard(order_id))
    await callback.answer()


@router.callback_query(F.data.startswith("executor_accept_order:"))
async def executor_accept_order(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bots: dict,
                                config: Settings):
    """Обрабатывает принятие заказа исполнителем."""
    order_id = int(callback.data.split(":")[1])
    user_data = await state.get_data()
    payment = user_data.get(f"payment_{order_id}")

    if payment is None:
        order_for_payment = await get_order_by_id(session, order_id)
        if not order_for_payment:
            await callback.message.edit_text("❌ Ошибка: заказ не найден.")
            await callback.answer()
            return
        payment = calculate_executor_payment(
            total_price=order_for_payment.total_price,
            commission_type=config.system.commission_type,
            commission_value=config.system.commission_value
        )

    order = await assign_executor_to_order(session, order_id, callback.from_user.id, payment)

    if order:
        await reset_consecutive_declines(session, callback.from_user.id)
        await callback.message.edit_text(
            f"✅ Вы приняли заказ №{order.id}. Он перемещен в раздел 'Мои заказы'.\n\n"
            f"Не забудьте изменить статус на '🚀 В пути', когда отправитесь к клиенту."
        )
        try:
            await bots["client"].send_message(
                order.client_tg_id,
                f"🤝 Отличные новости! На ваш заказ №{order.id} назначен исполнитель."
            )
        except Exception as e:
            await bots["admin"].send_message(config.admin_id,
                                             f"⚠️ Не удалось уведомить клиента о назначении исполнителя на заказ №{order.id}. Ошибка: {e}")
    else:
        await callback.message.edit_text("❌ Этот заказ уже был принят другим исполнителем или отменен.")

    await callback.answer()


@router.callback_query(F.data.startswith("executor_decline_order:"))
async def executor_decline_order(callback: types.CallbackQuery, session: AsyncSession, bots: dict, config: Settings):
    """
    Обрабатывает отказ от заказа, применяет штрафы и НЕМЕДЛЕННО передает заказ следующему.
    """
    order_id = int(callback.data.split(":")[1])
    executor_id = callback.from_user.id

    # 1. Помечаем текущее предложение как отклоненное
    await decline_active_offer(session, order_id, executor_id)
    # Запоминаем, что этот исполнитель отказался от заказа, чтобы не предлагать снова
    await add_declined_order(session, order_id, executor_id)

    # 2. Применяем штрафную систему (этот блок без изменений)
    user = await increment_and_get_declines(session, executor_id)
    if user and user.consecutive_declines >= 3:
        blocked_user = await block_user_temporarily(session, executor_id, hours=12)
        await callback.message.edit_text(
            f"Вы отказались от заказа №{order_id}.\n\n"
            f"⚠️ <b>Вы были временно заблокированы на 12 часов за 3 отказа подряд.</b>\n"
            f"Доступ будет восстановлен {blocked_user.blocked_until.strftime('%d.%m.%Y в %H:%M')}."
        )
        await bots["admin"].send_message(
            config.admin_id,
            f"⚠️ <b>Исполнитель @{callback.from_user.username or executor_id} был заблокирован на 12 часов</b>."
        )
    else:
        await callback.message.edit_text(
            f"Вы отказались от заказа №{order_id}.\n\n"
            f"<u>Внимание:</u> у вас {user.consecutive_declines if user else 0} отказ(а) подряд. "
            f"При 3 отказах подряд ваш аккаунт будет временно заблокирован."
        )

    # --- НОВАЯ ЛОГИКА: Мгновенный поиск следующего ---
    order = await get_order_by_id(session, order_id)
    if not order or order.status != OrderStatus.new:
        await callback.answer()
        return

    all_executors = await get_matching_executors(session, order.selected_date, order.selected_time)

    # Находим всех, кому уже предлагали или кто отказался
    declined_stmt = select(DeclinedOrder.executor_tg_id).where(DeclinedOrder.order_id == order_id)
    declined_result = await session.execute(declined_stmt)
    declined_ids = set(declined_result.scalars().all())

    # Ищем первого в списке, которому еще не предлагали
    next_executor = None
    for executor in all_executors:
        if executor.telegram_id not in declined_ids:
            next_executor = executor
            break

        # Если нашли, отправляем ему предложение
    if next_executor:
        from app.handlers.client import offer_order_to_executor  # Локальный импорт
        await offer_order_to_executor(session, bots, order, next_executor, config)
    else:
        # Если исполнители кончились
        await bots["admin"].send_message(
            config.admin_id,
            f"❗️<b>Никто не принял заказ №{order.id}.</b>\n"
            "Очередь исполнителей закончилась. Рекомендуется ручное назначение."
        )

    await callback.answer()


@router.message(F.text == "📋 Мои заказы")
async def show_my_orders(message: types.Message, session: AsyncSession):
    """Показывает список принятых исполнителем заказов."""
    my_orders = await get_executor_active_orders(session, message.from_user.id)

    if not my_orders:
        await message.answer("У вас нет принятых заказов в работе.")
        return

    await message.answer(
        "Ваши активные заказы:",
        reply_markup=get_my_orders_keyboard(my_orders)
    )


@router.callback_query(F.data.startswith("executor_view_my_order:"))
async def executor_view_my_order(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает детали заказа, который исполнитель уже принял."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order or order.executor_tg_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    # --- Блок форматирования даты ---
    try:
        formatted_date = datetime.datetime.strptime(order.selected_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        formatted_date = order.selected_date  # Если формат некорректен, показываем как есть

    services_text = "\n".join([f"  - {ADDITIONAL_SERVICES[item.service_key]}" for item in order.items]) or "Нет"

    test_label = " (ТЕСТ)" if order.is_test else ""
    order_details = (
        f"📝 <b>Детали заказа №{order.id}{test_label}</b>\n\n"
        f"<b>Статус:</b> {STATUS_MAPPING.get(order.status, 'Неизвестен')}\n"
        f"<b>Клиент:</b> {order.order_name}\n"
        f"<b>Адрес:</b> {order.address_text}\n"
        f"<b>Дата/время:</b> {formatted_date}, {order.selected_time}\n\n"
        f"<b>Доп. услуги:</b>\n{services_text}\n\n"
        f"💰 <b>Ваша выплата:</b> {order.executor_payment} ₽"
    )

    await callback.message.answer(order_details, reply_markup=get_work_in_progress_keyboard(order))
    await callback.answer()


@router.callback_query(F.data.startswith("executor_status_ontheway:"))
async def executor_status_on_the_way(callback: types.CallbackQuery, session: AsyncSession, bots: dict,
                                      config: Settings):
    """Обрабатывает смену статуса на 'в пути'."""
    order_id = int(callback.data.split(":")[1])
    order = await update_order_status(session, order_id, OrderStatus.on_the_way)

    if order:
        await callback.message.edit_text(
            f"✅ Статус заказа №{order.id} изменен на 'В пути'.\n\n"
            f"Когда прибудете на место, не забудьте нажать '✅ Начать уборку'."
        )
        # Уведомляем клиента
        try:
            await bots["client"].send_message(
                order.client_tg_id,
                f"🚀 Исполнитель выехал по вашему заказу №{order.id}."
            )
        except Exception as e:
            await bots["admin"].send_message(config.admin_id,
                                             f"⚠️ Не удалось уведомить клиента о выезде исполнителя (заказ №{order.id}). Ошибка: {e}")
    else:
        await callback.message.edit_text("❌ Не удалось обновить статус заказа.")

    await callback.answer()

@router.callback_query(F.data.startswith("executor_status_inprogress:"))
async def executor_status_in_progress(callback: types.CallbackQuery, session: AsyncSession, bots: dict,
                                      config: Settings):
    """Обрабатывает смену статуса на 'в работе' (уборка начата)."""
    order_id = int(callback.data.split(":")[1])
    order = await update_order_status(session, order_id, OrderStatus.in_progress)

    if order:
        # Устанавливаем время начала уборки
        order.in_progress_at = datetime.datetime.now()
        await session.commit()

        await callback.message.edit_text(
            f"✅ Статус заказа №{order.id} изменен на 'В работе'.\n\n"
            f"После окончания уборки, пожалуйста, загрузите фото 'после' и нажмите '✅ Завершить'."
        )
        # Уведомляем клиента
        try:
            await bots["client"].send_message(
                order.client_tg_id,
                f"🧼 Исполнитель приступил к уборке по вашему заказу №{order.id}."
            )
        except Exception as e:
            await bots["admin"].send_message(config.admin_id,
                                             f"⚠️ Не удалось уведомить клиента о начале уборки (заказ №{order.id}). Ошибка: {e}")
    else:
        await callback.message.edit_text("❌ Не удалось обновить статус заказа.")

    await callback.answer()


@router.callback_query(F.data.startswith("executor_upload_photo:"))
async def executor_upload_photo_start(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает процесс загрузки фото 'после'."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    current_photos_count = len(order.photos_after_ids) if order and order.photos_after_ids else 0

    await state.update_data(order_id_for_photo=order_id)
    await callback.message.answer(
        f"Пожалуйста, отправьте ОДНУ или НЕСКОЛЬКО фотографий выполненной работы (до 10 шт.).\n"
        f"Уже загружено: {current_photos_count}/10.\n\n"
        "Когда закончите, нажмите '✅ Готово'.",
        reply_markup=get_finish_upload_keyboard()
    )
    await state.set_state(ExecutorRegistration.uploading_photo)
    await callback.answer()


@router.message(ExecutorRegistration.uploading_photo, F.photo)
async def executor_photo_uploaded(message: types.Message, session: AsyncSession, state: FSMContext,
                                  album: List[types.Message] = None):
    """Принимает одно фото или альбом, сохраняет их и сообщает результат одним сообщением."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_for_photo")

    # Определяем, пришел альбом или одиночное фото
    photos_to_process = album if album else [message]

    order = await get_order_by_id(session, order_id)
    current_photos_count = len(order.photos_after_ids) if order and order.photos_after_ids else 0

    if current_photos_count + len(photos_to_process) > 10:
        await message.answer(
            f"Вы пытаетесь загрузить слишком много фото. Максимум - 10, уже загружено {current_photos_count}.")
        return

    # Добавляем все фото в БД в цикле
    for msg in photos_to_process:
        photo_id = msg.photo[-1].file_id
        await add_photo_to_order(session, order_id, photo_id)

    new_total_count = current_photos_count + len(photos_to_process)

    await message.answer(
        f"✅ Загружено {len(photos_to_process)} фото.\n"
        f"Всего для заказа: {new_total_count}/10."
    )


@router.message(ExecutorRegistration.uploading_photo, F.text == "✅ Готово")
async def executor_upload_finish(message: types.Message, session: AsyncSession, state: FSMContext):
    """Завершает процесс загрузки фото и возвращает к деталям заказа."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_for_photo")

    await state.clear()
    await message.answer("Вы вышли из режима загрузки фото.", reply_markup=get_executor_main_keyboard())

    # --- Прямая отправка деталей заказа вместо вызова другого хендлера ---
    order = await get_order_by_id(session, order_id)
    if order:
        # Форматируем дату
        try:
            formatted_date = datetime.datetime.strptime(order.selected_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            formatted_date = order.selected_date

        services_text = "\n".join([f"  - {ADDITIONAL_SERVICES[item.service_key]}" for item in order.items]) or "Нет"

        order_details = (
            f"📝 <b>Детали заказа №{order.id}</b>\n\n"
            f"<b>Статус:</b> {STATUS_MAPPING.get(order.status, 'Неизвестен')}\n"
            f"<b>Клиент:</b> {order.order_name}, {order.order_phone}\n"
            f"<b>Адрес:</b> {order.address_text}\n"
            f"<b>Дата/время:</b> {formatted_date}, {order.selected_time}\n\n"
            f"<b>Доп. услуги:</b>\n{services_text}\n\n"
            f"💰 <b>Ваша выплата:</b> {order.executor_payment} ₽"
        )

        await message.answer(order_details, reply_markup=get_work_in_progress_keyboard(order))

@router.message(ExecutorRegistration.uploading_photo, ~F.photo)
async def incorrect_photo_upload(message: types.Message):
    """Ловит любые сообщения, кроме фото, в состоянии загрузки."""
    await message.answer("Пожалуйста, отправьте фотографию, а не текст или другой файл.")

@router.callback_query(F.data.startswith("executor_complete_order:"))
async def executor_complete_order(callback: types.CallbackQuery, session: AsyncSession, bots: dict, config: Settings):
    """Завершает заказ и начисляет реферальный бонус, если это первый заказ."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    if not order.photos_after_ids:
        await callback.answer("Вы не можете завершить заказ, не загрузив фото 'после'.", show_alert=True)
        return

    # Меняем статус на 'completed'
    updated_order = await update_order_status(session, order_id, OrderStatus.completed)

    if updated_order:
        # Устанавливаем время завершения уборки
        updated_order.completed_at = datetime.datetime.now()
        await session.commit()

        await callback.message.edit_text(f"🎉 Заказ №{order_id} успешно завершен!")

        # --- НОВЫЙ БЛОК: Проверка и начисление реферального бонуса ---
        executor_user = await get_user(session, updated_order.executor_tg_id)
        if executor_user and executor_user.referred_by:
            # Проверяем, что это первый выполненный заказ
            completed_orders = await get_executor_completed_orders(session, executor_user.telegram_id)
            if len(completed_orders) == 1:
                await credit_referral_bonus(session, executor_user.referred_by)
                try:
                    # Уведомляем пригласившего о бонусе
                    await bots["executor"].send_message(
                        chat_id=executor_user.referred_by,
                        text=f"🎉 Поздравляем! Ваш реферал {executor_user.name} выполнил свой первый заказ. Вам начислен бонус 500 ₽!"
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить уведомление о реферальном бонусе пользователю {executor_user.referred_by}: {e}")

        # Уведомление клиенту с просьбой об оценке (остается без изменений)
        try:
            rating_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Оценить работу", callback_data=f"rate_order:{order_id}")]
            ])
            await bots["client"].send_message(
                order.client_tg_id,
                f"🎉 Ваша уборка по заказу №{order.id} завершена! "
                f"Пожалуйста, оцените качество работы нашего исполнителя.",
                reply_markup=rating_keyboard
            )
        except Exception as e:
            await bots["admin"].send_message(config.admin_id,
                                             f"⚠️ Не удалось отправить клиенту запрос на оценку заказа №{order.id}. Ошибка: {e}")
    else:
        await callback.message.edit_text("❌ Не удалось завершить заказ.")

    await callback.answer()


# --- БЛОК УПРАВЛЕНИЯ ГРАФИКОМ РАБОТЫ ---

def format_schedule_text(schedule_data: dict) -> str:
    """Форматирует текст с текущим расписанием из словаря состояния."""
    text = "🗓️ <b>Ваш текущий график работы:</b>\n\n"

    has_any_slot = False
    # Проверяем, есть ли хоть один выбранный слот во всем графике
    for day_slots in schedule_data.values():
        if day_slots:
            has_any_slot = True
            break

    if not has_any_slot:
        text += "Вы не выбрали ни одного рабочего слота."
        return text

    for day_code, day_name in WEEKDAYS.items():
        slots = schedule_data.get(day_code, [])
        if slots:
            # Сортируем слоты по времени начала (первое число в строке)
            sorted_slots = sorted(slots, key=lambda slot: int(slot.split(':')[0]))
            slots_str = ", ".join(sorted_slots)
            text += f"<b>{day_name}:</b> {slots_str}\n"

    return text


@router.message(F.text == "🗓️ График работы")
async def show_schedule_menu(message: types.Message, session: AsyncSession, state: FSMContext):
    """Отображает меню управления графиком работы."""
    await state.clear()
    schedule = await get_executor_schedule(session, message.from_user.id)

    schedule_data = {day: getattr(schedule, day, []) for day in WEEKDAYS} if schedule else {}
    await state.set_state(ExecutorRegistration.editing_schedule)
    # Сохраняем и сам график, и флаг его существования в БД
    await state.update_data(schedule=schedule_data, schedule_exists_in_db=(schedule is not None))

    if schedule:
        text = format_schedule_text(schedule_data)
    else:
        text = (
            "🗓️ <b>Ваш текущий график работы:</b>\n\n"
            "По умолчанию вы доступны для заказов в любые дни и время.\n\n"
            "Если вы хотите ограничить рабочее время, настройте ваш график."
        )

    text += "\n\nНажмите на день недели, чтобы изменить доступные временные слоты."

    await message.answer(text, reply_markup=get_schedule_menu_keyboard())


@router.callback_query(ExecutorRegistration.editing_schedule, F.data.startswith("edit_schedule_day:"))
async def edit_schedule_day(callback: types.CallbackQuery, state: FSMContext):
    """Показывает клавиатуру для редактирования слотов выбранного дня."""
    day_code = callback.data.split(":")[1]
    day_name = WEEKDAYS.get(day_code)

    user_data = await state.get_data()
    schedule_data = user_data.get("schedule", {})
    selected_slots = schedule_data.get(day_code, [])

    await callback.message.edit_text(
        f"Выберите доступные слоты для: <b>{day_name}</b>",
        reply_markup=get_day_schedule_keyboard(day_code, selected_slots)
    )
    await callback.answer()


@router.callback_query(ExecutorRegistration.editing_schedule, F.data.startswith("toggle_schedule_slot:"))
async def toggle_schedule_slot(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор/снятие временного слота."""
    _, day_code, slot = callback.data.split(":", 2)

    user_data = await state.get_data()
    schedule_data = user_data.get("schedule", {})

    # Получаем слоты для текущего дня, создаем список, если его нет
    day_slots = schedule_data.get(day_code, [])

    if slot in day_slots:
        day_slots.remove(slot)
    else:
        day_slots.append(slot)

    schedule_data[day_code] = day_slots
    await state.update_data(schedule=schedule_data)

    # Обновляем клавиатуру, чтобы показать изменения
    await callback.message.edit_reply_markup(
        reply_markup=get_day_schedule_keyboard(day_code, day_slots)
    )
    await callback.answer()


@router.callback_query(ExecutorRegistration.editing_schedule, F.data == "back_to_schedule_menu")
async def back_to_schedule_menu(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает к главному меню выбора дня недели (без запроса к БД)."""
    user_data = await state.get_data()
    schedule_data = user_data.get("schedule", {})

    # Проверяем, существует ли график в БД, чтобы показать правильное приветствие
    # Для этого нам все еще нужен один быстрый запрос при входе в меню
    schedule_in_db = user_data.get("schedule_exists_in_db", False)

    if not schedule_in_db and not any(schedule_data.values()):
        text = (
            "🗓️ <b>Ваш текущий график работы:</b>\n\n"
            "По умолчанию вы доступны для заказов в любые дни и время.\n\n"
            "Если вы хотите ограничить рабочее время, настройте ваш график."
        )
    else:
        text = format_schedule_text(schedule_data)

    text += "\n\nНажмите на день недели, чтобы изменить доступные временные слоты."

    await callback.message.edit_text(text, reply_markup=get_schedule_menu_keyboard())
    await callback.answer()


@router.callback_query(ExecutorRegistration.editing_schedule, F.data == "save_schedule")
async def save_schedule(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Сохраняет изменения в графике и выходит из режима редактирования."""
    user_data = await state.get_data()
    schedule_data = user_data.get("schedule")

    await update_executor_schedule(session, callback.from_user.id, schedule_data)

    await state.clear()
    await callback.message.edit_text("✅ Ваш график работы успешно сохранен!")
    # Отправляем сообщение, чтобы вернуть основную клавиатуру
    await callback.message.answer("Вы вернулись в главное меню.", reply_markup=get_executor_main_keyboard())
    await callback.answer()


# --- БЛОК УПРАВЛЕНИЯ БАЛАНСОМ ---

@router.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message, session: AsyncSession):
    """Показывает список завершенных заказов с суммой выплат."""
    completed_orders = await get_executor_completed_orders(session, message.from_user.id, limit=10)

    if not completed_orders:
        await message.answer("У вас еще нет завершенных заказов.")
        return

    total_payout = sum(order.executor_payment for order in completed_orders if order.executor_payment is not None)

    text = (
        f"💰 <b>Ваши начисления за последние {len(completed_orders)} заказов: {total_payout:.2f} ₽</b>\n\n"
        "Здесь показан список ваших последних выполненных работ. "
        "По вопросам выплат, пожалуйста, обращайтесь к администратору."
    )

    await message.answer(
        text,
        reply_markup=get_balance_orders_keyboard(completed_orders)
    )

@router.message(F.text == "👥 Реферальная программа")
async def show_referral_program(message: types.Message, session: AsyncSession):
    """Показывает информацию о реферальной программе."""
    user = await get_user(session, message.from_user.id)
    if not user or not user.referral_code:
        await message.answer("Произошла ошибка, ваш реферальный код не найден.")
        return

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    referral_link = f"https://t.me/{bot_username}?start={user.referral_code}"

    # Расчет количества выполнивших первый заказ (бонус за заказ - 500р)
    completed_count = int(user.referral_balance / 500)

    text = (
        f"<b>👥 Ваша реферальная программа</b>\n\n"
        f"Приглашайте других исполнителей и получайте <b>500 ₽</b> за каждого, кто выполнит свой первый заказ!\n\n"
        f"<b>Ваша реферальная ссылка:</b>\n<code>{referral_link}</code>\n\n"
        f"<b>Приглашено (всего регистраций):</b> {user.referrals_count}\n"
        f"<b>Выполнили первый заказ:</b> {completed_count}\n"
        f"<b>Заработано:</b> {user.referral_balance} ₽\n\n"
        "Нажмите кнопку 'Поделиться' ниже, чтобы отправить ссылку друзьям."
    )

    await message.answer(text, reply_markup=get_referral_program_keyboard(referral_link=referral_link))

@router.message(F.text == "⭐ Мой рейтинг")
async def show_my_rating(message: types.Message, session: AsyncSession):
    """Показывает исполнителю его текущий рейтинг и последние отзывы."""
    user = await get_user(session, message.from_user.id)
    if not user:
        await message.answer("Не удалось найти ваш профиль.")
        return

    # Получаем последние 5 заказов с отзывами
    orders_with_reviews = await get_executor_orders_with_reviews(session, message.from_user.id, limit=5)

    text = (
        f"<b>⭐ Ваш рейтинг</b>\n\n"
        f"<b>Средняя оценка:</b> {user.average_rating:.2f} из 5.00\n"
        f"<b>Всего оценок:</b> {user.review_count}\n\n"
    )

    if not orders_with_reviews:
        text += "У вас пока нет отзывов."
    else:
        text += "<b>Последние отзывы:</b>\n\n"
        for order in orders_with_reviews:
            text += (
                f"<b>Заказ №{order.id}</b> | Оценка: {'⭐' * order.rating}\n"
                f"<i>«{order.review_text}»</i>\n---\n"
            )

    await message.answer(text)

@router.callback_query(ExecutorRegistration.editing_schedule, F.data == "clear_schedule")
async def clear_schedule(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Полностью очищает график работы исполнителя."""
    # Создаем пустую структуру графика
    cleared_schedule_data = {day: [] for day in WEEKDAYS}

    # Сразу сохраняем пустой график в БД
    await update_executor_schedule(session, callback.from_user.id, cleared_schedule_data)

    await state.clear()
    await callback.message.edit_text(
        "✅ Ваш график работы был полностью очищен.\n\n"
        "Теперь вы не будете получать уведомления о новых заказах до тех пор, "
        "пока не настроите график заново."
    )
    # Отправляем сообщение, чтобы вернуть основную клавиатуру
    await callback.message.answer("Вы вернулись в главное меню.", reply_markup=get_executor_main_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("view_order_photos:"))
async def view_order_photos(callback: types.CallbackQuery, session: AsyncSession, bots: dict):
    """Отправляет исполнителю фотографии квартиры от клиента."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order or not order.photo_file_ids:
        await callback.answer("К этому заказу не прикреплены фотографии.", show_alert=True)
        return

    client_bot = bots.get("client")
    if not client_bot:
        await callback.answer("Ошибка конфигурации: клиентский бот не найден.", show_alert=True)
        return

    media_group = []
    try:
        for photo_id in order.photo_file_ids:
            # 1. Скачиваем файл с помощью клиент-бота
            photo_file = await client_bot.get_file(photo_id)
            photo_bytes_io = await client_bot.download_file(photo_file.file_path)
            photo_bytes = photo_bytes_io.read()

            # 2. Готовим файл для отправки
            buffered_file = BufferedInputFile(photo_bytes, filename=f"photo_{order_id}.jpg")
            media_group.append(InputMediaPhoto(media=buffered_file))

        # 3. Отправляем медиа-группу от имени текущего (исполнительского) бота
        if media_group:
            await callback.message.answer_media_group(media=media_group)

    except Exception as e:
        logging.error(f"Ошибка при отправке фото заказа №{order_id} исполнителю: {e}")
        await callback.answer("Произошла ошибка при загрузке фотографий.", show_alert=True)
    finally:
        await callback.answer()

# --- БЛОК: ЧАТ С КЛИЕНТОМ ---

@router.callback_query(F.data.startswith("start_chat:"))
async def start_chat_with_partner(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, config: Settings):
    """Начинает чат с клиентом или администратором в зависимости от контекста."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order:
        await callback.answer("Не удалось найти заказ.", show_alert=True)
        return

    original_message_text = callback.message.text or callback.message.caption or ""
    partner_id = None
    partner_role = None
    welcome_text = ""

    # Если в сообщении есть упоминание администратора/поддержки, это ответ админу
    if "[Администратор" in original_message_text or "[Поддержка" in original_message_text:
        partner_id = config.admin_id
        partner_role = "admin"
        welcome_text = f"Вы вошли в чат с администратором по заказу №{order.id}.\n" \
                       "Все сообщения будут пересланы. Для выхода нажмите кнопку."
    # В противном случае, это чат с клиентом
    elif order.client_tg_id:
        partner_id = order.client_tg_id
        partner_role = "client"
        welcome_text = f"Вы вошли в чат с клиентом по заказу №{order.id}.\n" \
                       "Все сообщения, которые вы сюда отправите, будут пересланы ему. " \
                       "Чтобы выйти, нажмите кнопку ниже."
    else:
        await callback.answer("Не удалось определить получателя чата.", show_alert=True)
        return

    await state.set_state(ChatStates.in_chat)
    await state.update_data(
        chat_partner_id=partner_id,
        partner_role=partner_role,
        order_id=order.id
    )
    await callback.message.answer(welcome_text, reply_markup=get_exit_chat_keyboard())
    await callback.answer()


@router.message(ChatStates.in_chat, F.text == "⬅️ Выйти из чата")
async def exit_chat_executor(message: types.Message, state: FSMContext):
    """Обрабатывает выход из чата для исполнителя."""
    await state.clear()
    await message.answer(
        "Вы вышли из чата. Возвращаю в главное меню.",
        reply_markup=get_executor_main_keyboard()
    )


@router.message(ChatStates.in_chat)
async def forward_message_from_executor(message: types.Message, state: FSMContext, bots: dict):
    """Пересылает сообщение от исполнителя клиенту или админу."""
    user_data = await state.get_data()
    partner_id = user_data.get("chat_partner_id")
    order_id = user_data.get("order_id")
    partner_role = user_data.get("partner_role") # Получаем роль из состояния

    if not all([partner_id, order_id, partner_role]):
        await message.answer("Ошибка чата. Попробуйте начать заново.")
        return

    # Динамически выбираем нужного бота для отправки
    target_bot = bots.get(partner_role)
    if not target_bot:
        await message.answer(f"Ошибка конфигурации: бот для роли '{partner_role}' не найден.")
        return

    if message.media_group_id:
        await message.answer("Пожалуйста, отправляйте фотографии по одной за раз.")
        return

    prefix = f"💬 <b>[Исполнитель | Заказ №{order_id}]:</b>\n"
    # Если сообщение адресовано админу, кнопка "Ответить" ему не нужна
    reply_keyboard = get_reply_to_chat_keyboard(order_id) if partner_role != "admin" else None

    try:
        if message.text:
            await target_bot.send_message(partner_id, f"{prefix}{message.text}", reply_markup=reply_keyboard)
        elif message.photo:
            photo_file = await message.bot.get_file(message.photo[-1].file_id)
            photo_bytes_io = await message.bot.download_file(photo_file.file_path)
            photo_to_send = BufferedInputFile(photo_bytes_io.read(), filename="photo.jpg")

            await target_bot.send_photo(
                chat_id=partner_id,
                photo=photo_to_send,
                caption=f"{prefix}{message.caption or ''}",
                reply_markup=reply_keyboard
            )
        await message.answer("✅ Ваше сообщение отправлено.")

    except Exception as e:
        logging.error(f"Ошибка пересылки сообщения к {partner_role} {partner_id}: {e}")
        await message.answer("Не удалось доставить сообщение. Попробуйте позже.")

# --- КОНЕЦ БЛОКА ---

@router.callback_query(F.data.startswith("executor_accept_changes:"))
async def executor_accept_changes(callback: types.CallbackQuery, session: AsyncSession, bots: dict, config: Settings):
    """Обрабатывает подтверждение изменений в заказе исполнителем."""
    order_id = int(callback.data.split(":")[1])
    # Меняем статус обратно на 'accepted'
    order = await update_order_status(session, order_id, OrderStatus.accepted)

    if order:
        await callback.message.edit_text(f"✅ Вы подтвердили изменения в заказе №{order_id}.")
        # Уведомляем клиента
        try:
            await bots["client"].send_message(
                chat_id=order.client_tg_id,
                text=f"👍 Исполнитель подтвердил готовность выполнить заказ №{order_id} с внесенными изменениями."
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить клиента о подтверждении изменений по заказу {order_id}: {e}")
            await bots["admin"].send_message(
                config.admin_id,
                f"⚠️ Не удалось уведомить клиента о подтверждении изменений по заказу №{order_id}."
            )
    else:
        await callback.message.edit_text("❌ Не удалось найти или обновить заказ.")

    await callback.answer()

@router.callback_query(F.data.startswith("executor_decline_changes:"))
async def executor_decline_changes(callback: types.CallbackQuery, session: AsyncSession, bots: dict, config: Settings):
    """Обрабатывает отказ исполнителя от заказа после изменений."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id) # Нужны данные заказа для уведомлений

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    # Снимаем исполнителя с заказа и возвращаем в пул
    unassigned_order = await unassign_executor_from_order(session, order_id)

    if unassigned_order:
        await callback.message.edit_text(f"Вы отказались от заказа №{order_id}. Заказ возвращен в список новых.")

        # Уведомляем клиента
        try:
            await bots["client"].send_message(
                chat_id=order.client_tg_id,
                text=(
                    f"❗️ К сожалению, исполнитель не смог принять изменения в заказе №{order_id}. "
                    "Мы уже начали поиск нового исполнителя для вас."
                )
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить клиента об отказе исполнителя по заказу {order_id}: {e}")

            # Уведомляем админа
            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ Исполнитель @{callback.from_user.username or callback.from_user.id} отказался от заказа №{order_id} после внесения изменений. "
                "Заказ возвращен в пул новых."
            )

            # Запускаем повторный поиск исполнителя
            await find_and_notify_executors(session, order_id, bots["executor"], config)

        else:
            await callback.message.edit_text("❌ Не удалось обновить заказ.")

        await callback.answer()

# --- БЛОК: СИСТЕМА ПОДДЕРЖКИ ДЛЯ ИСПОЛНИТЕЛЯ ---

@router.message(F.text == "🆘 Помощь")
async def executor_support_menu(message: types.Message, state: FSMContext):
    """Показывает главное меню раздела поддержки для исполнителя."""
    await state.clear()
    await message.answer(
        "Вы находитесь в разделе поддержки. Чем мы можем помочь?",
        reply_markup=get_executor_support_menu_keyboard()
    )

@router.callback_query(F.data == "executor_create_ticket")
async def executor_create_ticket_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс создания нового тикета от исполнителя."""
    await callback.message.edit_text(
        "Пожалуйста, подробно опишите вашу проблему или вопрос одним сообщением. "
        "При необходимости, вы сможете прикрепить фото на следующем шаге."
    )
    await state.set_state(ExecutorSupportStates.creating_ticket_message)
    await callback.answer()

@router.message(ExecutorSupportStates.creating_ticket_message, F.text)
async def executor_ticket_message_received(message: types.Message, state: FSMContext):
    """Сохраняет текст обращения и предлагает прикрепить фото."""
    await state.update_data(ticket_text=message.text)
    await message.answer(
        "Спасибо! Теперь вы можете прикрепить одну фотографию, чтобы лучше описать проблему, или пропустить этот шаг.",
        reply_markup=get_executor_skip_photo_keyboard()
    )
    await state.set_state(ExecutorSupportStates.waiting_for_ticket_photo)

@router.callback_query(F.data == "executor_my_tickets")
async def executor_my_tickets_list(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает список обращений исполнителя."""
    user_tickets = await get_user_tickets(session, user_tg_id=callback.from_user.id)
    if not user_tickets:
        await callback.message.edit_text(
            "У вас пока нет обращений в поддержку.",
            reply_markup=get_executor_support_menu_keyboard()
        )
    else:
        await callback.message.edit_text(
            "Ваши обращения в поддержку:",
            reply_markup=get_executor_my_tickets_keyboard(user_tickets)
        )
    await callback.answer()

@router.callback_query(F.data.startswith("executor_view_ticket:"))
async def executor_view_ticket(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает полную переписку по выбранному тикету."""
    ticket_id = int(callback.data.split(":")[1])
    ticket = await get_ticket_by_id(session, ticket_id)

    if not ticket or ticket.user_tg_id != callback.from_user.id:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    history = f"<b>Обращение №{ticket.id} от {ticket.created_at.strftime('%d.%m.%Y')}</b>\n"
    history += f"Статус: <i>{ticket.status.value}</i>\n\n"

    last_photo_id = None
    for message in sorted(ticket.messages, key=lambda m: m.created_at):
        author = "Вы" if message.author == MessageAuthor.client else "Поддержка"
        time = message.created_at.strftime('%H:%M')
        history += f"<b>{author}</b> ({time}):\n{message.text}\n"
        if message.photo_file_id:
            history += "<i>К сообщению прикреплено фото.</i>\n"
            last_photo_id = message.photo_file_id
        history += "\n"

    keyboard = get_executor_view_ticket_keyboard(ticket)
    await callback.message.delete()

    if last_photo_id:
        try:
            # Фото могло быть загружено через любого бота, пробуем через executor-бота
            await callback.message.answer_photo(photo=last_photo_id, caption=history, reply_markup=keyboard)
        except Exception:
            await callback.message.answer(text=history, reply_markup=keyboard)
    else:
        await callback.message.answer(text=history, reply_markup=keyboard)
    await callback.answer()

async def finish_executor_ticket_creation(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings, photo_id: str | None = None):
    """Общая функция для завершения создания тикета от исполнителя."""
    user_data = await state.get_data()
    ticket_text = user_data.get("ticket_text")

    new_ticket = await create_ticket(
        session=session,
        user_tg_id=message.from_user.id,
        message_text=ticket_text,
        photo_id=photo_id
    )

    if new_ticket:
        await message.answer(
            f"✅ Спасибо! Ваше обращение №{new_ticket.id} принято в работу.",
            reply_markup=get_executor_main_keyboard()
        )

        admin_bot = bots["admin"]
        executor_bot = bots["executor"]  # Используем бот исполнителя, т.к. файл был отправлен именно ему

        admin_caption = (
            f"❗️ <b>Новое обращение от ИСПОЛНИТЕЛЯ №{new_ticket.id}</b>\n\n"
            f"<b>От:</b> @{message.from_user.username or message.from_user.full_name} ({message.from_user.id})\n\n"
            f"<b>Текст обращения:</b>\n{ticket_text}"
        )

        go_to_ticket_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Перейти к тикету", callback_data=f"admin_view_ticket:{new_ticket.id}")]
        ])

        if photo_id:
            photo_file = await executor_bot.get_file(photo_id)
            photo_bytes_io = await executor_bot.download_file(photo_file.file_path)
            photo_bytes = photo_bytes_io.read()
            photo_to_send = BufferedInputFile(photo_bytes, filename="photo.jpg")

            await admin_bot.send_photo(
                chat_id=config.admin_id,
                photo=photo_to_send,
                caption=admin_caption,
                reply_markup=go_to_ticket_keyboard
            )
        else:
            await admin_bot.send_message(
                config.admin_id,
                admin_caption,
                reply_markup=go_to_ticket_keyboard
            )

        user_tickets = await get_user_tickets(session, user_tg_id=message.from_user.id)
        await message.answer(
            "Ваши обращения в поддержку:",
            reply_markup=get_executor_my_tickets_keyboard(user_tickets)
        )
    else:
        await message.answer("Произошла ошибка при создании обращения. Пожалуйста, попробуйте снова.")
    await state.clear()


@router.message(ExecutorSupportStates.waiting_for_ticket_photo, F.photo)
async def executor_ticket_photo_received(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """Принимает фото и завершает создание тикета."""
    photo_id = message.photo[-1].file_id
    await finish_executor_ticket_creation(message, state, session, bots, config, photo_id)

@router.message(ExecutorSupportStates.waiting_for_ticket_photo, F.text == "➡️ Пропустить")
async def executor_ticket_photo_skipped(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """Пропускает шаг с фото и завершает создание тикета."""
    await finish_executor_ticket_creation(message, state, session, bots, config)

@router.callback_query(F.data == "executor_back_to_main_menu")
async def executor_back_to_main_menu(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает в главное меню поддержки."""
    await state.clear()
    await callback.message.edit_text(
        "Вы находитесь в разделе поддержки. Чем мы можем помочь?",
        reply_markup=get_executor_support_menu_keyboard()
    )
    await callback.answer()