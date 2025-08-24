import datetime
import logging
from contextlib import suppress
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.handlers.states import OrderStates
from app.keyboards.client_kb import (
    ADDITIONAL_SERVICES,
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
)
from app.services.db_queries import (
    create_order,
    create_user,
    get_user,
    get_user_orders,
    update_order_datetime,
    update_order_services_and_price,
    update_order_address,
    update_order_rooms_and_price,
    update_order_status,
    OrderStatus, get_order_by_id
)
from app.services.price_calculator import ADDITIONAL_SERVICE_PRICES, calculate_preliminary_cost
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
    active_orders = [o for o in orders if o.status in (OrderStatus.new, OrderStatus.accepted, OrderStatus.in_progress)]

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
        aware_order_datetime = naive_order_datetime.replace(tzinfo=TYUMEN_TZ)
        if aware_order_datetime - datetime.datetime.now(TYUMEN_TZ) > datetime.timedelta(hours=12):
            can_be_edited = True
    except (ValueError, IndexError):
        pass # Если что-то пошло не так с датой, просто не даем редактировать

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
        reply_markup=get_view_order_keyboard(order_id, can_be_edited=can_be_edited)
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
    selected_services = {item.service_key for item in order.items}

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

    # Получаем оригинальный заказ, чтобы знать тип уборки и доп. услуги
    order = await get_order_by_id(session, order_id)
    if not order:
        await message.answer("Произошла ошибка, не удалось найти заказ.", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return

    # --- Главная логика пересчета ---
    # 1. Считаем новую базовую стоимость
    new_preliminary_cost = calculate_preliminary_cost(
        cleaning_type=order.cleaning_type,
        room_count_str=user_data.get("new_room_count"),
        bathroom_count_str=user_data.get("new_bathroom_count")
    )
    # 2. Считаем стоимость доп. услуг (они не изменились)
    additional_cost = sum(ADDITIONAL_SERVICE_PRICES.get(item.service_key, 0) for item in order.items)
    # 3. Складываем для получения итоговой цены
    new_total_price = new_preliminary_cost + additional_cost

    # Обновляем заказ в базе данных
    updated_order = await update_order_rooms_and_price(
        session,
        order_id=order_id,
        new_room_count=user_data.get("new_room_count"),
        new_bathroom_count=user_data.get("new_bathroom_count"),
        new_total_price=new_total_price
    )

    if updated_order:
        await message.answer(
            f"✅ <b>Параметры заказа №{order_id} обновлены!</b>\n\n"
            f"<b>Комнат:</b> {updated_order.room_count}, <b>Санузлов:</b> {updated_order.bathroom_count}\n"
            f"💰 <b>Новая итоговая стоимость: {updated_order.total_price} ₽</b>",
            reply_markup=get_main_menu_keyboard()
        )
        # Уведомление админу
        await bots["admin"].send_message(
            config.admin_id,
            f"❗️ <b>В заказе №{order_id} изменены параметры.</b>\n"
            f"<b>Комнат:</b> {updated_order.room_count}, <b>Санузлов:</b> {updated_order.bathroom_count}\n"
            f"<b>Новая стоимость:</b> {updated_order.total_price} ₽"
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
    """Обрабатывает повтор заказа, предзаполняя все данные."""
    await callback.answer("Заполняю данные из вашего прошлого заказа...")
    order_id = int(callback.data.split(":")[1])

    old_order = await get_order_by_id(session, order_id)
    if not old_order:
        await callback.answer("Не удалось найти информацию о прошлом заказе.", show_alert=True)
        return

    # "Клонируем" данные из старого заказа в состояние
    await state.set_data({
        "cleaning_type": old_order.cleaning_type,
        "room_count": old_order.room_count,
        "bathroom_count": old_order.bathroom_count,
        "selected_services": {item.service_key for item in old_order.items},
        "address_text": old_order.address_text,
        "address_lat": old_order.address_lat,
        "address_lon": old_order.address_lon,
        "order_name": old_order.order_name,
        "order_phone": old_order.order_phone,
        "total_cost": old_order.total_price
    })

    user_data = await state.get_data()

    # --- Формируем итоговое сообщение (этот блок кода можно вынести в отдельную функцию) ---
    selected_services_keys = user_data.get("selected_services", set())
    selected_services_text = "\n".join(
        [f"    - {ADDITIONAL_SERVICES[key]}" for key in selected_services_keys]
    ) or "Нет"

    summary_text = (
        f"<b>Пожалуйста, проверьте ваш новый заказ:</b>\n\n"
        f"<i>Все данные скопированы из заказа №{order_id}. "
        f"Вы можете изменить дату и время на следующих шагах.</i>\n\n"
        f"<b>Тип уборки:</b> {user_data.get('cleaning_type')}\n"
        f"<b>Комнат:</b> {user_data.get('room_count')}, <b>Санузлов:</b> {user_data.get('bathroom_count')}\n\n"
        f"<b>Дополнительные услуги:</b>\n{selected_services_text}\n\n"
        f"💰 <b>ИТОГОВАЯ СТОИМОСТЬ: {user_data.get('total_cost')} ₽</b>"
    )

    # Удаляем сообщение с деталями заказа, чтобы не мешало
    await callback.message.delete()

    # Отправляем предзаполненный заказ и переводим на шаг выбора даты, чтобы можно было ее изменить
    now = datetime.datetime.now()
    await callback.message.answer(summary_text)
    await callback.message.answer(
        "Пожалуйста, выберите новую дату для этого заказа:",
        reply_markup=await create_calendar(now.year, now.month)
    )
    await state.set_state(OrderStates.choosing_date)

@router.message(F.text == "📞 Поддержка")
async def support(message: types.Message):
    """Обработчик кнопки 'Поддержка'."""
    await message.answer("Это раздел поддержки. Вскоре мы его настроим.")


@router.callback_query(
    StateFilter(OrderStates.choosing_additional_services, OrderStates.editing_additional_services),
    F.data.startswith("add_service_")
)
async def handle_add_service(callback: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор/отмену дополнительной услуги."""
    service_key = callback.data.split("_")[-1]

    user_data = await state.get_data()

    # Используем копию, чтобы избежать RuntimeError
    selected_services = user_data.get("selected_services", set()).copy()

    if service_key in selected_services:
        selected_services.remove(service_key)
    else:
        selected_services.add(service_key)

    await state.update_data(selected_services=selected_services)

    # Более надежный способ подсчета стоимости
    preliminary_cost = user_data.get("preliminary_cost", 0)
    additional_cost = sum(ADDITIONAL_SERVICE_PRICES.get(s, 0) for s in selected_services)
    total_cost = preliminary_cost + additional_cost

    # Сохраняем итоговую стоимость в состояние
    await state.update_data(total_cost=total_cost)

    with suppress(TelegramBadRequest):
        await callback.message.edit_text(
            f"Итоговая стоимость уборки: <b>{total_cost} ₽</b>.\n\n"
            f"Выберите дополнительные услуги или нажмите 'Готово'.",
            reply_markup=get_additional_services_keyboard(selected_services)
        )
    await callback.answer()


@router.callback_query(
    StateFilter(OrderStates.choosing_additional_services, OrderStates.editing_additional_services),
    F.data == "done_services"
)
async def done_additional_services(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bots: dict,
                                   config: Settings):
    """Завершает выбор доп. услуг: либо сохраняет изменения, либо переходит к вводу адреса."""
    user_data = await state.get_data()
    order_id = user_data.get("order_id_to_edit")

    # Устанавливаем итоговую стоимость, если она еще не посчитана
    if "total_cost" not in user_data:
        await state.update_data(total_cost=user_data.get("preliminary_cost"))
        user_data = await state.get_data()  # Перезагружаем данные

    # Если мы в режиме редактирования
    if order_id:
        new_services = user_data.get("selected_services", set())
        new_price = user_data.get("total_cost")

        updated_order = await update_order_services_and_price(session, order_id, new_services, new_price)

        if updated_order:
            await callback.message.edit_text(
                f"Отлично! Дополнительные услуги для заказа №{order_id} были обновлены.\n"
                f"Новая стоимость: <b>{new_price} ₽</b>"
            )
            # Уведомление админу
            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ <b>В заказе №{order_id} изменены доп. услуги.</b>\n"
                f"Новая стоимость: {new_price} ₽"
            )
            await state.clear()
            await callback.message.answer("Вы вернулись в главное меню.", reply_markup=get_main_menu_keyboard())

        else:
            await callback.answer("Произошла ошибка при обновлении заказа.", show_alert=True)
            await state.clear()

    # Если это новый заказ, продолжаем стандартный сценарий
    else:
        await callback.message.delete()
        await callback.message.answer(
            "Отлично! Теперь введите ваш адрес или отправьте геолокацию с помощью кнопки ниже.",
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

    # Если мы в режиме редактирования
    if order_id:
        new_address = user_data.get("address_text")
        new_lat = user_data.get("address_lat")
        new_lon = user_data.get("address_lon")

        updated_order = await update_order_address(session, order_id, new_address, new_lat, new_lon)

        if updated_order:
            await message.answer(
                f"Отлично! Адрес для заказа №{order_id} был успешно изменен.\n\n"
                f"📍 <b>Новый адрес:</b> {new_address}",
                reply_markup=get_main_menu_keyboard()
            )
            # Уведомление админу
            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ <b>В заказе №{order_id} изменен адрес.</b>\n"
                f"Новый адрес: {new_address}"
            )
        else:
            await message.answer("Произошла ошибка при обновлении заказа.", reply_markup=get_main_menu_keyboard())

        await state.clear()

    # Если это новый заказ, продолжаем стандартный сценарий
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

    # Проверяем, редактируем ли мы заказ
    if order_id:
        new_date = user_data.get("selected_date")
        new_time = user_data.get("selected_time")

        updated_order = await update_order_datetime(session, order_id, new_date, new_time)

        if updated_order:
            # Форматируем дату для красивого вывода
            try:
                selected_date = datetime.datetime.strptime(new_date, "%Y-%m-%d")
                formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
            except (ValueError, KeyError, TypeError):
                formatted_date = new_date

            await message.answer(
                f"Отлично! Дата и время для заказа №{order_id} были успешно изменены.\n\n"
                f"📅 <b>Новая дата:</b> {formatted_date}\n"
                f"🕒 <b>Новое время:</b> {new_time}",
                reply_markup=get_main_menu_keyboard()
            )
            # Уведомление админу (тоже с красивой датой)
            await bots["admin"].send_message(
                config.admin_id,
                f"❗️ <b>В заказе №{order_id} изменена дата/время.</b>\n"
                f"Новая дата: {formatted_date}\n"
                f"Новое время: {new_time}"
            )
        else:
            await message.answer(
                "Произошла ошибка при обновлении заказа. Пожалуйста, попробуйте снова.",
                reply_markup=get_main_menu_keyboard()
            )

        await state.clear()

    else:
        # Стандартный флоу создания заказа
        await message.answer(
            "Время выбрано. По желанию, вы можете загрузить фото вашей квартиры, "
            "чтобы мы лучше оценили сложность. Или просто нажмите 'Пропустить'.",
            reply_markup=get_photo_keyboard()
        )
        await state.set_state(OrderStates.waiting_for_photo)

# --- БЛОК: ОБРАБОТЧИКИ ДЛЯ ШАГА С ФОТО ---

@router.message(OrderStates.waiting_for_photo, F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    """Обрабатывает загруженное фото и переходит к вводу имени."""
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await message.answer(
        "Спасибо, фото получил. Теперь, пожалуйста, введите ваше имя для заказа.",
        reply_markup=get_order_name_keyboard()
    )
    await state.set_state(OrderStates.entering_order_name)

@router.message(OrderStates.waiting_for_photo, F.text == "➡️ Пропустить")
async def skip_photo(message: types.Message, state: FSMContext):
    """Обрабатывает пропуск шага с фото и переходит к вводу имени."""
    await message.answer(
        "Хорошо, пропустили этот шаг. Теперь, пожалуйста, введите ваше имя для заказа.",
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


@router.message(OrderStates.entering_order_name, F.text)
async def handle_order_name(message: types.Message, state: FSMContext):
    """Обрабатывает введенное имя и запрашивает телефон."""
    await state.update_data(order_name=message.text)
    await message.answer(
        f"Отлично, {message.text}. Теперь, пожалуйста, отправьте ваш номер телефона с помощью кнопки или введите его вручную.",
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

@router.message(OrderStates.confirming_order, F.text == "⬅️ Назад к вводу телефона")
async def back_to_phone_step(message: types.Message, state: FSMContext):
    """Возвращает к шагу ввода телефона."""
    await message.answer(
        "Вы вернулись к вводу номера телефона. Отправьте номер с помощью кнопки или введите вручную.",
        reply_markup=get_order_phone_keyboard()
    )
    await state.set_state(OrderStates.entering_order_phone)

@router.message(OrderStates.entering_order_phone, (F.contact | F.text))
async def handle_order_phone(message: types.Message, state: FSMContext):
    """Обрабатывает телефон и показывает финальную сводку для подтверждения."""
    phone_number = message.contact.phone_number if message.contact else message.text
    await state.update_data(order_phone=phone_number)

    user_data = await state.get_data()

    # Форматируем дату
    date_str = user_data.get('selected_date')
    try:
        selected_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        formatted_date = f"{selected_date.day} {RUSSIAN_MONTHS_GENITIVE[selected_date.month]} {selected_date.year}"
    except (ValueError, KeyError, TypeError):
        formatted_date = date_str

    # Собираем информацию о доп. услугах
    selected_services_keys = user_data.get("selected_services", set())
    selected_services_text = "\n".join(
        [f"    - {ADDITIONAL_SERVICES[key]}" for key in selected_services_keys]
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


@router.message(OrderStates.confirming_order, F.text == "✅ Все верно, подтвердить")
async def handle_confirmation(message: types.Message, state: FSMContext): # <--- УБРАН session
    """Переходит к выбору способа оплаты."""
    await message.answer(
        "Отлично! Теперь выберите способ оплаты:",
        reply_markup=get_payment_keyboard()
    )
    await state.set_state(OrderStates.choosing_payment_method)

@router.message(OrderStates.confirming_order, F.text == "⬅️ Отменить и вернуться в меню")
async def handle_cancel_order(message: types.Message, state: FSMContext): # <--- УБРАН session
    """Отменяет заказ и возвращает в главное меню."""
    await state.clear()
    await message.answer(
        "Заказ отменен. Вы вернулись в главное меню.",
        reply_markup=get_main_menu_keyboard()
    )

@router.message(OrderStates.choosing_payment_method, F.text == "💵 Наличными исполнителю")
async def handle_payment_cash(message: types.Message, state: FSMContext, session: AsyncSession, bots: dict, config: Settings): # <--- ИСПОЛЬЗУЕМ bots
    """Обрабатывает оплату наличными, сохраняет заказ и уведомляет админа."""
    user_data = await state.get_data()
    await create_order(session, user_data, client_tg_id=message.from_user.id)
    await message.answer(
        "Спасибо! Ваш заказ принят в работу. Мы скоро подберем для вас исполнителя.",
        reply_markup=get_main_menu_keyboard()
    )
    summary_text = (
        f"✅ <b>Новый заказ!</b>\n\n"
        f"<b>Клиент:</b> @{message.from_user.username or message.from_user.full_name} ({message.from_user.id})\n"
        f"<b>Имя в заказе:</b> {user_data.get('order_name')}\n"
        f"<b>Телефон:</b> {user_data.get('order_phone')}\n\n"
        f"<b>Адрес:</b> {user_data.get('address_text', 'Не указан')}\n"
        f"<b>Дата и время:</b> {user_data.get('selected_date')} {user_data.get('selected_time')}\n\n"
        f"💰 <b>ИТОГОВАЯ СТОИМОСТЬ: {user_data.get('total_cost')} ₽</b>\n"
        f"<b>Тип оплаты:</b> Наличные"
    )
    await bots["admin"].send_message(chat_id=config.admin_id, text=summary_text)
    await state.clear()

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