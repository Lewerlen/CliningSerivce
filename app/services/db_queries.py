import datetime
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.database.models import (User, UserRole, Order, OrderItem, OrderStatus, Ticket, TicketMessage, MessageAuthor,
                                 TicketStatus, UserStatus, ExecutorSchedule, DeclinedOrder, OrderOffer)
import random
import string
from app.keyboards.executor_kb import WEEKDAYS

async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    """Возвращает пользователя по его telegram_id или None, если пользователь не найден."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()

async def create_user(session: AsyncSession, telegram_id: int, name: str, phone: str | None = None, role: UserRole = UserRole.client) -> User:
    """Создает и возвращает нового пользователя."""
    new_user = User(
        telegram_id=telegram_id,
        name=name,
        phone=phone,
        role=role
    )
    session.add(new_user)
    await session.commit()
    return new_user

async def register_executor(session: AsyncSession, telegram_id: int, name: str, phone: str, referred_by: int | None = None) -> User:
    """Регистрирует пользователя как исполнителя, обновляет его роль и присваивает реферальный код."""
    user = await get_user(session, telegram_id)
    is_new_referral = False

    if user:
        # Если пользователь уже есть (например, был клиентом), обновляем его данные
        user.role = UserRole.executor
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

async def create_order(session: AsyncSession, data: dict, client_tg_id: int):
    """Создает заказ и связанные с ним доп. услуги в базе данных."""

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
        total_price=data.get("total_cost")
    )
    session.add(new_order)
    await session.flush()  # Получаем id заказа для связи

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
    """Обновляет статус заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.status = status
        await session.commit()
        return order
    return None

async def get_order_by_id(session: AsyncSession, order_id: int):
    """Возвращает заказ по его ID вместе с выбранными доп. услугами."""
    result = await session.execute(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()

async def update_order_datetime(session: AsyncSession, order_id: int, new_date: str, new_time: str):
    """Обновляет дату и время заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.selected_date = new_date
        order.selected_time = new_time
        await session.commit()
        return order
    return None

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
    """Назначает исполнителя на заказ, обновляет статус и сумму выплаты."""
    order = await session.get(Order, order_id)
    if order and order.status == OrderStatus.new:
        order.executor_tg_id = executor_tg_id
        order.status = OrderStatus.accepted
        order.executor_payment = payment_amount
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

async def update_order_services_and_price(session: AsyncSession, order_id: int, new_services: set,
                                          new_total_price: float):
    """Обновляет доп. услуги и итоговую стоимость заказа."""
    order = await get_order_by_id(session, order_id)
    if not order:
        return None

    # Удаляем старые услуги, связанные с этим заказом
    for item in order.items:
        await session.delete(item)

    # Добавляем новые услуги
    for service_key in new_services:
        order_item = OrderItem(order_id=order.id, service_key=service_key)
        session.add(order_item)

    # Обновляем цену
    order.total_price = new_total_price

    await session.commit()
    return order

async def update_order_address(session: AsyncSession, order_id: int, new_address: str, new_lat: float | None, new_lon: float | None):
    """Обновляет адрес заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.address_text = new_address
        order.address_lat = new_lat
        order.address_lon = new_lon
        await session.commit()
        return order
    return None

async def update_order_rooms_and_price(session: AsyncSession, order_id: int, new_room_count: str, new_bathroom_count: str, new_total_price: float):
    """Обновляет количество комнат, санузлов и итоговую стоимость заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.room_count = new_room_count
        order.bathroom_count = new_bathroom_count
        order.total_price = new_total_price
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
            Order.status == OrderStatus.completed
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
    """Сохраняет оценку и текст отзыва для конкретного заказа."""
    order = await session.get(Order, order_id)
    if order:
        order.rating = rating
        order.review_text = review_text
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

async def unassign_executor_from_order(session: AsyncSession, order_id: int) -> Order | None:
    """Снимает исполнителя с заказа и возвращает заказ в статус 'new'."""
    order = await session.get(Order, order_id)
    if order:
        order.executor_tg_id = None
        order.status = OrderStatus.new
        order.reminder_24h_sent = False # Сбрасываем флаги напоминаний
        order.reminder_2h_sent = False
        await session.commit()
        return order
    return None


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
        Order.rating.isnot(None)
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
        )
    )
    result = await session.execute(stmt)
    counts = result.mappings().one()
    return dict(counts)

async def get_order_details_for_admin(session: AsyncSession, order_id: int) -> Order | None:
    """
    Возвращает детали заказа по его ID, подгружая связанные данные
    о клиенте и исполнителе для панели администратора.
    """
    stmt = (
        select(Order)
        .options(
            selectinload(Order.items),  # Загружаем доп. услуги
            selectinload(Order.executor).load_only(User.name, User.telegram_id, User.phone), # Загружаем только нужные поля Исполнителя
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