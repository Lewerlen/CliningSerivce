import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.database.models import User, UserRole, Order, OrderItem, OrderStatus, Ticket, TicketMessage, MessageAuthor, TicketStatus

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
        photo_file_id=data.get("photo_file_id"),
        total_price=data.get("total_cost")
    )
    session.add(new_order)
    await session.flush()  # Получаем id заказа для связи

    # Создаем записи для доп. услуг
    selected_services = data.get("selected_services", set())
    for service_key in selected_services:
        order_item = OrderItem(order_id=new_order.id, service_key=service_key)
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