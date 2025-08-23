
import datetime
import logging
from contextlib import suppress

from aiogram import F, Router, types, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.handlers.states import OrderStates
from app.keyboards.client_kb import (
    ADDITIONAL_SERVICES,
    create_calendar,
    get_address_confirmation_keyboard,
    get_address_keyboard,
    get_additional_services_keyboard,
    get_bathroom_count_keyboard,
    get_cleaning_type_keyboard,
    get_confirmation_keyboard,
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
    update_order_status,
    OrderStatus, get_order_by_id
)
from app.services.price_calculator import ADDITIONAL_SERVICE_PRICES, calculate_preliminary_cost
from app.services.yandex_maps_api import get_address_from_coords, get_address_from_text

router = Router()

# Список месяцев в родительном падеже для красивого вывода
RUSSIAN_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}



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

    await message.answer(
        f"Отлично! Предварительная стоимость уборки: <b>{cost} ₽</b>.\n\n"
        f"Теперь выберите дополнительные услуги, которые вам нужны, или нажмите 'Готово'.",
        reply_markup=get_additional_services_keyboard()
    )
    await state.set_state(OrderStates.choosing_additional_services)


@router.message(F.text == "💬 Мои заказы")
async def my_orders(message: types.Message, session: AsyncSession):
    """Показывает историю заказов, разделяя на активные и завершенные."""
    orders = await get_user_orders(session, client_tg_id=message.from_user.id)

    if not orders:
        await message.answer("У вас еще нет заказов.")
        return

    # Разделяем заказы на активные и завершенные/отмененные
    active_orders = [o for o in orders if o.status in (OrderStatus.new, OrderStatus.accepted, OrderStatus.in_progress)]
    completed_orders = [o for o in orders if o.status in (OrderStatus.completed, OrderStatus.cancelled)]

    response_text = ""

    # Формируем список активных заказов
    if active_orders:
        response_text += "<b>Активные заказы:</b>\n\n"
        for order in active_orders:
            response_text += (
                f"<b>Заказ №{order.id}</b> от {order.created_at.strftime('%d.%m.%Y')}\n"
                f"Статус: <i>{order.status.value}</i>, Сумма: {order.total_price} ₽\n"
                f"Адрес: {order.address_text}\n\n"
            )

    # Формируем список завершенных заказов
    if completed_orders:
        response_text += "<b>Архив заказов:</b>\n\n"
        for order in completed_orders:
            response_text += (
                f"<b>Заказ №{order.id}</b> от {order.created_at.strftime('%d.%m.%Y')}\n"
                f"Статус: <i>{order.status.value}</i>, Сумма: {order.total_price} ₽\n\n"
            )

    # Создаем клавиатуру с кнопками "Отменить" и "Заказать снова"
    keyboard = InlineKeyboardBuilder()
    for order in active_orders:
        keyboard.button(text=f"❌ Отменить заказ №{order.id}", callback_data=f"cancel_order:{order.id}")
    for order in completed_orders:
        keyboard.button(text=f"🔄 Заказать снова №{order.id}", callback_data=f"repeat_order:{order.id}")

    # Выстраиваем кнопки в один столбец
    keyboard.adjust(1)

    await message.answer(response_text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("cancel_order:"))
async def cancel_order(callback: types.CallbackQuery, session: AsyncSession, bot: Bot, config: Settings):
    """Обрабатывает отмену заказа."""
    order_id = int(callback.data.split(":")[1])

    # Меняем статус в БД
    updated_order = await update_order_status(session, order_id, OrderStatus.cancelled)

    if updated_order:
        # Редактируем исходное сообщение, убирая кнопку
        await callback.message.edit_text(
            f"<b>Заказ №{updated_order.id} от {updated_order.created_at.strftime('%d.%m.%Y')}</b>\n"
            f"Статус: <i>{updated_order.status.value}</i>\n"
            f"Сумма: {updated_order.total_price} ₽\n"
            f"Адрес: {updated_order.address_text}"
        )
        await callback.answer("Заказ отменен.")

        # Отправляем уведомление админу
        await bot.send_message(
            chat_id=config.admin_id,
            text=f"❗️ <b>Клиент @{callback.from_user.username} отменил заказ №{order_id}.</b>"
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
        # ... и так далее, можно скопировать формирование текста из handle_order_phone ...
        f"💰 <b>ИТОГОВАЯ СТОИМОСТЬ: {user_data.get('total_cost')} ₽</b>"
    )

    # Убираем старую клавиатуру "Мои заказы"
    await callback.message.edit_reply_markup(reply_markup=None)

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
    OrderStates.choosing_additional_services,
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
    OrderStates.choosing_additional_services,
    F.data == "done_services"
)
async def done_additional_services(callback: types.CallbackQuery, state: FSMContext):
    """Завершает выбор доп. услуг и переходит к вводу адреса."""
    user_data = await state.get_data()
    # Если total_cost не был установлен (не выбраны доп. услуги),
    # то он равен preliminary_cost
    if "total_cost" not in user_data:
        await state.update_data(total_cost=user_data.get("preliminary_cost"))

    await callback.message.delete() # Удаляем inline-клавиатуру
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
async def handle_address_confirmation(message: types.Message, state: FSMContext):
    """Переходит к выбору даты после подтверждения адреса."""
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

    user_id = callback.from_user.id
    username = callback.from_user.username or "unknown"
    logging.info(
        f"Выбрал дату: {date_str}",
        extra={"username": username, "user_id": user_id}
    )
    try:
        selected_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        day = selected_date.day
        month_name = RUSSIAN_MONTHS_GENITIVE[selected_date.month]
        year = selected_date.year
        formatted_date = f"{day} {month_name} {year}"
    except (ValueError, KeyError):
        formatted_date = date_str

    await callback.message.delete()
    await callback.message.answer(
        f"Вы выбрали дату: {formatted_date}.\n\nТеперь выберите удобный временной интервал:",
        reply_markup=get_time_keyboard()
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
async def handle_time_selection(message: types.Message, state: FSMContext):
    """Обрабатывает выбор времени и переходит к шагу загрузки фото."""
    if message.text == "⬅️ Назад к выбору даты":
        await back_to_date_selection(message, state)
        return

    await state.update_data(selected_time=message.text)
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


@router.message(OrderStates.waiting_for_photo, F.text == "⬅️ Назад к выбору времени")
async def back_to_time_selection(message: types.Message, state: FSMContext):
    """Возвращает к выбору времени."""
    await message.answer(
        "Вы вернулись к выбору времени. Выберите удобный интервал:",
        reply_markup=get_time_keyboard()
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
        f"<b>Клиент:</b> @{message.from_user.username} ({message.from_user.id})\n"
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