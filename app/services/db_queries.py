import datetime
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.database.models import (User, UserRole, Order, OrderItem, OrderStatus, Ticket, TicketMessage, MessageAuthor,
                                 TicketStatus, UserStatus, ExecutorSchedule, DeclinedOrder, OrderOffer, OrderLog,
                                 SystemSettings)
import random
import string
from app.common.texts import STATUS_MAPPING
from app.keyboards.executor_kb import WEEKDAYS

async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Возвращает пользователя по его telegram_id или None, если пользователь не найден."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()

async def create_user(session: AsyncSession, telegram_id: int, name: str, username: str | None, phone: str | None = None, role: UserRole = UserRole.client) -> User:
    """Создает и возвращает нового пользователя."""
    new_user = User(
        telegram_id=telegram_id,
        name=name,
        username=username,
        phone=phone,
        role=role
    )
    session.add(new_user)
    await session.commit()
    return new_user

async def get_users_by_role(session: AsyncSession, role: UserRole) -> list[User]:
    """Возвращает список пользователей по их роли."""
    result = await session.execute(
        select(User).where(User.role == role)
    )
    return result.scalars().all()

async def register_executor(session: AsyncSession, telegram_id: int, name: str, username: str | None, phone: str, referred_by: int | None = None) -> User:
    """Регистрирует пользователя как исполнителя, обновляет его роль и присваивает реферальный код."""
    user = await get_user(session, telegram_id)
    is_new_referral = False

    if user:
        # Если пользователь уже есть (например, был клиентом), обновляем его данные
        user.role = UserRole.executor
        user.name = name
        user.username = username
        user.phone = phone
        user.status = UserStatus.active
        if not user.referral_code: # Генерируем код, только если его еще нет
            user.referral_code = generate_referral_code()
        if referred_by and not user.referred_by: # Сохраняем пригласившего, если его еще нет
            user.referred_by = referred_by
            is_new_referral = True
    else:
        # Если пользователя нет, создаем нового
        user = User(
            telegram_id=telegram_id,
            name=name,
            username=username,
            phone=phone,
            role=UserRole.executor,
            status=UserStatus.active,
            referral_code=generate_referral_code(),
            referred_by=referred_by
        )
        session.add(user)
        if referred_by:
            is_new_referral = True

    # Инкрементируем счетчик, если это новая успешная регистрация по ссылке
    if is_new_referral:
        referrer = await get_user(session, referred_by)
        if referrer:
            referrer.referrals_count += 1

    await session.commit()
    return user

async def create_order(session: AsyncSession, data: dict, client_tg_id: int, is_test: bool = False):
    """Создает заказ, связанные с ним доп. услуги и первую запись в логе."""

    # Создаем основной заказ
    new_order = Order(
        client_tg_id=client_tg_id,
        cleaning_type=data.get("cleaning_type"),
        room_count=data.get("room_count"),
        bathroom_count=data.get("bathroom_count"),
        address_text=data.get("address_text"),
        address_lat=data.get("address_lat"),
        address_lon=data.get("address_lon"),
        selected_date=data.get("selected_date"),
        selected_time=data.get("selected_time"),
        order_name=data.get("order_name"),
        order_phone=data.get("order_phone"),
        photo_file_ids=data.get("photo_ids"),
        total_price=data.get("total_cost"),
        is_test=is_test
    )
    session.add(new_order)
    await session.flush()  # Получаем id заказа для связи

    # Добавляем первую запись в лог
    log_message = "✅ Заказ создан клиентом"
    if is_test:
        log_message += " (ТЕСТОВЫЙ РЕЖИM)"
    session.add(OrderLog(order_id=new_order.id, message=log_message))

    # Создаем записи для доп. услуг
    selected_services = data.get("selected_services", {})
    for service_key, quantity in selected_services.items():
        order_item = OrderItem(order_id=new_order.id, service_key=service_key, quantity=quantity)
        session.add(order_item)

    await session.commit()
    return new_order

async def get_user_orders(session: AsyncSession, client_tg_id: int):
    """Возвращает список заказов пользователя."""
    result = await session.execute(
        select(Order).where(Order.client_tg_id == client_tg_id).order_by(Order.created_at.desc())
    )
    return result.scalars().all()


async def update_order_status(session: AsyncSession, order_id: int, status: OrderStatus):
    """Обновляет статус заказа и добавляет запись в лог."""
    order = await session.get(Order, order_id)
    if order:
        order.status = status
        session.add(OrderLog(order_id=order.id, message=f"Статус изменен на '{STATUS_MAPPING.get(status, status.value)}'"))
        await session.commit()
        return order
    return None

async def get_order_by_id(session: AsyncSession, order_id: int):
    """Возвращает заказ по его ID вместе с выбранными доп. услугами."""
    result = await session.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()

async def get_orders_by_status(session: AsyncSession, status: OrderStatus, executor_tg_id: int = None) -> list[Order]:
    """
    Возвращает список заказов с определенным статусом.
    Если передан executor_tg_id, исключает заказы, от которых он отказался.
    """
    stmt = select(Order).where(Order.status == status)

    if executor_tg_id:
        # Находим ID заказов, от которых исполнитель отказался
        declined_stmt = select(DeclinedOrder.order_id).where(DeclinedOrder.executor_tg_id == executor_tg_id)
        declined_result = await session.execute(declined_stmt)
        declined_order_ids = declined_result.scalars().all()

        if declined_order_ids:
            # Исключаем эти заказы из основного запроса
            stmt = stmt.where(Order.id.notin_(declined_order_ids))

    stmt = stmt.order_by(Order.created_at.asc())
    result = await session.execute(stmt)
    return result.scalars().all()

async def assign_executor_to_order(session: AsyncSession, order_id: int, executor_tg_id: int, payment_amount: float) -> Order | None:
    """Назначает исполнителя на заказ, обновляет статус, сумму выплаты и добавляет лог."""
    order = await session.get(Order, order_id)
    if order and order.status == OrderStatus.new:
        order.executor_tg_id = executor_tg_id
        order.status = OrderStatus.accepted
        order.executor_payment = payment_amount

        session.add(OrderLog(order_id=order.id, message="✅ Исполнитель назначен"))

        await session.commit()
        return order
    return None


async def add_photo_to_order(session: AsyncSession, order_id: int, photo_file_id: str) -> Order | None:
    """Добавляет file_id фотографии 'после' к заказу."""
    order = await session.get(Order, order_id)
    if order:
        # Если списка еще нет, создаем его
        if order.photos_after_ids is None:
            order.photos_after_ids = []

        # Добавляем новый file_id в список. SQLAlchemy отследит изменение.
        order.photos_after_ids.append(photo_file_id)

        # Принудительно помечаем поле как измененное для SQLAlchemy
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(order, "photos_after_ids")

        await session.commit()
        return order
    return None

async def get_executor_active_orders(session: AsyncSession, executor_tg_id: int) -> list[Order]:
    """Возвращает список активных заказов ('accepted', 'on_the_way', 'in_progress') для конкретного исполнителя."""
    result = await session.execute(
        select(Order)
        .where(
            Order.executor_tg_id == executor_tg_id,
            Order.status.in_([OrderStatus.accepted, OrderStatus.on_the_way, OrderStatus.in_progress])
        )
        .order_by(Order.created_at.asc())
    )
    return result.scalars().all()

async def update_order_services_and_price(session: AsyncSession, order_id: int, new_services: dict,
                                          new_total_price: float, admin_id: int, admin_username: str) -> Order | None:
    """Обновляет доп. услуги и итоговую стоимость заказа."""
    order = await get_order_by_id(session, order_id)
    if not order:
        return None

    # Удаляем старые услуги
    for item in order.items:
        await session.delete(item)
    await session.flush()  # Применяем удаление

    # Добавляем новые услуги
    for service_key, quantity in new_services.items():
        order_item = OrderItem(order_id=order.id, service_key=service_key, quantity=quantity)
        session.add(order_item)

    # Обновляем цену
    order.total_price = new_total_price

    # Добавляем лог
    log_message = f"📝 Администратор @{admin_username} изменил доп. услуги. Новая цена: {new_total_price} ₽"
    session.add(OrderLog(order_id=order_id, message=log_message, admin_id=admin_id))

    await session.commit()
    return order


async def update_order_datetime(session: AsyncSession, order_id: int, new_date: str, new_time: str, admin_id: int, admin_username: str) -> Order | None:
    """Обновляет дату и время заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.selected_date = new_date
        order.selected_time = new_time
        log_message = f"📅 Администратор @{admin_username} изменил дату на {new_date} и время на {new_time}"
        session.add(OrderLog(order_id=order_id, message=log_message, admin_id=admin_id))
        await session.commit()
        return order
    return None


async def update_order_address(session: AsyncSession, order_id: int, new_address: str, new_lat: float | None, new_lon: float | None, admin_id: int, admin_username: str) -> Order | None:
    """Обновляет адрес заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.address_text = new_address
        order.address_lat = new_lat
        order.address_lon = new_lon
        log_message = f"📍 Администратор @{admin_username} изменил адрес на: {new_address}"
        session.add(OrderLog(order_id=order_id, message=log_message, admin_id=admin_id))
        await session.commit()
        return order
    return None

async def update_order_rooms_and_price(session: AsyncSession, order_id: int, new_room_count: str, new_bathroom_count: str, new_total_price: float, admin_id: int, admin_username: str):
    """Обновляет количество комнат, санузлов и итоговую стоимость заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.room_count = new_room_count
        order.bathroom_count = new_bathroom_count
        order.total_price = new_total_price
        log_message = f"🏠 Администратор @{admin_username} изменил кол-во комнат на {new_room_count} и санузлов на {new_bathroom_count}. Новая цена: {new_total_price} ₽"
        session.add(OrderLog(order_id=order_id, message=log_message, admin_id=admin_id))
        await session.commit()
        return order
    return None

async def create_ticket(session: AsyncSession, user_tg_id: int, message_text: str, photo_id: str | None = None) -> Ticket | None:
    """Создает новый тикет и первое сообщение к нему."""
    # Создаем сам тикет
    new_ticket = Ticket(user_tg_id=user_tg_id)
    session.add(new_ticket)
    await session.flush() # Это нужно, чтобы получить ID нового тикета для связи

    # Создаем первое сообщение от клиента
    first_message = TicketMessage(
        ticket_id=new_ticket.id,
        author=MessageAuthor.client,
        text=message_text,
        photo_file_id=photo_id
    )
    session.add(first_message)

    await session.commit()
    return new_ticket

async def get_user_tickets(session: AsyncSession, user_tg_id: int) -> list[Ticket]:
    """Возвращает список всех тикетов пользователя, отсортированных по дате обновления."""
    result = await session.execute(
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .where(Ticket.user_tg_id == user_tg_id)
        .order_by(Ticket.updated_at.desc())
    )
    return result.scalars().all()

async def get_ticket_by_id(session: AsyncSession, ticket_id: int) -> Ticket | None:
    """Возвращает один тикет со всеми сообщениями по его ID."""
    result = await session.execute(
        select(Ticket)
        .options(selectinload(Ticket.messages), selectinload(Ticket.user)) # Добавили загрузку пользователя
        .where(Ticket.id == ticket_id)
    )
    return result.scalar_one_or_none()


async def get_tickets_by_status(session: AsyncSession, status: TicketStatus) -> list[Ticket]:
    """Возвращает список тикетов с определенным статусом."""
    result = await session.execute(
        select(Ticket)
        .options(selectinload(Ticket.user), selectinload(Ticket.messages)) # Загружаем и пользователя, и сообщения
        .where(Ticket.status == status)
        .order_by(Ticket.created_at.asc())
    )
    return result.scalars().all()


async def add_message_to_ticket(session: AsyncSession, ticket_id: int, author: MessageAuthor,
                                text: str, photo_id: str | None = None) -> TicketMessage | None:
    """Добавляет новое сообщение в тикет и обновляет дату тикета."""
    ticket = await session.get(Ticket, ticket_id)
    if not ticket:
        return None

    # Создаем новое сообщение
    new_message = TicketMessage(
        ticket_id=ticket_id,
        author=author,
        text=text,
        photo_file_id=photo_id
    )
    session.add(new_message)

    # Обновляем статус тикета и время последнего ответа
    ticket.updated_at = datetime.datetime.now()
    if author == MessageAuthor.admin:
        ticket.status = TicketStatus.answered
    elif author == MessageAuthor.client:
        # Если отвечает клиент, возвращаем тикет в работу
        ticket.status = TicketStatus.in_progress


    await session.commit()
    return new_message


async def update_ticket_status(session: AsyncSession, ticket_id: int, status: TicketStatus, admin_tg_id: int | None = None) -> Ticket | None:
    """Обновляет статус тикета и опционально назначает администратора."""
    ticket = await session.get(Ticket, ticket_id)
    if ticket:
        ticket.status = status
        if admin_tg_id:
            ticket.admin_tg_id = admin_tg_id
        await session.commit()
    return ticket


async def get_matching_executors(session: AsyncSession, order_date_str: str, order_time_slot: str) -> list[User]:
    """
    Возвращает список активных исполнителей, отсортированный по приоритету, затем по рейтингу и количеству отзывов,
    чей график соответствует заказу, либо всех, если у них нет графика.
    """
    try:
        order_date = datetime.datetime.strptime(order_date_str, "%Y-%m-%d")
        day_of_week_code = list(WEEKDAYS.keys())[order_date.weekday()]
    except (ValueError, IndexError):
        return []

    schedule_day_column = getattr(ExecutorSchedule, day_of_week_code)

    # 1. Находим исполнителей с подходящим графиком и СОРТИРУЕМ их по новым правилам
    stmt_with_schedule = (
        select(User)
        .join(ExecutorSchedule, User.telegram_id == ExecutorSchedule.executor_tg_id)
        .where(
            User.role == UserRole.executor,
            User.status == UserStatus.active,
            schedule_day_column.any(order_time_slot)
        )
        .order_by(User.priority.desc(), User.average_rating.desc(), User.review_count.desc()) # <-- ИЗМЕНЕНИЕ ЗДЕСЬ
    )
    result_with_schedule = await session.execute(stmt_with_schedule)
    executors_with_schedule = list(result_with_schedule.scalars().all())

    # 2. Находим исполнителей без графика и тоже СОРТИРУЕМ их
    stmt_without_schedule = (
        select(User)
        .outerjoin(ExecutorSchedule, User.telegram_id == ExecutorSchedule.executor_tg_id)
        .where(
            User.role == UserRole.executor,
            User.status == UserStatus.active,
            ExecutorSchedule.id.is_(None)
        )
        .order_by(User.priority.desc(), User.average_rating.desc(), User.review_count.desc()) # <-- И ИЗМЕНЕНИЕ ЗДЕСЬ
    )
    result_without_schedule = await session.execute(stmt_without_schedule)
    executors_without_schedule = list(result_without_schedule.scalars().all())

    # 3. Объединяем два отсортированных списка.
    # Сначала идут исполнители с подходящим графиком, потом все остальные.
    # Внутри каждой группы сортировка по приоритету/рейтингу.
    return executors_with_schedule + executors_without_schedule


async def get_executor_schedule(session: AsyncSession, executor_tg_id: int) -> ExecutorSchedule | None:
    """Возвращает график работы исполнителя."""
    result = await session.execute(
        select(ExecutorSchedule).where(ExecutorSchedule.executor_tg_id == executor_tg_id)
    )
    return result.scalar_one_or_none()


async def update_executor_schedule(session: AsyncSession, executor_tg_id: int, schedule_data: dict) -> ExecutorSchedule:
    """Обновляет или создает график работы для исполнителя."""
    schedule = await get_executor_schedule(session, executor_tg_id)
    if not schedule:
        schedule = ExecutorSchedule(executor_tg_id=executor_tg_id)
        session.add(schedule)

    for day, slots in schedule_data.items():
        setattr(schedule, day, slots)

    await session.commit()
    return schedule

async def get_executor_completed_orders(session: AsyncSession, executor_tg_id: int, limit: int = 10) -> list[Order]:
    """Возвращает список последних завершенных заказов исполнителя."""
    result = await session.execute(
        select(Order)
        .where(
            Order.executor_tg_id == executor_tg_id,
            Order.status == OrderStatus.completed,
            Order.is_test == False
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()

async def get_user_by_referral_code(session: AsyncSession, referral_code: str) -> User | None:
    """Находит пользователя по его реферальному коду."""
    result = await session.execute(
        select(User).where(User.referral_code == referral_code)
    )
    return result.scalar_one_or_none()

async def credit_referral_bonus(session: AsyncSession, referrer_id: int, bonus_amount: int = 500):
    """Начисляет бонус пригласившему."""
    referrer = await get_user(session, referrer_id)
    if referrer:
        referrer.referral_balance += bonus_amount
        await session.commit()

# --- Вспомогательная функция для генерации кода ---
def generate_referral_code(length: int = 8) -> str:
    """Генерирует случайный реферальный код."""
    # Создаем код вида 'ref' + случайные буквы и цифры
    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return f"ref{random_part}"

# --- БЛОК: ФУНКЦИИ ДЛЯ РЕЙТИНГОВ ---

async def save_order_rating(session: AsyncSession, order_id: int, rating: int, review_text: str) -> Order | None:
    """Сохраняет оценку и текст отзыва для конкретного заказа и добавляет запись в лог."""
    order = await session.get(Order, order_id)
    if order:
        order.rating = rating
        order.review_text = review_text
        session.add(OrderLog(order_id=order_id, message=f"⭐ Клиент поставил оценку {rating}/5"))
        await session.commit()
        return order
    return None

async def update_executor_rating(session: AsyncSession, executor_tg_id: int):
    """Пересчитывает и обновляет средний рейтинг и количество отзывов для исполнителя."""
    # Выбираем все заказы с оценкой для данного исполнителя
    stmt = select(Order.rating).where(
        Order.executor_tg_id == executor_tg_id,
        Order.rating.isnot(None)
    )
    result = await session.execute(stmt)
    ratings = result.scalars().all()

    executor = await get_user(session, executor_tg_id)
    if executor:
        if ratings:
            executor.average_rating = round(sum(ratings) / len(ratings), 2)
            executor.review_count = len(ratings)
        else:
            executor.average_rating = 0.0
            executor.review_count = 0
        await session.commit()

async def get_executor_orders_with_reviews(session: AsyncSession, executor_tg_id: int, limit: int = 5) -> list[Order]:
    """Возвращает последние заказы исполнителя, по которым есть отзывы."""
    result = await session.execute(
        select(Order)
        .where(
            Order.executor_tg_id == executor_tg_id,
            Order.rating.isnot(None),
            Order.review_text.isnot(None)
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
# --- КОНЕЦ БЛОКА ---

async def unassign_executor_from_order(session: AsyncSession, order_id: int) -> tuple[Order | None, int | None]:
    """
    Снимает исполнителя с заказа, добавляет его в список отказавшихся, возвращает заказ в статус 'new' и логирует действие.
    Возвращает кортеж из обновленного заказа и ID предыдущего исполнителя.
    """
    order = await get_order_by_id(session, order_id)
    if not order:
        return None, None

    previous_executor_id = order.executor_tg_id

    # Если исполнитель был назначен, добавляем его в "отказники", чтобы не предлагать заказ снова
    if previous_executor_id:
        decline = DeclinedOrder(order_id=order_id, executor_tg_id=previous_executor_id)
        session.add(decline)

    order.executor_tg_id = None
    order.status = OrderStatus.new
    order.executor_payment = None
    order.reminder_24h_sent = False # Сбрасываем флаги напоминаний
    order.reminder_2h_sent = False
    session.add(OrderLog(order_id=order_id, message="🔄 Исполнитель снят с заказа"))
    await session.commit()
    return order, previous_executor_id


async def increment_and_get_declines(session: AsyncSession, telegram_id: int) -> User | None:
    """Увеличивает счетчик последовательных отказов и возвращает пользователя."""
    user = await get_user(session, telegram_id)
    if user:
        user.consecutive_declines += 1
        await session.commit()
        return user
    return None

async def reset_consecutive_declines(session: AsyncSession, telegram_id: int):
    """Сбрасывает счетчик последовательных отказов."""
    user = await get_user(session, telegram_id)
    if user and user.consecutive_declines > 0:
        user.consecutive_declines = 0
        await session.commit()

async def block_user_temporarily(session: AsyncSession, telegram_id: int, hours: int = 12) -> User | None:
    """Блокирует пользователя на определенное количество часов."""
    user = await get_user(session, telegram_id)
    if user:
        user.status = UserStatus.blocked
        user.blocked_until = datetime.datetime.now() + datetime.timedelta(hours=hours)
        user.consecutive_declines = 0
        await session.commit()
        return user
    return None

async def unblock_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Снимает блокировку с пользователя."""
    user = await get_user(session, telegram_id)
    if user and user.status == UserStatus.blocked:
        user.status = UserStatus.active
        user.blocked_until = None
        await session.commit()
        return user
    return None

async def update_user_phone(session: AsyncSession, telegram_id: int, phone: str) -> User | None:
    """Обновляет номер телефона пользователя."""
    user = await get_user(session, telegram_id)
    if user and not user.phone: # Обновляем, только если телефон еще не указан
        user.phone = phone
        await session.commit()
        return user
    return user

async def add_declined_order(session: AsyncSession, order_id: int, executor_tg_id: int):
    """Добавляет запись об отказе исполнителя от заказа."""
    new_decline = DeclinedOrder(order_id=order_id, executor_tg_id=executor_tg_id)
    session.add(new_decline)
    await session.commit()

async def create_order_offer(session: AsyncSession, order_id: int, executor_tg_id: int, expires_at: datetime.datetime) -> OrderOffer:
    """Создает новое предложение заказа для исполнителя."""
    new_offer = OrderOffer(
        order_id=order_id,
        executor_tg_id=executor_tg_id,
        expires_at=expires_at,
        status='active'
    )
    session.add(new_offer)
    await session.commit()
    return new_offer

async def get_active_offer_for_order(session: AsyncSession, order_id: int) -> OrderOffer | None:
    """Возвращает активное предложение для конкретного заказа, если оно есть."""
    result = await session.execute(
        select(OrderOffer).where(
            OrderOffer.order_id == order_id,
            OrderOffer.status == 'active'
        )
    )
    return result.scalar_one_or_none()

async def decline_active_offer(session: AsyncSession, order_id: int, executor_tg_id: int) -> OrderOffer | None:
    """Находит активное предложение и меняет его статус на 'declined'."""
    offer = await get_active_offer_for_order(session, order_id)
    if offer and offer.executor_tg_id == executor_tg_id:
        offer.status = 'declined'
        await session.commit()
        return offer
    return None

async def add_bonus_to_executor(session: AsyncSession, executor_tg_id: int, amount: float):
    """Начисляет бонус на бонусный баланс исполнителя."""
    executor = await get_user(session, executor_tg_id)
    if executor:
        executor.bonus_balance += amount
        # session.commit() здесь не нужен, т.к. он будет вызван в основной функции


async def check_and_award_performance_bonus(session: AsyncSession, executor_tg_id: int) -> int | None:
    """
    Проверяет, соответствует ли исполнитель критериям для получения бонуса, и начисляет его.
    Возвращает сумму бонуса, если он был начислен, иначе None.
    """
    executor = await get_user(session, executor_tg_id)
    if not executor:
        return None

    # --- Критерии для бонуса (можно вынести в конфиг) ---
    bonus_order_count_step = 10  # Давать бонус за каждые 10 заказов
    bonus_min_rating = 4.0
    bonus_amount = 500

    # Проверяем, что у исполнителя достаточно высокий рейтинг
    if executor.average_rating < bonus_min_rating:
        return None

    # Считаем количество выполненных заказов с рейтингом
    stmt = select(func.count(Order.id)).where(
        Order.executor_tg_id == executor_tg_id,
        Order.status == OrderStatus.completed,
        Order.rating.isnot(None),
        Order.is_test == False
    )
    result = await session.execute(stmt)
    rated_orders_count = result.scalar_one()

    # Проверяем, достиг ли исполнитель нового порога для бонуса
    # и что за этот порог бонус еще не был выдан
    if rated_orders_count >= executor.last_bonus_order_count + bonus_order_count_step:
        await add_bonus_to_executor(session, executor_tg_id, bonus_amount)
        executor.last_bonus_order_count += bonus_order_count_step
        # один commit в конце
        await session.commit()
        return bonus_amount

    return None

async def get_order_counts_by_status(session: AsyncSession) -> dict:
    """Возвращает количество заказов для каждого статуса."""
    from sqlalchemy import case

    stmt = (
        select(
            func.count(case((Order.status == OrderStatus.new, Order.id))).label("new"),
            func.count(case((Order.status.in_([OrderStatus.accepted, OrderStatus.on_the_way, OrderStatus.in_progress]), Order.id))).label("in_progress"),
            func.count(case((Order.status == OrderStatus.completed, Order.id))).label("completed"),
            func.count(case((Order.status == OrderStatus.cancelled, Order.id))).label("cancelled"),
        ).where(Order.is_test == False)
    )
    result = await session.execute(stmt)
    counts = result.mappings().one()
    return dict(counts)

async def get_order_details_for_admin(session: AsyncSession, order_id: int) -> Order | None:
    """
    Возвращает детали заказа по его ID, подгружая связанные данные
    о клиенте, исполнителе и логах для панели администратора.
    """
    stmt = (
        select(Order)
        .options(
            selectinload(Order.items),  # Загружаем доп. услуги
            selectinload(Order.executor).load_only(User.name, User.telegram_id, User.phone, User.username),
            # Загружаем и username
            selectinload(Order.logs)
        )
        .where(Order.id == order_id)
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()

    # Дополнительно загружаем клиента, так как прямой связи в модели нет
    if order:
        client_result = await session.execute(select(User).where(User.telegram_id == order.client_tg_id))
        order.client = client_result.scalar_one_or_none()

    return order

async def get_all_executors(session: AsyncSession, supervisor_id: int | None = None) -> list[User]:
    """
    Возвращает список всех исполнителей.
    Если указан supervisor_id, возвращает только исполнителей этого супервайзера.
    """
    stmt = (
        select(User)
        .where(User.role == UserRole.executor)
    )
    # Если передан ID супервайзера, добавляем фильтр
    if supervisor_id:
        stmt = stmt.where(User.supervisor_id == supervisor_id)

    stmt = stmt.order_by(User.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()

async def block_executor_by_admin(session: AsyncSession, executor_tg_id: int) -> User | None:
    """Блокирует исполнителя (устанавливает статус blocked)."""
    user = await get_user(session, executor_tg_id)
    if user and user.role == UserRole.executor:
        user.status = UserStatus.blocked
        # Можно также установить user.blocked_until, если нужна временная блокировка
        await session.commit()
        return user
    return None


async def unblock_executor_by_admin(session: AsyncSession, executor_tg_id: int) -> User | None:
    """Активирует исполнителя (устанавливает статус active)."""
    user = await get_user(session, executor_tg_id)
    if user and user.role == UserRole.executor:
        user.status = UserStatus.active
        user.blocked_until = None
        await session.commit()
        return user
    return None

async def update_executor_payment(session: AsyncSession, order_id: int, new_payment: float, admin_id: int, admin_username: str) -> Order | None:
    """Обновляет сумму выплаты исполнителю и логирует действие."""
    order = await session.get(Order, order_id)
    if order and order.executor_tg_id:
        order.executor_payment = new_payment
        log_message = f"💰 Администратор @{admin_username} изменил выплату на {new_payment} ₽"
        session.add(OrderLog(order_id=order_id, message=log_message, admin_id=admin_id))
        await session.commit()
        return order
    return None

async def get_orders_for_report_for_executor(session: AsyncSession, start_date: datetime.datetime, end_date: datetime.datetime, executor_tg_id: int) -> list[Order]:
    """Возвращает все заказы конкретного исполнителя за указанный период для отчета."""
    result = await session.execute(
        select(Order)
        .options(
            selectinload(Order.client),
            selectinload(Order.executor)
        )
        .where(
            Order.executor_tg_id == executor_tg_id,
            Order.created_at.between(start_date, end_date),
            Order.is_test == False
        )
        .order_by(Order.created_at.desc())
    )
    return result.scalars().all()

async def update_executor_priority(session: AsyncSession, executor_tg_id: int, new_priority: int) -> User | None:
    """Обновляет приоритет исполнителя."""
    user = await get_user(session, executor_tg_id)
    if user and user.role == UserRole.executor:
        user.priority = new_priority
        await session.commit()
        return user
    return None

async def get_executor_statistics(session: AsyncSession, executor_tg_id: int) -> dict:
    """Собирает и возвращает статистику по конкретному исполнителю."""
    stats = {
        "completed_count": 0,
        "cancelled_count": 0,
        "in_progress_count": 0,
        "total_earnings": 0.0,
    }

    # Считаем количество заказов по разным статусам
    stmt_counts = (
        select(
            Order.status,
            func.count(Order.id),
            func.sum(Order.executor_payment)
        )
        .where(Order.executor_tg_id == executor_tg_id, Order.is_test == False)
        .group_by(Order.status)
    )
    result_counts = await session.execute(stmt_counts)
    status_stats = result_counts.all()

    for status, count, total_payment in status_stats:
        if status == OrderStatus.completed:
            stats["completed_count"] = count
            stats["total_earnings"] = total_payment or 0.0
        elif status == OrderStatus.cancelled:
            stats["cancelled_count"] = count
        elif status in {OrderStatus.accepted, OrderStatus.on_the_way, OrderStatus.in_progress}:
            # Суммируем все "активные" статусы в один счетчик
            stats["in_progress_count"] += count

    return stats

async def update_user_role(session: AsyncSession, user_tg_id: int, new_role: UserRole) -> User | None:
    """Обновляет роль пользователя."""
    user = await get_user(session, user_tg_id)
    if user:
        user.role = new_role
        await session.commit()
        return user
    return None


async def assign_supervisor_to_executor(session: AsyncSession, executor_tg_id: int, supervisor_tg_id: int | None) -> User | None:
    """Назначает или снимает супервайзера для исполнителя."""
    executor = await get_user(session, executor_tg_id)
    if executor and executor.role == UserRole.executor:
        executor.supervisor_id = supervisor_tg_id
        await session.commit()
        return executor
    return None


async def get_all_supervisors(session: AsyncSession) -> list[User]:
    """Возвращает список всех пользователей с ролью 'supervisor'."""
    return await get_users_by_role(session, UserRole.supervisor)

async def get_orders_by_status_for_supervisor(session: AsyncSession, supervisor_id: int, statuses: list[OrderStatus]) -> list[Order]:
    """Возвращает список заказов с определенными статусами для супервайзера."""
    # 1. Получаем список ID исполнителей, закрепленных за этим супервайзером
    stmt_executors = select(User.telegram_id).where(User.supervisor_id == supervisor_id, User.role == UserRole.executor)
    result_executors = await session.execute(stmt_executors)
    executor_ids = result_executors.scalars().all()

    if not executor_ids:
        return []

    # 2. Находим заказы с нужными статусами, которые назначены на этих исполнителей
    stmt_orders = select(Order).where(
        Order.status.in_(statuses),
        Order.executor_tg_id.in_(executor_ids)
    ).order_by(Order.created_at.desc())

    result_orders = await session.execute(stmt_orders)
    return result_orders.scalars().all()

async def get_all_admins_and_supervisors(session: AsyncSession) -> list[User]:
    """Возвращает список всех пользователей с ролями 'admin' и 'supervisor'."""
    stmt = select(User).where(User.role.in_([UserRole.admin, UserRole.supervisor])).order_by(User.name)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_orders_for_report(session: AsyncSession, start_date: datetime.datetime, end_date: datetime.datetime) -> list[Order]:
    """Возвращает все заказы за указанный период для формирования отчета."""
    result = await session.execute(
        select(Order)
        .options(
            selectinload(Order.client),
            selectinload(Order.executor)
        )
        .where(Order.created_at.between(start_date, end_date), Order.is_test == False)
        .order_by(Order.created_at.desc())
    )
    return result.scalars().all()

async def get_system_settings(session: AsyncSession) -> SystemSettings | None:
    """Возвращает системные настройки (ожидается, что они хранятся с id=1)."""
    result = await session.execute(select(SystemSettings).where(SystemSettings.id == 1))
    return result.scalar_one_or_none()


async def update_system_settings(session: AsyncSession, settings_data: dict) -> SystemSettings:
    """Обновляет системные настройки или создает их, если они не существуют."""
    settings = await get_system_settings(session)
    if not settings:
        settings = SystemSettings(id=1)
        session.add(settings)

    for key, value in settings_data.items():
        setattr(settings, key, value)

    await session.commit()
    return settings

async def get_general_statistics(session: AsyncSession) -> dict:
    """Собирает и возвращает общую статистику по заказам."""
    stats = {}
    now = datetime.datetime.now()

    # Заказы за сегодня
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stmt_today = select(func.count(Order.id), func.sum(Order.total_price)).where(Order.created_at >= today_start, Order.is_test == False)
    result_today = await session.execute(stmt_today)
    stats['orders_today'], stats['revenue_today'] = result_today.one()

    # Заказы за неделю
    week_start = today_start - datetime.timedelta(days=now.weekday())
    stmt_week = select(func.count(Order.id), func.sum(Order.total_price)).where(Order.created_at >= week_start, Order.is_test == False)
    result_week = await session.execute(stmt_week)
    stats['orders_week'], stats['revenue_week'] = result_week.one()

    # Заказы за месяц
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt_month = select(func.count(Order.id), func.sum(Order.total_price)).where(Order.created_at >= month_start, Order.is_test == False)
    result_month = await session.execute(stmt_month)
    stats['orders_month'], stats['revenue_month'] = result_month.one()

    # Средний чек
    stmt_avg = select(func.avg(Order.total_price)).where(Order.is_test == False)
    result_avg = await session.execute(stmt_avg)
    stats['avg_check'] = result_avg.scalar_one_or_none()

    # Среднее время выполнения заказа
    stmt_avg_completion = select(func.avg(Order.completed_at - Order.in_progress_at)).where(
        Order.status == OrderStatus.completed,
        Order.in_progress_at.isnot(None),
        Order.completed_at.isnot(None)
    )
    result_avg_completion = await session.execute(stmt_avg_completion)
    avg_completion_timedelta = result_avg_completion.scalar_one_or_none()
    if avg_completion_timedelta:
        total_seconds = avg_completion_timedelta.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        stats['avg_completion_time'] = f"{hours} ч {minutes} мин"
    else:
        stats['avg_completion_time'] = "Нет данных"


    return stats

async def get_top_executors(session: AsyncSession, limit: int = 5) -> list[User]:
    """Возвращает список лучших исполнителей по рейтингу и количеству заказов."""
    stmt = (
        select(User)
        .where(User.role == UserRole.executor, User.review_count > 0)
        .order_by(User.average_rating.desc(), User.review_count.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_top_additional_services(session: AsyncSession, limit: int = 5) -> list:
    """Возвращает список самых популярных дополнительных услуг."""
    stmt = (
        select(OrderItem.service_key, func.sum(OrderItem.quantity).label('total_quantity'))
        .group_by(OrderItem.service_key)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.all()