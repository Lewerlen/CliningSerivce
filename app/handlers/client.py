import datetime
import logging
from contextlib import suppress
from typing import List
from zoneinfo import ZoneInfo
from aiogram import F, Router, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.db_queries import get_matching_executors
from app.config import Settings
from app.handlers.states import OrderStates, SupportStates, RatingStates, ChatStates
from app.keyboards.executor_kb import get_new_order_notification_keyboard, get_order_changes_confirmation_keyboard
from app.keyboards.client_kb import (
    ADDITIONAL_SERVICES, get_exit_chat_keyboard,
    get_support_menu_keyboard, get_reply_to_chat_keyboard,
    create_calendar,
    get_active_orders_keyboard,
    get_view_order_keyboard,
    get_archive_orders_keyboard,
    get_view_archive_order_keyboard,
    get_address_confirmation_keyboard,
    get_address_keyboard,
    get_additional_services_keyboard,
    get_bathroom_count_keyboard,
    get_cleaning_type_keyboard,
    get_confirmation_keyboard,
    get_edit_order_keyboard,
    get_main_menu_keyboard,
    get_order_name_keyboard,
    get_order_phone_keyboard,
    get_payment_keyboard,
    get_photo_keyboard,
    get_room_count_keyboard,
    get_time_keyboard,
    get_view_ticket_keyboard,
    get_my_tickets_keyboard,
    get_skip_photo_keyboard,
    get_rating_keyboard
)
from app.services.db_queries import (
    create_order,
    create_ticket,
    create_user,
    get_user,
    get_user_orders,
    update_order_datetime,
    update_order_services_and_price,
    update_order_address,
    update_order_rooms_and_price,
    update_order_status,
    OrderStatus, get_order_by_id,
    get_ticket_by_id,
    get_user_tickets,
    add_message_to_ticket,
    update_ticket_status,
    save_order_rating, update_executor_rating, update_user_phone, create_order_offer
)
from app.database.models import MessageAuthor, TicketStatus, User, Order
from app.services.price_calculator import ADDITIONAL_SERVICE_PRICES, calculate_preliminary_cost, calculate_total_cost
from app.services.yandex_maps_api import get_address_from_coords, get_address_from_text
from app.common.texts import STATUS_MAPPING, RUSSIAN_MONTHS_GENITIVE

TYUMEN_TZ = ZoneInfo("Asia/Yekaterinburg") # UTC+5, соответствует Тюмени
ALL_TIME_SLOTS = ["9:00 - 12:00", "12:00 - 15:00", "15:00 - 18:00", "18:00 - 21:00"]

router = Router()
@router.message(CommandStart())
async def cmd_start(message: types.Message, session: AsyncSession, state: FSMContext):
    """Обработчик команды /start."""
    await state.clear()
    user = await get_user(session, message.from_user.id)

    if user:
        # Если пользователь уже есть, просто приветствуем
        await message.answer(
            f"С возвращением, {user.name}!",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        # Если пользователя нет, СОЗДАЕМ ЕГО СРАЗУ
        await create_user(
            session,
            telegram_id=message.from_user.id,
            name=message.from_user.full_name
            # Телефон не передаем
        )
        await message.answer(
            f"Здравствуйте, {message.from_user.full_name}! Рады видеть вас в нашем сервисе.",
            reply_markup=get_main_menu_keyboard()
        )

@router.message(F.text == "📦 Заказать уборку")
async def start_order(message: types.Message, state: FSMContext):
    """Начинает сценарий оформления заказа."""
    await message.answer(
        "Отлично! Давайте рассчитаем стоимость. Выберите тип уборки:",
        reply_markup=get_cleaning_type_keyboard()
    )
    await state.set_state(OrderStates.choosing_cleaning_type)


@router.message(
    OrderStates.choosing_cleaning_type,
    F.text.in_({"🧽 Поддерживающая", "🧼 Генеральная", "🛠 После ремонта"})
)
async def handle_cleaning_type(message: types.Message, state: FSMContext):
    """Обрабатывает выбор типа уборки и спрашивает количество комнат."""
    # Сохраняем выбранный тип уборки
    await state.update_data(cleaning_type=message.text)

    await message.answer(
        "Записал. Теперь выберите количество комнат:",
        reply_markup=get_room_count_keyboard()
    )
    # Переводим пользователя на следующий шаг
    await state.set_state(OrderStates.choosing_room_count)

@router.message(
    OrderStates.choosing_room_count,
    F.text.in_({"1", "2", "3", "4", "5+"})
)
async def handle_room_count(message: types.Message, state: FSMContext):
    """Обрабатывает выбор количества комнат и спрашивает количество санузлов."""
    await state.update_data(room_count=message.text)
    await message.answer(
        "Принято. Теперь выберите количество санузлов:",
        reply_markup=get_bathroom_count_keyboard()
    )
    await state.set_state(OrderStates.choosing_bathroom_count)


@router.message(
    OrderStates.choosing_bathroom_count,
    F.text.in_({"1", "2", "3+"})
)
async def handle_bathroom_count(message: types.Message, state: FSMContext):
    """
    Обрабатывает выбор количества санузлов, рассчитывает предварительную стоимость
    и переходит к выбору доп. услуг.
    """
    # Сохраняем количество санузлов
    await state.update_data(bathroom_count=message.text)

    # Получаем все данные из состояния
    user_data = await state.get_data()

    # Рассчитываем стоимость
    cost = calculate_preliminary_cost(
        cleaning_type=user_data.get("cleaning_type"),
        room_count_str=user_data.get("room_count"),
        bathroom_count_str=user_data.get("bathroom_count")
    )

    await state.update_data(preliminary_cost=cost)

    # Сначала отправляем сообщение со стоимостью, которое убирает нижнюю клавиатуру
    await message.answer(
        f"Отлично! Предварительная стоимость уборки: <b>{cost} ₽</b>.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    # Затем отправляем сообщение с inline-клавиатурой доп. услуг
    await message.answer(
        "Теперь выберите дополнительные услуги, которые вам нужны, или нажмите 'Готово'.",
        reply_markup=get_additional_services_keyboard()
    )
    await state.set_state(OrderStates.choosing_additional_services)


@router.message(F.text == "💬 Мои заказы")
async def my_orders(message: types.Message, session: AsyncSession, state: FSMContext):
    """Отображает список активных заказов в виде кнопок."""
    await state.clear()  # На всякий случай сбрасываем состояние

    orders = await get_user_orders(session, client_tg_id=message.from_user.id)
    active_orders = [o for o in orders if o.status in (OrderStatus.new, OrderStatus.accepted, OrderStatus.in_progress, OrderStatus.pending_confirmation)]

    if not active_orders:
        await message.answer(
            "У вас нет активных заказов.",
            # Клавиатура с одной кнопкой для перехода в архив
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗂 Архив заказов", callback_data="view_archive")]
            ])
        )
        return

    await message.answer(
        "Ваши активные заказы:",
        reply_markup=get_active_orders_keyboard(active_orders)
    )

@router.callback_query(F.data.startswith("edit_order:"))
async def edit_order_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс редактирования заказа."""
    order_id = int(callback.data.split(":")[1])
    await state.update_data(order_id_to_edit=order_id)

    await callback.message.edit_text(
        f"Выбран заказ №{order_id}. Что вы хотите изменить?",
        reply_markup=get_edit_order_keyboard()
    )
    await state.set_state(OrderStates.editing_order)
    await callback.answer()


@router.callback_query(F.data.startswith("view_order:"))
async def view_order(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает подробную информацию о выбранном заказе."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    can_be_edited = False
    try:
        order_start_time_str = order.selected_time.split(' ')[0]
        order_datetime_str = f"{order.selected_date} {order_start_time_str}"
        naive_order_datetime = datetime.datetime.strptime(order_datetime_str, "%Y-%m-%d %H:%M")

        # Делаем время заказа "осведомленным" о часовом поясе
        aware_order_datetime = naive_order_datetime.replace(tzinfo=TYUMEN_TZ)

        # Сравниваем с текущим временем в той же таймзоне
        if aware_order_datetime - datetime.datetime.now(tz=TYUMEN_TZ) > datetime.timedelta(hours=12):
            can_be_edited = True
    except (ValueError, IndexError):
        pass  # Если что-то пошло не так с датой, просто не даем редактировать

    # Собираем информацию о доп. услугах
    selected_services_text = "\n".join(
        [f"    - {ADDITIONAL_SERVICES[item.service_key]}" for item in order.items]
    ) or "Нет"

    # Форматируем дату
    try:
        selected_date = datetime.datetime.strptime(order.selected_date, "%Y-%m-%d")
        formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
    except (ValueError, KeyError, TypeError):
        formatted_date = order.selected_date

    # Формируем текст
    order_details = (
        f"<b>Детали заказа №{order.id}</b>\n\n"
        f"<b>Статус:</b> <i>{STATUS_MAPPING.get(order.status, order.status.value)}</i>\n"
        f"<b>Тип уборки:</b> {order.cleaning_type}\n"
        f"<b>Комнат:</b> {order.room_count}, <b>Санузлов:</b> {order.bathroom_count}\n\n"
        f"<b>Дополнительные услуги:</b>\n{selected_services_text}\n\n"
        f"📍 <b>Адрес:</b> {order.address_text}\n"
        f"📅 <b>Дата:</b> {formatted_date}\n"
        f"🕒 <b>Время:</b> {order.selected_time}\n\n"
        f"💰 <b>ИТОГОВАЯ СТОИМОСТЬ: {order.total_price} ₽</b>"
    )

    await callback.message.edit_text(
        order_details,
        reply_markup=get_view_order_keyboard(order, can_be_edited=can_be_edited)
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_orders_list")
async def back_to_orders_list(callback: types.CallbackQuery, session: AsyncSession):
    """Возвращает к списку активных заказов."""
    # Мы не можем просто вызвать my_orders, так как это обработчик message,
    # а у нас callback. Поэтому мы дублируем его логику, но для callback.
    await callback.answer()
    orders = await get_user_orders(session, client_tg_id=callback.from_user.id)
    active_orders = [o for o in orders if o.status in (OrderStatus.new, OrderStatus.accepted, OrderStatus.in_progress)]

    if not active_orders:
        await callback.message.edit_text(
            "У вас нет активных заказов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗂 Архив заказов", callback_data="view_archive")]
            ])
        )
        return

    await callback.message.edit_text(
        "Ваши активные заказы:",
        reply_markup=get_active_orders_keyboard(active_orders)
    )


@router.callback_query(F.data == "view_archive")
async def view_archive(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает архив заказов."""
    await callback.answer()
    orders = await get_user_orders(session, client_tg_id=callback.from_user.id)
    completed_orders = [o for o in orders if o.status in (OrderStatus.completed, OrderStatus.cancelled)]

    if not completed_orders:
        await callback.message.edit_text(
            "Ваш архив пуст.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к активным заказам", callback_data="back_to_orders_list")]
            ])
        )
        return

    await callback.message.edit_text(
        "Архив ваших заказов:",
        reply_markup=get_archive_orders_keyboard(completed_orders)
    )

@router.callback_query(F.data.startswith("view_archive_order:"))
async def view_archive_order(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает подробную информацию о выбранном архивном заказе."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    # Форматирование деталей заказа (аналогично view_order)
    selected_services_text = "\n".join(
        [f"    - {ADDITIONAL_SERVICES[item.service_key]}" for item in order.items]
    ) or "Нет"
    try:
        selected_date = datetime.datetime.strptime(order.selected_date, "%Y-%m-%d")
        formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
    except (ValueError, KeyError, TypeError):
        formatted_date = order.selected_date

    order_details = (
        f"<b>Детали заказа №{order.id} (Архив)</b>\n\n"
        f"<b>Статус:</b> <i>{STATUS_MAPPING.get(order.status, order.status.value)}</i>\n"
    f"<b>Тип уборки:</b> {order.cleaning_type}\n"

    f"<b>Комнат:</b> {order.room_count}, <b>Санузлов:</b> {order.bathroom_count}\n\n"

    f"<b>Дополнительные услуги:</b>\n{selected_services_text}\n\n"

    f"📍 <b>Адрес:</b> {order.address_text}\n"

    f"📅 <b>Дата:</b> {formatted_date}\n"

    f"🕒 <b>Время:</b> {order.selected_time}\n\n"

    f"💰 <b>ИТОГОВАЯ СТОИМОСТЬ: {order.total_price} ₽</b>"
    )

    await callback.message.edit_text(
        order_details,
        reply_markup=get_view_archive_order_keyboard(order_id)
    )
    await callback.answer()

@router.callback_query(OrderStates.editing_order, F.data == "edit_datetime")
async def edit_order_datetime(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс изменения даты и времени для заказа."""
    await callback.message.delete()
    now = datetime.datetime.now()
    await callback.message.answer(
        "Пожалуйста, выберите новую дату для этого заказа:",
        reply_markup=await create_calendar(now.year, now.month)
    )
    # Переиспользуем существующий стейт выбора даты
    await state.set_state(OrderStates.choosing_date)
    await callback.answer()

@router.callback_query(OrderStates.editing_order, F.data == "back_to_my_orders")
async def back_to_orders_from_edit(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Возвращает пользователя к списку заказов из режима редактирования."""
    await state.clear()
    await callback.message.delete()
    # Просто вызываем функцию, которая отображает заказы
    await my_orders(callback.message, session, state)
    await callback.answer()

@router.callback_query(OrderStates.editing_order, F.data == "edit_services")
async def edit_order_services_start(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает процесс изменения доп. услуг для заказа."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")

    # Получаем актуальные данные о заказе из БД
    order = await get_order_by_id(session, order_id)
    if not order:
        await callback.answer("Не удалось найти заказ. Попробуйте снова.", show_alert=True)
        return

    # Рассчитываем базовую стоимость (без доп. услуг)
    preliminary_cost = calculate_preliminary_cost(
        cleaning_type=order.cleaning_type,
        room_count_str=order.room_count,
        bathroom_count_str=order.bathroom_count
    )

    # Получаем уже выбранные услуги
    selected_services = {item.service_key: item.quantity for item in order.items}

    # Сохраняем в состояние, чтобы потом пересчитать
    await state.update_data(preliminary_cost=preliminary_cost, selected_services=selected_services)

    await callback.message.edit_text(
        f"Текущая стоимость: {order.total_price} ₽.\n\n"
        "Выберите или снимите выбор с дополнительных услуг:",
        reply_markup=get_additional_services_keyboard(selected_services)
    )

    # Переводим в новый стейт для редактирования услуг
    await state.set_state(OrderStates.editing_additional_services)
    await callback.answer()

@router.callback_query(OrderStates.editing_order, F.data == "edit_address")
async def edit_address_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс изменения адреса."""
    await callback.message.delete()
    await callback.message.answer(
        "Пожалуйста, введите новый адрес или отправьте геолокацию.",
        reply_markup=get_address_keyboard()
    )
    # Мы переиспользуем существующий сценарий ввода адреса
    await state.set_state(OrderStates.entering_address)
    await callback.answer()

@router.callback_query(OrderStates.editing_order, F.data == "edit_rooms")
async def edit_rooms_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс изменения количества комнат."""
    await callback.message.delete()
    await callback.message.answer(
        "Пожалуйста, выберите новое количество комнат:",
        reply_markup=get_room_count_keyboard()
    )
    # Переводим в новый стейт
    await state.set_state(OrderStates.editing_room_count)
    await callback.answer()

@router.message(
    OrderStates.editing_room_count,
    F.text.in_({"1", "2", "3", "4", "5+"})
)
async def edit_room_count_chosen(message: types.Message, state: FSMContext):
    """Сохраняет новое количество комнат и спрашивает о санузлах."""
    await state.update_data(new_room_count=message.text)
    await message.answer(
        "Отлично. Теперь выберите новое количество санузлов:",
        reply_markup=get_bathroom_count_keyboard()
    )
    await state.set_state(OrderStates.editing_bathroom_count)


@router.message(
    OrderStates.editing_bathroom_count,
    F.text.in_({"1", "2", "3+"})
)
async def edit_bathroom_count_chosen(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """Сохраняет санузлы, пересчитывает стоимость и обновляет заказ."""
    await state.update_data(new_bathroom_count=message.text)
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")
    order = await get_order_by_id(session, order_id)

    if not order:
        await message.answer("Ошибка, заказ не найден.", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return

    new_preliminary_cost = calculate_preliminary_cost(
        cleaning_type=order.cleaning_type,
        room_count_str=user_data.get("new_room_count"),
        bathroom_count_str=user_data.get("new_bathroom_count")
    )
    additional_cost = sum(ADDITIONAL_SERVICE_PRICES.get(item.service_key, 0) * item.quantity for item in order.items)
    new_total_price = new_preliminary_cost + additional_cost
    updated_order = await update_order_rooms_and_price(
        session, order_id=order_id,
        new_room_count=user_data.get("new_room_count"), new_bathroom_count=user_data.get("new_bathroom_count"),
        new_total_price=new_total_price
    )

    if updated_order:
        await bots["admin"].send_message(
            config.admin_id,
            f"❗️ <b>В заказе №{order_id} изменены параметры.</b>\n"
            f"<b>Комнат:</b> {updated_order.room_count}, <b>Санузлов:</b> {updated_order.bathroom_count}\n"
            f"<b>Новая стоимость:</b> {updated_order.total_price} ₽"
        )

        if updated_order.executor_tg_id:
            order_pending = await update_order_status(session, order_id, OrderStatus.pending_confirmation)
            if order_pending:
                await message.answer(
                    f"Изменения для заказа №{order_id} сохранены. Мы уведомили исполнителя и ожидаем его подтверждения.",
                    reply_markup=get_main_menu_keyboard()
                )
                try:
                    new_executor_payment = round(new_total_price * 0.85)
                    await bots["executor"].send_message(
                        chat_id=updated_order.executor_tg_id,
                        text=(
                            f"❗️ <b>В заказе №{order_id} изменены параметры.</b>\n"
                            f"Новые параметры: {updated_order.room_count} комнат, {updated_order.bathroom_count} санузлов\n"
                            f"Новая выплата: {new_executor_payment} ₽\n\n"
                            "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
                        ),
                        reply_markup=get_order_changes_confirmation_keyboard(order_id)
                    )
                except Exception as e:
                    logging.error(f"Статус заказа {order_id} изменен, но НЕ удалось отправить уведомление: {e}")
                    await bots["admin"].send_message(
                        config.admin_id,
                        f"⚠️ <b>В заказе №{order_id} изменены параметры.</b>\n"
                        f"<b>НО не удалось уведомить исполнителя.</b> Свяжитесь с ним вручную."
                    )
            else:
                logging.error(f"Критическая ошибка: не удалось изменить статус заказа {order_id} на pending_confirmation.")
        else:
            await message.answer(
                f"✅ <b>Параметры заказа №{order_id} обновлены!</b>\n\n"
                f"<b>Комнат:</b> {updated_order.room_count}, <b>Санузлов:</b> {updated_order.bathroom_count}\n"
                f"💰 <b>Новая итоговая стоимость: {updated_order.total_price} ₽</b>",
                reply_markup=get_main_menu_keyboard()
            )
    else:
        await message.answer("Произошла ошибка при обновлении заказа.", reply_markup=get_main_menu_keyboard())
    await state.clear()

@router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order(callback: types.CallbackQuery, session: AsyncSession, bots: dict, config: Settings):
    """Обрабатывает отмену заказа."""
    order_id = int(callback.data.split(":")[1])

    # Меняем статус в БД
    updated_order = await update_order_status(session, order_id, OrderStatus.cancelled)

    if updated_order:
        # Редактируем исходное сообщение, убирая кнопку
        await callback.message.edit_text(
            f"<b>Заказ №{updated_order.id} от {updated_order.created_at.strftime('%d.%m.%Y')}</b>\n"
            f"Статус: <i>{STATUS_MAPPING.get(updated_order.status, updated_order.status.value)}</i>\n"
            f"Сумма: {updated_order.total_price} ₽\n"
            f"Адрес: {updated_order.address_text}"
        )
        await callback.answer("Заказ отменен.")

        # Отправляем уведомление админу
        await bots["admin"].send_message(
            chat_id=config.admin_id,
            text=f"❗️ <b>Клиент @{callback.from_user.username or callback.from_user.full_name} отменил заказ №{order_id}.</b>"
        )
    else:
        await callback.answer("Не удалось найти или обновить заказ.", show_alert=True)


@router.callback_query(F.data.startswith("repeat_order:"))
async def repeat_order(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """
    Обрабатывает повтор заказа, предзаполняя все данные и переходя к редактированию доп. услуг.
    """
    await callback.answer("Загружаю данные из вашего прошлого заказа...")
    order_id = int(callback.data.split(":")[1])

    old_order = await get_order_by_id(session, order_id)
    if not old_order:
        await callback.answer("Не удалось найти информацию о прошлом заказе.", show_alert=True)
        return

    # Рассчитываем предварительную стоимость на основе старого заказа
    preliminary_cost = calculate_preliminary_cost(
        cleaning_type=old_order.cleaning_type,
        room_count_str=old_order.room_count,
        bathroom_count_str=old_order.bathroom_count
    )

    # Собираем доп. услуги из старого заказа
    selected_services = {item.service_key: item.quantity for item in old_order.items}

    # "Клонируем" все данные из старого заказа в состояние
    await state.set_data({
        "cleaning_type": old_order.cleaning_type,
        "room_count": old_order.room_count,
        "bathroom_count": old_order.bathroom_count,
        "selected_services": selected_services,
        "address_text": old_order.address_text,
        "address_lat": old_order.address_lat,
        "address_lon": old_order.address_lon,
        "order_name": old_order.order_name,
        "order_phone": old_order.order_phone,
        "preliminary_cost": preliminary_cost,
        # Сразу рассчитываем полную стоимость для отображения
        "total_cost": calculate_total_cost(preliminary_cost, selected_services)
    })

    # Удаляем сообщение с деталями архивного заказа
    await callback.message.delete()

    # Отправляем сообщение с предложением изменить доп. услуги
    await callback.message.answer(
        f"Данные из заказа №{order_id} скопированы. Вы можете изменить набор дополнительных услуг.",
        reply_markup=get_additional_services_keyboard(selected_services)
    )

    # Переходим на шаг выбора доп. услуг, как в обычном заказе
    await state.set_state(OrderStates.choosing_additional_services)

@router.message(F.text == "📞 Поддержка")
async def support(message: types.Message, state: FSMContext):
    """Показывает главное меню раздела поддержки."""
    await state.clear() # Сбрасываем состояния на случай, если пользователь был в другом сценарии
    await message.answer(
        "Вы находитесь в разделе поддержки. Чем мы можем помочь?",
        reply_markup=get_support_menu_keyboard()
    )


# Константа для услуг, требующих указания количества
QUANTITY_SERVICES = {"win", "chair"}


@router.callback_query(
    StateFilter(OrderStates.choosing_additional_services, OrderStates.editing_additional_services),
    F.data.startswith("add_service_")
)
async def handle_add_service(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор доп. услуги: либо включает/выключает, либо запрашивает количество."""
    service_key = callback.data.split("_")[-1]
    user_data = await state.get_data()
    selected_services = user_data.get("selected_services", {}).copy()

    # --- Новая логика ---
    if service_key in QUANTITY_SERVICES:
        # Если услуга уже выбрана, удаляем ее
        if service_key in selected_services:
            del selected_services[service_key]
            await state.update_data(selected_services=selected_services)
            # Пересчитываем стоимость и обновляем клавиатуру
            await update_services_message(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                state=state
            )
        else:
            # Если услуга не выбрана, запрашиваем количество
            await state.update_data(
                current_service_for_quantity=service_key,
                services_message_id=callback.message.message_id  # Запоминаем ID главного сообщения
            )
            prompt_message = await callback.message.answer(
                f"Пожалуйста, укажите количество ({ADDITIONAL_SERVICES[service_key].split('(')[0].strip()}):"
            )
            # Запоминаем ID сообщения с вопросом, чтобы потом его удалить
            await state.update_data(quantity_prompt_message_id=prompt_message.message_id)
            await state.set_state(OrderStates.entering_service_quantity)
        await callback.answer()
        return

    if service_key in selected_services:
        del selected_services[service_key]
    else:
        selected_services[service_key] = 1  # Для обычных услуг количество всегда 1

    await state.update_data(selected_services=selected_services)
    # Вызываем обновленную функцию
    await update_services_message(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        state=state
    )
    await callback.answer()


@router.message(OrderStates.entering_service_quantity, F.text.isdigit())
async def handle_service_quantity(message: types.Message, state: FSMContext):
    """Обрабатывает введенное количество для услуги."""
    quantity = int(message.text)
    if not (1 <= quantity <= 10):
        await message.answer("Пожалуйста, введите число от 1 до 10.")
        return

    user_data = await state.get_data()
    service_key = user_data.get("current_service_for_quantity")
    selected_services = user_data.get("selected_services", {}).copy()
    services_message_id = user_data.get("services_message_id")
    quantity_prompt_message_id = user_data.get("quantity_prompt_message_id")

    selected_services[service_key] = quantity
    await state.update_data(selected_services=selected_services)

    # Возвращаемся к основному состоянию выбора услуг
    current_state_str = await state.get_state()
    if "editing" in current_state_str:
        await state.set_state(OrderStates.editing_additional_services)
    else:
        await state.set_state(OrderStates.choosing_additional_services)

        # Обновляем исходное сообщение с клавиатурой
        if services_message_id:
            await update_services_message(
                bot=message.bot,
                chat_id=message.chat.id,
                message_id=services_message_id,
                state=state
            )

    # Удаляем сообщение-вопрос от бота
    if quantity_prompt_message_id:
        with suppress(TelegramBadRequest):
            await message.bot.delete_message(message.chat.id, quantity_prompt_message_id)
    # Удаляем сообщение с ответом от пользователя
    with suppress(TelegramBadRequest):
        await message.delete()


# --- Вспомогательная функция для обновления сообщения ----
async def update_services_message(bot: Bot, chat_id: int, message_id: int, state: FSMContext):
    """Пересчитывает стоимость и обновляет сообщение с доп. услугами."""
    user_data = await state.get_data()
    selected_services = user_data.get("selected_services", {})
    preliminary_cost = user_data.get("preliminary_cost", 0)

    additional_cost = 0
    for service, quantity in selected_services.items():
        additional_cost += ADDITIONAL_SERVICE_PRICES.get(service, 0) * quantity

    total_cost = preliminary_cost + additional_cost
    await state.update_data(total_cost=total_cost)

    with suppress(TelegramBadRequest):
        await bot.edit_message_text(
            text=f"Итоговая стоимость уборки: <b>{total_cost} ₽</b>.\n\n"
                 f"Выберите дополнительные услуги или нажмите 'Готово'.",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=get_additional_services_keyboard(selected_services)
        )
    # Сохраняем ID сообщения, чтобы его можно было обновить в будущем
    await state.update_data(services_message_id=message_id)


@router.callback_query(
    StateFilter(OrderStates.choosing_additional_services, OrderStates.editing_additional_services),
    F.data == "done_services"
)
async def done_additional_services(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bots: dict,
                                   config: Settings):
    """Завершает выбор доп. услуг: либо сохраняет изменения, либо переходит к вводу адреса."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")

    if "total_cost" not in user_data:
        await state.update_data(total_cost=user_data.get("preliminary_cost"))
        user_data = await state.get_data()

    if order_id:
        new_services = user_data.get("selected_services", {})
        new_price = user_data.get("total_cost")
        updated_order = await update_order_services_and_price(session, order_id, new_services, new_price)

        if updated_order:
            # Уведомляем админа об изменениях в любом случае
            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ <b>В заказе №{order_id} изменены доп. услуги.</b>\nНовая стоимость: {new_price} ₽"
            )

            # Если исполнитель был назначен
            if updated_order.executor_tg_id:
                order_pending = await update_order_status(session, order_id, OrderStatus.pending_confirmation)
                if order_pending:
                    # Сообщаем клиенту, что ждем подтверждения
                    await callback.message.edit_text(
                        f"Изменения в доп. услугах для заказа №{order_id} сохранены.\n"
                        f"Новая стоимость: <b>{new_price} ₽</b>.\n\n"
                        "Мы уведомили исполнителя и ожидаем его подтверждения."
                    )
                    await callback.message.answer("Вы вернулись в главное меню.", reply_markup=get_main_menu_keyboard())
                    try:
                        new_executor_payment = round(new_price * 0.85)
                        await bots["executor"].send_message(
                            chat_id=updated_order.executor_tg_id,
                            text=(
                                f"❗️ <b>В заказе №{order_id} изменены доп. услуги.</b>\n"
                                f"Новая выплата: {new_executor_payment} ₽\n\n"
                                "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
                            ),
                            reply_markup=get_order_changes_confirmation_keyboard(order_id)
                        )
                    except Exception as e:
                        logging.error(f"Статус заказа {order_id} изменен, но НЕ удалось отправить уведомление: {e}")
                        await bots["admin"].send_message(
                            config.admin_id,
                            f"⚠️ <b>В заказе №{order_id} изменены доп. услуги.</b>\n"
                            f"<b>НО не удалось уведомить исполнителя.</b> Свяжитесь с ним вручную."
                        )
                else:
                    logging.error(f"Критическая ошибка: не удалось изменить статус заказа {order_id} на pending_confirmation.")
            # Если исполнителя не было
            else:
                await callback.message.edit_text(
                    f"Отлично! Дополнительные услуги для заказа №{order_id} были обновлены.\n"
                    f"Новая стоимость: <b>{new_price} ₽</b>"
                )
                await callback.message.answer("Вы вернулись в главное меню.", reply_markup=get_main_menu_keyboard())
        else:
            await callback.answer("Произошла ошибка при обновлении заказа.", show_alert=True)
        await state.clear()
    else:
        await callback.message.delete()
        await callback.message.answer(
            "Отлично! Теперь введите ваш адрес или отправьте геолокацию.",
            reply_markup=get_address_keyboard()
        )
        await state.set_state(OrderStates.entering_address)
    await callback.answer()

@router.message(OrderStates.entering_address, F.location)
async def handle_address_location(message: types.Message, state: FSMContext, config: Settings):
    """Обрабатывает геолокацию, получает адрес и просит подтверждения."""
    lat, lon = message.location.latitude, message.location.longitude
    address_text = await get_address_from_coords(lat, lon, config.api_keys.yandex_api_key)

    if address_text:
        await state.update_data(address_lat=lat, address_lon=lon, address_text=address_text)
        await message.answer(
            f"Ваш адрес: <b>{address_text}</b>.\nВсе верно?",
            reply_markup=get_address_confirmation_keyboard()
        )
        await state.set_state(OrderStates.confirming_address)
    else:
        await message.answer("Не удалось определить адрес. Пожалуйста, введите его вручную.")


@router.message(OrderStates.entering_address, F.text)
async def handle_address_text(message: types.Message, state: FSMContext, config: Settings):
    """Обрабатывает текстовый адрес, проверяет его и просит подтверждения."""
    if message.text == "⬅️ Назад к доп. услугам":
        await back_to_additional_services(message, state)
        return

    validated_address = await get_address_from_text(message.text, config.api_keys.yandex_api_key)
    if validated_address:
        await state.update_data(address_text=validated_address)
        await message.answer(
            f"Мы уточнили ваш адрес: <b>{validated_address}</b>.\nВсе верно?",
            reply_markup=get_address_confirmation_keyboard()
        )
        await state.set_state(OrderStates.confirming_address)
    else:
        await message.answer("Не удалось найти такой адрес. Попробуйте еще раз или отправьте геолокацию.")


@router.message(OrderStates.confirming_address, F.text == "✅ Да, все верно")
async def handle_address_confirmation(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """Обрабатывает подтверждение адреса: либо обновляет заказ, либо продолжает создание нового."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")

    if order_id:
        new_address = user_data.get("address_text")
        new_lat = user_data.get("address_lat")
        new_lon = user_data.get("address_lon")
        updated_order = await update_order_address(session, order_id, new_address, new_lat, new_lon)

        if updated_order:
            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ <b>В заказе №{order_id} изменен адрес.</b>\nНовый адрес: {new_address}"
            )

            if updated_order.executor_tg_id:
                order_pending = await update_order_status(session, order_id, OrderStatus.pending_confirmation)
                if order_pending:
                    await message.answer(
                        f"Изменения адреса для заказа №{order_id} сохранены. Мы уведомили исполнителя и ожидаем его подтверждения.",
                        reply_markup=get_main_menu_keyboard()
                    )
                    try:
                        await bots["executor"].send_message(
                            chat_id=updated_order.executor_tg_id,
                            text=(
                                f"❗️ <b>В заказе №{order_id} изменен адрес.</b>\n"
                                f"Новый адрес: {new_address}\n\n"
                                "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
                            ),
                            reply_markup=get_order_changes_confirmation_keyboard(order_id)
                        )
                    except Exception as e:
                        logging.error(f"Статус заказа {order_id} изменен, но НЕ удалось отправить уведомление: {e}")
                        await bots["admin"].send_message(
                            config.admin_id,
                            f"⚠️ <b>В заказе №{order_id} изменен адрес.</b>\n"
                            f"<b>НО не удалось уведомить исполнителя.</b> Свяжитесь с ним вручную."
                        )
                else:
                    logging.error(f"Критическая ошибка: не удалось изменить статус заказа {order_id} на pending_confirmation.")
            else:
                await message.answer(
                    f"Отлично! Адрес для заказа №{order_id} был успешно изменен.",
                    reply_markup=get_main_menu_keyboard()
                )
        else:
            await message.answer("Произошла ошибка при обновлении заказа.", reply_markup=get_main_menu_keyboard())
        await state.clear()
    else:
        now = datetime.datetime.now()
        await message.answer(
            "Отлично! Теперь выберите удобную дату:",
            reply_markup=await create_calendar(now.year, now.month)
        )
        await state.set_state(OrderStates.choosing_date)


@router.message(OrderStates.confirming_address, F.text == "✏️ Ввести вручную")
async def handle_reenter_address(message: types.Message, state: FSMContext):
    """Позволяет пользователю ввести адрес заново."""
    await message.answer(
        "Пожалуйста, введите адрес текстом (Город, улица, дом):",
        reply_markup=types.ReplyKeyboardRemove()  # Убираем кнопки подтверждения
    )
    await state.set_state(OrderStates.entering_address)


# --- БЛОК: ОБРАБОТЧИКИ ДЛЯ КАЛЕНДАРЯ ---
@router.callback_query(OrderStates.choosing_date, F.data.startswith("month_nav:"))
async def process_calendar_navigation(callback: types.CallbackQuery):
    """Обрабатывает навигацию 'вперед'/'назад' по календарю."""
    user_id = callback.from_user.id
    username = callback.from_user.username or "unknown"
    try:
        _, direction, year_str, month_str = callback.data.split(":")
        year, month = int(year_str), int(month_str)

        if direction == "next":
            month += 1
            if month > 12:
                month = 1
                year += 1
        elif direction == "prev":
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        with suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(
                reply_markup=await create_calendar(year, month)
            )
    except Exception as e:
        logging.error(
            f"Ошибка при навигации по календарю: {e}",
            extra={"username": username, "user_id": user_id}
        )
    finally:
        await callback.answer()


@router.callback_query(OrderStates.choosing_date, F.data == "back_to_address")
async def back_to_address_step(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает к шагу ввода адреса."""
    await callback.message.delete()
    await callback.message.answer(
        "Вы вернулись к вводу адреса. Введите адрес или отправьте геолокацию.",
        reply_markup=get_address_keyboard()
    )
    await state.set_state(OrderStates.entering_address)
    await callback.answer()


@router.callback_query(OrderStates.choosing_date, F.data.startswith("day:"))
async def process_date_selection(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор конкретной даты и переходит к выбору времени."""
    date_str = callback.data.split(":")[1]
    await state.update_data(selected_date=date_str)

    now_tyumen = datetime.datetime.now(TYUMEN_TZ)
    today_tyumen_str = now_tyumen.strftime("%Y-%m-%d")

    available_slots = ALL_TIME_SLOTS
    # Если выбран сегодняшний день, фильтруем слоты
    if date_str == today_tyumen_str:
        current_hour = now_tyumen.hour
        available_slots = [
            slot for slot in ALL_TIME_SLOTS if int(slot.split(':')[0]) > current_hour
        ]

    # Если на сегодня слотов не осталось
    if not available_slots:
        await callback.answer("На сегодня доступных слотов больше нет, выберите другую дату.", show_alert=True)
        # Обновляем календарь, чтобы пользователь мог выбрать заново
        now = datetime.datetime.now()
        await callback.message.edit_reply_markup(reply_markup=await create_calendar(now.year, now.month))
        return

    # Форматируем дату для красивого вывода
    try:
        selected_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
    except (ValueError, KeyError):
        formatted_date = date_str

    await callback.message.delete()
    await callback.message.answer(
        f"Вы выбрали дату: {formatted_date}.\n\nТеперь выберите удобный временной интервал:",
        reply_markup=get_time_keyboard(available_slots)
    )
    await state.set_state(OrderStates.choosing_time)
    await callback.answer()

@router.message(OrderStates.choosing_time, F.text == "⬅️ Назад к выбору даты")
async def back_to_date_selection(message: types.Message, state: FSMContext):
    """Возвращает к выбору даты (календарю)."""
    now = datetime.datetime.now()
    # Убираем обычную клавиатуру перед показом inline
    await message.answer("Вы вернулись к выбору даты.", reply_markup=types.ReplyKeyboardRemove())
    await message.answer(
        "Выберите новую дату:",
        reply_markup=await create_calendar(now.year, now.month)
    )
    await state.set_state(OrderStates.choosing_date)


@router.message(OrderStates.choosing_time, F.text)
async def handle_time_selection(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict,
                                config: Settings):
    """Обрабатывает выбор времени. Либо продолжает создание заказа, либо обновляет существующий."""
    if message.text == "⬅️ Назад к выбору даты":
        await back_to_date_selection(message, state)
        return

    await state.update_data(selected_time=message.text)
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")

    if order_id:
        new_date = user_data.get("selected_date")
        new_time = user_data.get("selected_time")
        updated_order = await update_order_datetime(session, order_id, new_date, new_time)

        if updated_order:
            try:
                selected_date = datetime.datetime.strptime(new_date, "%Y-%m-%d")
                formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
            except (ValueError, KeyError, TypeError):
                formatted_date = new_date

            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ <b>В заказе №{order_id} изменена дата/время.</b>\n"
                f"Новая дата: {formatted_date}\nНовое время: {new_time}"
            )

            if updated_order.executor_tg_id:
                order_pending = await update_order_status(session, order_id, OrderStatus.pending_confirmation)
                if order_pending:
                    await message.answer(
                        f"Изменения даты и времени для заказа №{order_id} сохранены. Мы уведомили исполнителя и ожидаем его подтверждения.",
                        reply_markup=get_main_menu_keyboard()
                    )
                    try:
                        await bots["executor"].send_message(
                            chat_id=updated_order.executor_tg_id,
                            text=(
                                f"❗️ <b>В заказе №{order_id} изменена дата/время.</b>\n"
                                f"Новая дата: {formatted_date}\nНовое время: {new_time}\n\n"
                                "Пожалуйста, подтвердите, что вы готовы выполнить заказ с этими изменениями."
                            ),
                            reply_markup=get_order_changes_confirmation_keyboard(order_id)
                        )
                    except Exception as e:
                        logging.error(f"Статус заказа {order_id} изменен, но НЕ удалось отправить уведомление: {e}")
                        await bots["admin"].send_message(
                            config.admin_id,
                            f"⚠️ <b>В заказе №{order_id} изменена дата/время.</b>\n"
                            f"<b>НО не удалось уведомить исполнителя.</b> Свяжитесь с ним вручную."
                        )
                else:
                    logging.error(
                        f"Критическая ошибка: не удалось изменить статус заказа {order_id} на pending_confirmation.")
            else:
                await message.answer(
                    f"Отлично! Дата и время для заказа №{order_id} были успешно изменены.",
                    reply_markup=get_main_menu_keyboard()
                )
        else:
            await message.answer("Произошла ошибка.", reply_markup=get_main_menu_keyboard())
        await state.clear()
    else:
        await message.answer(
            "Время выбрано. Можете загрузить фото или нажать 'Пропустить'.",
            reply_markup=get_photo_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_photo)

# --- БЛОК: ОБРАБОТЧИКИ ДЛЯ ШАГА С ФОТО ---

@router.message(OrderStates.waiting_for_photo, F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    """Принимает фото и добавляет его в список в состоянии FSM."""
    user_data = await state.get_data()
    photo_ids = user_data.get("photo_ids", [])

    if len(photo_ids) >= 10:
        await message.answer("Вы уже загрузили максимальное количество фотографий (10). Нажмите 'Продолжить'.")
        return

    photo_ids.append(message.photo[-1].file_id)
    await state.update_data(photo_ids=photo_ids)
    await message.answer(f"Фото {len(photo_ids)}/10 добавлено. Вы можете отправить еще или нажать 'Продолжить'.")


@router.message(OrderStates.waiting_for_photo, F.text.in_({"✅ Продолжить", "➡️ Пропустить"}))
async def continue_after_photo(message: types.Message, state: FSMContext):
    """Переходит к шагу ввода имени."""
    await message.answer(
        "Отлично. Теперь, пожалуйста, введите ваше имя для заказа.",
        reply_markup=get_order_name_keyboard()
    )
    await state.set_state(OrderStates.entering_order_name)


async def back_to_time_selection(message: types.Message, state: FSMContext):
    """Возвращает к выбору времени."""
    await message.answer(
        "Вы вернулись к выбору времени. Выберите удобный интервал:",
        reply_markup=get_time_keyboard(ALL_TIME_SLOTS)
    )
    await state.set_state(OrderStates.choosing_time)


# --- НОВЫЙ БЛОК: ОБРАБОТЧИКИ КОНТАКТНЫХ ДАННЫХ ---

@router.message(OrderStates.entering_order_name, F.text == "⬅️ Назад к фото")
async def back_to_photo_step(message: types.Message, state: FSMContext):
    """Возвращает к шагу загрузки фото."""
    await message.answer(
        "Вы вернулись к шагу загрузки фото. Можете прислать фото или пропустить.",
        reply_markup=get_photo_keyboard()
    )
    await state.set_state(OrderStates.waiting_for_photo)

async def _show_order_summary(message: types.Message, state: FSMContext):
    """Вспомогательная функция для формирования и отправки сводки по заказу."""
    user_data = await state.get_data()

    # Форматируем дату
    date_str = user_data.get('selected_date')
    try:
        selected_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
    except (ValueError, KeyError, TypeError):
        formatted_date = date_str

    # Собираем информацию о доп. услугах
    selected_services_data = user_data.get("selected_services", {})
    selected_services_text = "\n".join(
        [f"    - {ADDITIONAL_SERVICES[key]}" for key in selected_services_data.keys()]
    ) or "Нет"

    # Формируем итоговое сообщение
    summary_text = (
        f"<b>Пожалуйста, проверьте и подтвердите ваш заказ:</b>\n\n"
        f"<b>Тип уборки:</b> {user_data.get('cleaning_type')}\n"
        f"<b>Комнат:</b> {user_data.get('room_count')}, <b>Санузлов:</b> {user_data.get('bathroom_count')}\n\n"
        f"<b>Дополнительные услуги:</b>\n{selected_services_text}\n\n"
        f"📍 <b>Адрес:</b> {user_data.get('address_text', 'Не указан')}\n"
        f"📅 <b>Дата:</b> {formatted_date}\n"
        f"🕒 <b>Время:</b> {user_data.get('selected_time')}\n\n"
        f"👤 <b>Имя:</b> {user_data.get('order_name')}\n"
        f"📞 <b>Телефон:</b> {user_data.get('order_phone')}\n\n"
        f"💰 <b>ИТОГОВАЯ СТОИМОСТЬ: {user_data.get('total_cost')} ₽</b>"
    )

    await message.answer(text=summary_text, reply_markup=get_confirmation_keyboard())
    await state.set_state(OrderStates.confirming_order)

@router.message(OrderStates.entering_order_name, F.text)
async def handle_order_name(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обрабатывает имя. Если телефон уже есть - показывает сводку, иначе - запрашивает телефон."""
    if message.text == "⬅️ Назад к фото":
        await back_to_photo_step(message, state)
        return

    await state.update_data(order_name=message.text)
    user = await get_user(session, message.from_user.id)

    # Проверяем, есть ли у пользователя телефон в БАЗЕ ДАННЫХ
    if user and user.phone:
        await state.update_data(order_phone=user.phone)
        # Если телефон есть, сразу переходим к сводке
        await _show_order_summary(message, state)
    else:
        # Иначе, запрашиваем телефон
        await message.answer(
            f"Отлично, {message.text}. Теперь, пожалуйста, отправьте ваш номер телефона с помощью кнопки или введите его вручную.",
            reply_markup=get_order_phone_keyboard()
        )
        await state.set_state(OrderStates.entering_order_phone)

@router.message(OrderStates.confirming_order, F.text == "⬅️ Назад к вводу телефона")
async def back_to_phone_step(message: types.Message, state: FSMContext):
    """Возвращает к шагу ввода телефона."""
    await message.answer(
        "Вы вернулись к вводу номера телефона. Отправьте номер с помощью кнопки или введите вручную.",
        reply_markup=get_order_phone_keyboard()
    )
    await state.set_state(OrderStates.entering_order_phone)

@router.message(OrderStates.entering_order_phone, F.text == "⬅️ Назад к имени")
async def back_to_name_step(message: types.Message, state: FSMContext):
    """Возвращает к шагу ввода имени."""
    await message.answer(
        "Вы вернулись к вводу имени. Пожалуйста, введите имя для заказа:",
        reply_markup=get_order_name_keyboard()
    )
    await state.set_state(OrderStates.entering_order_name)

@router.message(OrderStates.entering_order_phone, (F.contact | F.text))
async def handle_order_phone(message: types.Message, state: FSMContext, session: AsyncSession):
    """Обрабатывает телефон, сохраняет его в БД и показывает финальную сводку."""
    phone_number = message.contact.phone_number if message.contact else message.text

    # Добавим простую проверку, что это не кнопка "Назад"
    if "назад" in phone_number.lower():
        await back_to_name_step(message, state)
        return

    await state.update_data(order_phone=phone_number)

    # Сохраняем телефон в профиль пользователя для будущих заказов
    await update_user_phone(session, message.from_user.id, phone_number)

    # Показываем сводку
    await _show_order_summary(message, state)


@router.message(OrderStates.confirming_order, F.text == "✅ Все верно, подтвердить")
async def handle_confirmation(message: types.Message, state: FSMContext):
    """Переходит к выбору способа оплаты."""
    await message.answer(
        "Отлично! Теперь выберите способ оплаты:",
        reply_markup=get_payment_keyboard()
    )
    await state.set_state(OrderStates.choosing_payment_method)


@router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order(callback: types.CallbackQuery, session: AsyncSession, bots: dict, config: Settings):
    """Обрабатывает отмену заказа."""
    order_id = int(callback.data.split(":")[1])

    # Сначала получаем заказ, чтобы знать, был ли у него исполнитель
    order_to_cancel = await get_order_by_id(session, order_id)
    if not order_to_cancel:
        await callback.answer("Не удалось найти заказ.", show_alert=True)
        return

    executor_id_to_notify = order_to_cancel.executor_tg_id

    # Меняем статус в БД
    updated_order = await update_order_status(session, order_id, OrderStatus.cancelled)

    if updated_order:
        # Редактируем исходное сообщение, убирая кнопку
        await callback.message.edit_text(
            f"<b>Заказ №{updated_order.id} от {updated_order.created_at.strftime('%d.%m.%Y')}</b>\n"
            f"Статус: <i>{STATUS_MAPPING.get(updated_order.status, updated_order.status.value)}</i>\n"
            f"Сумма: {updated_order.total_price} ₽\n"
            f"Адрес: {updated_order.address_text}"
        )
        await callback.answer("Заказ отменен.")

        # Уведомляем админа
        await bots["admin"].send_message(
            chat_id=config.admin_id,
            text=f"❗️ <b>Клиент @{callback.from_user.username or callback.from_user.full_name} отменил заказ №{order_id}.</b>"
        )

        # Уведомляем исполнителя, если он был назначен
        if executor_id_to_notify:
            try:
                await bots["executor"].send_message(
                    chat_id=executor_id_to_notify,
                    text=f"❗️<b>ОТМЕНА ЗАКАЗА</b>❗️\n\nКлиент отменил заказ №{order_id}, который был на вас назначен."
                )
            except Exception as e:
                logging.error(
                    f"Не удалось уведомить исполнителя {executor_id_to_notify} об отмене заказа {order_id}: {e}")
    else:
        await callback.answer("Не удалось найти или обновить заказ.", show_alert=True)


@router.message(OrderStates.choosing_payment_method, F.text == "💵 Наличными исполнителю")
async def handle_payment_cash(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict,
                              config: Settings):
    """
    Обрабатывает оплату наличными, сохраняет заказ и запускает процесс поиска исполнителя.
    """
    user_data = await state.get_data()
    new_order = await create_order(session, user_data, client_tg_id=message.from_user.id)
    await message.answer(
        "Спасибо! Ваш заказ принят в работу. Мы начали поиск исполнителя и скоро уведомим вас.",
        reply_markup=get_main_menu_keyboard()
    )

    # Уведомляем админа о новом заказе
    # (здесь остается ваш код для уведомления админа, я его сократил для краткости)
    summary_text_admin = f"✅ <b>Новый заказ! №{new_order.id}</b>..."
    await bots["admin"].send_message(chat_id=config.admin_id, text=summary_text_admin)

    # --- НОВАЯ ЛОГИКА ОЧЕРЕДИ ---
    executors = await get_matching_executors(
        session, new_order.selected_date, new_order.selected_time
    )

    if executors:
        # Берем первого исполнителя из отсортированного списка
        next_executor = executors[0]

        # Запускаем процесс предложения заказа (эта функция будет создана ниже)
        await offer_order_to_executor(session, bots, new_order, next_executor)
    else:
        await bots["admin"].send_message(
            config.admin_id,
            f"❗️<b>Внимание!</b> На новый заказ №{new_order.id} не найдено подходящих исполнителей."
        )

    await state.clear()


async def offer_order_to_executor(session: AsyncSession, bots: dict, order: Order, executor: User):
    """Отправляет предложение одному исполнителю и создает запись в OrderOffer."""
    now = datetime.datetime.now(TYUMEN_TZ)
    order_start_time = datetime.datetime.strptime(
        f"{order.selected_date} {order.selected_time.split(' ')[0]}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=TYUMEN_TZ)

    time_to_order = order_start_time - now

    # Определяем время на ответ
    if time_to_order < datetime.timedelta(hours=24):
        timeout_minutes = 15
    elif time_to_order < datetime.timedelta(days=3):
        timeout_minutes = 30
    else:
        timeout_minutes = 60

    expires_at = now + datetime.timedelta(minutes=timeout_minutes)

    # Убираем информацию о таймзоне перед записью в БД
    naive_expires_at = expires_at.replace(tzinfo=None)

    # Создаем предложение в БД с "наивным" временем
    await create_order_offer(session, order.id, executor.telegram_id, naive_expires_at)

    # Уведомляем исполнителя
    executor_payment = round(order.total_price * 0.85)
    notification_text = (
        f"🔥 <b>Новый заказ №{order.id}</b>\n\n"
        f"<b>Дата и время:</b> {order.selected_date}, {order.selected_time}\n"
        f"💰 <b>Ваша выплата:</b> {executor_payment} ₽\n\n"
        f"<i>У вас есть {timeout_minutes} минут, чтобы принять решение.</i>"
    )
    notification_keyboard = get_new_order_notification_keyboard(order.id, timeout_minutes)

    try:
        await bots["executor"].send_message(
            chat_id=executor.telegram_id,
            text=notification_text,
            reply_markup=notification_keyboard
        )
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление исполнителю {executor.telegram_id}: {e}")

@router.message(OrderStates.choosing_payment_method, F.text == "💳 Онлайн-оплата")
async def handle_payment_online(message: types.Message): # <--- УБРАН state
    """Обрабатывает онлайн-оплату."""
    await message.answer(
        "Раздел онлайн-оплаты находится в разработке. "
        "Пожалуйста, выберите оплату наличными на данный момент.",
        reply_markup=get_payment_keyboard()
    )

# --- ЕДИНЫЙ БЛОК ОБРАБОТЧИКОВ "НАЗАД" ---

@router.message(OrderStates.choosing_cleaning_type, F.text == "⬅️ Назад в меню")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    """Возвращает в главное меню."""
    await state.clear()
    await message.answer(
        "Вы вернулись в главное меню.",
        reply_markup=get_main_menu_keyboard()
    )

@router.message(OrderStates.choosing_room_count, F.text == "⬅️ Назад")
async def back_to_cleaning_type(message: types.Message, state: FSMContext):
    """Возвращает к выбору типа уборки."""
    await message.answer(
        "Вы вернулись к выбору типа уборки. Выберите тип:",
        reply_markup=get_cleaning_type_keyboard()
    )
    await state.set_state(OrderStates.choosing_cleaning_type)

@router.message(OrderStates.choosing_bathroom_count, F.text == "⬅️ Назад")
async def back_to_room_count(message: types.Message, state: FSMContext):
    """Возвращает к выбору количества комнат."""
    await message.answer(
        "Вы вернулись к выбору количества комнат. Выберите количество:",
        reply_markup=get_room_count_keyboard()
    )
    await state.set_state(OrderStates.choosing_room_count)

@router.callback_query(OrderStates.choosing_additional_services, F.data == "back_to_bathrooms")
async def back_to_bathroom_count(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает к выбору количества санузлов."""
    await callback.message.delete()
    await callback.message.answer(
        "Вы вернулись к выбору количества санузлов.",
        reply_markup=get_bathroom_count_keyboard()
    )
    await state.set_state(OrderStates.choosing_bathroom_count)
    await callback.answer()

@router.message(OrderStates.entering_address, F.text == "⬅️ Назад к доп. услугам")
async def back_to_additional_services(message: types.Message, state: FSMContext):
    """Возвращает к выбору доп. услуг."""
    user_data = await state.get_data()
    selected_services = user_data.get("selected_services", set())
    total_cost = user_data.get("total_cost", user_data.get("preliminary_cost", 0))

    await message.answer(
        f"Итоговая стоимость уборки: <b>{total_cost} ₽</b>.\n\n"
        f"Вы вернулись к выбору дополнительных услуг.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await message.answer(
        "Выберите услуги:",
        reply_markup=get_additional_services_keyboard(selected_services)
    )
    await state.set_state(OrderStates.choosing_additional_services)

# --- КОНЕЦ БЛОКА ---

# --- БЛОК: ОБРАБОТЧИКИ ДЛЯ СИСТЕМЫ ПОДДЕРЖКИ ---

@router.callback_query(F.data == "create_ticket")
async def create_ticket_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс создания нового тикета."""
    await callback.message.edit_text(
        "Пожалуйста, подробно опишите вашу проблему или вопрос одним сообщением. "
        "При необходимости, вы сможете прикрепить фото на следующем шаге."
    )
    await state.set_state(SupportStates.creating_ticket_message)
    await callback.answer()


@router.message(SupportStates.creating_ticket_message, F.text)
async def create_ticket_message_received(message: types.Message, state: FSMContext):
    """Сохраняет текст обращения и предлагает прикрепить фото."""
    # Сохраняем текст будущего тикета в состояние
    await state.update_data(ticket_text=message.text)

    await message.answer(
        "Спасибо! Теперь вы можете прикрепить одну фотографию, чтобы лучше описать проблему, или пропустить этот шаг.",
        reply_markup=get_skip_photo_keyboard()
    )
    # Переводим на шаг ожидания фото
    await state.set_state(SupportStates.waiting_for_ticket_photo)

@router.callback_query(F.data == "my_tickets")
async def my_tickets_list(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает список обращений пользователя."""
    user_tickets = await get_user_tickets(session, user_tg_id=callback.from_user.id)

    if not user_tickets:
        await callback.message.edit_text(
            "У вас пока нет обращений в поддержку.",
            reply_markup=get_support_menu_keyboard() # Возвращаем клавиатуру меню поддержки
        )
    else:
        await callback.message.edit_text(
            "Ваши обращения в поддержку:",
            reply_markup=get_my_tickets_keyboard(user_tickets)
        )
    await callback.answer()

@router.callback_query(F.data == "back_to_support_menu")
async def back_to_support_menu(callback: types.CallbackQuery):
    """Возвращает в главное меню поддержки."""
    await callback.message.edit_text(
        "Вы находитесь в разделе поддержки. Чем мы можем помочь?",
        reply_markup=get_support_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_ticket:"))
async def view_ticket(callback: types.CallbackQuery, session: AsyncSession):
    """Показывает полную переписку по выбранному тикету, включая последнее фото."""
    ticket_id = int(callback.data.split(":")[1])
    ticket = await get_ticket_by_id(session, ticket_id)

    if not ticket or ticket.user_tg_id != callback.from_user.id:
        await callback.answer("Тикет не найден.", show_alert=True)
        return

    # --- Новая логика сборки ---
    history = f"<b>Обращение №{ticket.id} от {ticket.created_at.strftime('%d.%m.%Y')}</b>\n"
    history += f"Статус: <i>{ticket.status.value}</i>\n\n"

    last_photo_id = None
    # Собираем историю и ищем последнее фото в переписке
    for message in sorted(ticket.messages, key=lambda m: m.created_at):
        author = "Вы" if message.author == MessageAuthor.client else "Поддержка"
        time = message.created_at.strftime('%H:%M')
        history += f"<b>{author}</b> ({time}):\n{message.text}\n"
        if message.photo_file_id:
            history += "<i>К сообщению прикреплено фото.</i>\n"
            last_photo_id = message.photo_file_id # Запоминаем ID последнего фото
        history += "\n"

    keyboard = get_view_ticket_keyboard(ticket)

    # Удаляем предыдущее сообщение (список тикетов или уведомление) для чистоты
    await callback.message.delete()

    if last_photo_id:
        # Если в истории есть фото, отправляем последнее из них с подписью
        try:
            await callback.message.answer_photo(
                photo=last_photo_id,
                caption=history,
                reply_markup=keyboard
            )
        except TelegramBadRequest:
            # Если file_id по какой-то причине невалиден, отправляем просто текст
            await callback.message.answer(text=history, reply_markup=keyboard)
    else:
        # Если в истории нет фото, отправляем просто текстовое сообщение
        await callback.message.answer(text=history, reply_markup=keyboard)

    await callback.answer()


@router.callback_query(F.data.startswith("reply_ticket:"))
async def reply_to_ticket_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс ответа на тикет."""
    ticket_id = int(callback.data.split(":")[1])
    await state.update_data(replying_ticket_id=ticket_id)

    # Удаляем старое сообщение с историей, чтобы не мешалось
    await callback.message.delete()
    # И присылаем новое с просьбой ввести ответ
    await callback.message.answer(
        "Пожалуйста, напишите ваш ответ одним сообщением."
    )
    await state.set_state(SupportStates.replying_to_ticket)
    await callback.answer()


@router.message(SupportStates.replying_to_ticket, F.text)
async def reply_to_ticket_message_received(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict,
                                           config: Settings):
    """Принимает ответ клиента, добавляет его в тикет и уведомляет админа."""
    user_data = await state.get_data()
    ticket_id = user_data.get("replying_ticket_id")

    # Добавляем сообщение в БД
    await add_message_to_ticket(
        session=session,
        ticket_id=ticket_id,
        author=MessageAuthor.client,
        text=message.text
    )

    await message.answer("✅ Ваш ответ отправлен в поддержку.")
    await state.clear()

    # --- ИСПРАВЛЕНИЕ №2: Добавляем кнопку для админа ---
    admin_text = (
        f"💬 <b>Получен ответ по тикету №{ticket_id}</b>\n\n"
        f"<b>От клиента:</b> @{message.from_user.username or message.from_user.full_name}\n\n"
        f"<b>Текст:</b>\n{message.text}"
    )
    go_to_ticket_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➡️ Перейти к тикету", callback_data=f"admin_view_ticket:{ticket_id}")]
    ])
    await bots["admin"].send_message(
        config.admin_id,
        admin_text,
        reply_markup=go_to_ticket_keyboard
    )

    # --- ИСПРАВЛЕНИЕ №1: Правильно показываем обновленный список тикетов ---
    user_tickets = await get_user_tickets(session, user_tg_id=message.from_user.id)
    await message.answer(
        "Ваши обращения в поддержку:",
        reply_markup=get_my_tickets_keyboard(user_tickets)
    )


@router.callback_query(F.data.startswith("close_ticket:"))
async def close_ticket(callback: types.CallbackQuery, session: AsyncSession):
    """Закрывает тикет по запросу пользователя."""
    ticket_id = int(callback.data.split(":")[1])

    await update_ticket_status(session, ticket_id, TicketStatus.closed)

    await callback.answer("Обращение закрыто.", show_alert=True)

    # Обновляем сообщение с историей, чтобы показать новый статус
    await view_ticket(callback, session)


async def finish_ticket_creation(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict,
                                 config: Settings, photo_id: str | None = None):
    """Общая функция для завершения создания тикета."""
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
            reply_markup=types.ReplyKeyboardRemove()
        )

        # --- НОВАЯ ЛОГИКА УВЕДОМЛЕНИЯ АДМИНА ---
        admin_bot = bots["admin"]
        client_bot = bots["client"]

        # Формируем текст для подписи к фото или для отдельного сообщения
        admin_caption = (
            f"❗️ <b>Новое обращение в поддержку №{new_ticket.id}</b>\n\n"
            f"<b>От клиента:</b> @{message.from_user.username or message.from_user.full_name} ({message.from_user.id})\n\n"
            f"<b>Текст обращения:</b>\n{ticket_text}"
        )

        # Создаем inline-кнопку для быстрого перехода к тикету
        go_to_ticket_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Перейти к тикету", callback_data=f"admin_view_ticket:{new_ticket.id}")]
        ])

        if photo_id:
            # Скачиваем фото во временный объект в памяти
            photo_file = await client_bot.get_file(photo_id)
            photo_bytes_io = await client_bot.download_file(photo_file.file_path)
            photo_bytes = photo_bytes_io.read()  # Читаем байты из объекта BytesIO

            # Оборачиваем байты в BufferedInputFile для отправки
            photo_to_send = BufferedInputFile(photo_bytes, filename="photo.jpg")

            # Отправляем фото с подписью и КНОПКОЙ от имени АДМИН-БОТА
            await admin_bot.send_photo(
                chat_id=config.admin_id,
                photo=photo_to_send,
                caption=admin_caption,
                reply_markup=go_to_ticket_keyboard
            )
        else:
            # Если фото нет, просто отправляем текст и КНОПКУ от имени АДМИН-БОТА
            await admin_bot.send_message(
                config.admin_id,
                admin_caption,
                reply_markup=go_to_ticket_keyboard
            )

        # Показываем обновленный список тикетов клиенту
        user_tickets = await get_user_tickets(session, user_tg_id=message.from_user.id)
        await message.answer(
            "Ваши обращения в поддержку:",
            reply_markup=get_my_tickets_keyboard(user_tickets)
        )
    else:
        await message.answer("Произошла ошибка при создании обращения. Пожалуйста, попробуйте снова.")

    await state.clear()


@router.message(SupportStates.waiting_for_ticket_photo, F.photo)
async def ticket_photo_received(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict,
                                config: Settings):
    """Принимает фото и завершает создание тикета."""
    photo_id = message.photo[-1].file_id
    await finish_ticket_creation(message, state, session, bots, config, photo_id)


@router.message(SupportStates.waiting_for_ticket_photo, F.text == "➡️ Пропустить")
async def ticket_photo_skipped(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict,
                               config: Settings):
    """Пропускает шаг с фото и завершает создание тикета."""
    await finish_ticket_creation(message, state, session, bots, config)


@router.message(SupportStates.waiting_for_ticket_photo, F.text == "⬅️ Отменить создание тикета")
async def ticket_creation_cancelled(message: types.Message, state: FSMContext):
    """Отменяет создание тикета и возвращает в меню поддержки."""
    await state.clear()
    await message.answer(
        "Создание обращения отменено.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    # Показываем меню поддержки
    await message.answer(
        "Вы находитесь в разделе поддержки. Чем мы можем помочь?",
        reply_markup=get_support_menu_keyboard()
    )

# --- БЛОК: ОБРАБОТЧИКИ ОЦЕНКИ И ОТЗЫВА ---

@router.callback_query(F.data.startswith("rate_order:"))
async def rate_order_start(callback: types.CallbackQuery, state: FSMContext):
    """Начинает процесс оценки заказа."""
    order_id = int(callback.data.split(":")[1])
    await state.update_data(order_id_for_rating=order_id)

    await callback.message.edit_text(
        "Пожалуйста, оцените качество выполненной работы:",
        reply_markup=get_rating_keyboard(order_id)
    )
    await state.set_state(RatingStates.waiting_for_rating)
    await callback.answer()

@router.callback_query(RatingStates.waiting_for_rating, F.data.startswith("set_rating:"))
async def handle_rating(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Обрабатывает выбор оценки и запрашивает текстовый отзыв."""
    _, order_id_str, rating_str = callback.data.split(":")
    order_id = int(order_id_str)
    rating = int(rating_str)

    await state.update_data(current_rating=rating)

    await callback.message.edit_text(
        f"Вы поставили оценку: {'⭐' * rating}\n\n"
        "Спасибо! Теперь, пожалуйста, напишите небольшой отзыв о работе исполнителя. "
        "Ваш отзыв поможет нам стать лучше."
    )
    await state.set_state(RatingStates.waiting_for_review)
    await callback.answer()

@router.message(RatingStates.waiting_for_review, F.text)
async def handle_review(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings):
    """Сохраняет отзыв, обновляет рейтинг исполнителя и завершает процесс."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_for_rating")
    rating = user_data.get("current_rating")
    review_text = message.text

    # 1. Сохраняем оценку и отзыв в заказе
    order = await save_order_rating(session, order_id, rating, review_text)

    if not order or not order.executor_tg_id:
        await message.answer("Произошла ошибка, не удалось сохранить ваш отзыв.")
        await state.clear()
        return

    # 2. Обновляем общий рейтинг исполнителя
    await update_executor_rating(session, order.executor_tg_id)

    await message.answer(
        "🎉 Спасибо за ваш отзыв! Мы ценим ваше мнение.",
        reply_markup=get_main_menu_keyboard()
    )

    # 3. Уведомляем исполнителя о новой оценке
    try:
        executor_bot = bots.get("executor")
        await executor_bot.send_message(
            chat_id=order.executor_tg_id,
            text=(
                f"🎉 Поздравляем! Вы получили новый отзыв по заказу №{order_id}.\n\n"
                f"<b>Оценка:</b> {'⭐' * rating}\n"
                f"<b>Отзыв клиента:</b> {review_text}\n\n"
                "Ваш общий рейтинг был обновлен."
            )
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление об оценке исполнителю {order.executor_tg_id}: {e}")

    await state.clear()

# --- КОНЕЦ БЛОКА ---

# --- БЛОК: ЧАТ С ИСПОЛНИТЕЛЕМ ---

@router.callback_query(F.data.startswith("start_chat:"))
async def start_chat_with_executor(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Начинает чат с исполнителем по конкретному заказу."""
    order_id = int(callback.data.split(":")[1])
    order = await get_order_by_id(session, order_id)

    if not order or not order.executor_tg_id:
        await callback.answer("Не удалось найти исполнителя по этому заказу.", show_alert=True)
        return

    await state.set_state(ChatStates.in_chat)
    await state.update_data(chat_partner_id=order.executor_tg_id, order_id=order.id)

    await callback.message.answer(
        f"Вы вошли в чат с исполнителем по заказу №{order.id}.\n"
        "Все сообщения, которые вы сюда отправите, будут пересланы ему. "
        "Чтобы выйти, нажмите кнопку ниже.",
        reply_markup=get_exit_chat_keyboard()
    )
    await callback.answer()


@router.message(ChatStates.in_chat, F.text == "⬅️ Выйти из чата")
async def exit_chat_client(message: types.Message, state: FSMContext):
    """Обрабатывает выход из чата для клиента."""
    await state.clear()
    await message.answer(
        "Вы вышли из чата. Возвращаю в главное меню.",
        reply_markup=get_main_menu_keyboard()
    )


@router.message(ChatStates.in_chat)
async def forward_message_to_executor(message: types.Message, state: FSMContext, bots: dict):
    """Пересылает сообщение от клиента исполнителю."""
    user_data = await state.get_data()
    partner_id = user_data.get("chat_partner_id")
    order_id = user_data.get("order_id")

    if not partner_id:
        return

    # Если пользователь пытается отправить альбом, вежливо просим этого не делать
    if message.media_group_id:
        await message.answer("Пожалуйста, отправляйте фотографии по одной за раз.")
        return

    executor_bot = bots.get("executor")
    prefix = f"💬 <b>[Клиент | Заказ №{order_id}]:</b>\n"
    reply_keyboard = get_reply_to_chat_keyboard(order_id)

    try:
        if message.text:
            await executor_bot.send_message(partner_id, f"{prefix}{message.text}", reply_markup=reply_keyboard)
        elif message.photo:
            photo_file = await message.bot.get_file(message.photo[-1].file_id)
            photo_bytes_io = await message.bot.download_file(photo_file.file_path)
            photo_to_send = BufferedInputFile(photo_bytes_io.read(), filename="photo.jpg")

            await executor_bot.send_photo(
                chat_id=partner_id,
                photo=photo_to_send,
                caption=f"{prefix}{message.caption or ''}",
                reply_markup=reply_keyboard
            )

        await message.answer("✅ Ваше сообщение отправлено.")

    except Exception as e:
        logging.error(f"Ошибка пересылки сообщения исполнителю {partner_id}: {e}")
        await message.answer("Не удалось доставить сообщение. Попробуйте позже.")

# --- КОНЕЦ БЛОКА ---