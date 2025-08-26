from sqlalchemy.orm import declarative_base, relationship
import datetime
import enum
from sqlalchemy import Column, Integer, String, BigInteger, \
    Float, DateTime, Enum, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import ARRAY

Base = declarative_base()

class UserRole(enum.Enum):
    client = "client"
    executor = "executor"
    admin = "admin"

class UserStatus(enum.Enum):
    active = "active"
    blocked = "blocked"

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False) # [cite: 283]
    role = Column(Enum(UserRole), default=UserRole.client, nullable=False) # [cite: 283]
    name = Column(String) # [cite: 283]
    phone = Column(String) # [cite: 283]
    rating = Column(Float, default=0.0) # [cite: 283]
    status = Column(Enum(UserStatus), default=UserStatus.active, nullable=False) # [cite: 283]
    created_at = Column(DateTime, default=datetime.datetime.now)

    referral_code = Column(String, unique=True, nullable=True)
    referred_by = Column(BigInteger, nullable=True)
    referral_balance = Column(Float, default=0.0)
    referrals_count = Column(Integer, default=0)

    average_rating = Column(Float, default=0.0)
    review_count = Column(Integer, default=0)
    consecutive_declines = Column(Integer, default=0, nullable=False)
    blocked_until = Column(DateTime, nullable=True)  # Время окончания блокировки

class ServiceType(enum.Enum):
    base = "base"
    additional = "additional"

class Service(Base):
    __tablename__ = 'services'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False) # [cite: 289]
    type = Column(Enum(ServiceType), nullable=False) # [cite: 289]
    price = Column(Float, nullable=False) # [cite: 289]

class OrderStatus(enum.Enum):
    new = "new"
    accepted = "accepted"
    on_the_way = "on_the_way"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"
    pending_confirmation = "pending_confirmation"


class Order(Base):
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_tg_id = Column(BigInteger, nullable=False)
    executor_tg_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=True) # ID исполнителя
    status = Column(Enum(OrderStatus), default=OrderStatus.new, nullable=False)
    executor_payment = Column(Float, nullable=True) # Сумма выплаты исполнителю

    # Детали заказа
    cleaning_type = Column(String)
    room_count = Column(String)
    bathroom_count = Column(String)

    # Адрес
    address_text = Column(String)
    address_lat = Column(Float)
    address_lon = Column(Float)

    # Дата и время
    selected_date = Column(String)
    selected_time = Column(String)

    # Контакты для заказа
    order_name = Column(String)
    order_phone = Column(String)

    # Фото и цена
    photo_file_ids = Column(ARRAY(String), nullable=True)  # Фото "до" от клиента
    photos_after_ids = Column(ARRAY(String), nullable=True)  # Фото "после" от исполнителя
    total_price = Column(Float)

    created_at = Column(DateTime, default=datetime.datetime.now)

    # Поля для отслеживания отправки напоминаний
    reminder_24h_sent = Column(Boolean, default=False, nullable=False)
    reminder_2h_sent = Column(Boolean, default=False, nullable=False)

    # Связь с таблицей order_items
    items = relationship("OrderItem", back_populates="order")
    executor = relationship("User", foreign_keys=[executor_tg_id])

    rating = Column(Integer, nullable=True)
    review_text = Column(String, nullable=True)



class OrderItem(Base):
    __tablename__ = 'order_items'

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    service_key = Column(String, nullable=False)
    quantity = Column(Integer, default=1, nullable=False)

    # Связь с таблицей orders
    order = relationship("Order", back_populates="items")

class TicketStatus(enum.Enum):
    new = "Новый"
    in_progress = "В работе"
    answered = "Ответ получен"
    closed = "Закрыт"

class Ticket(Base):
    __tablename__ = 'tickets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_tg_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)
    admin_tg_id = Column(BigInteger, nullable=True) # ID админа, который взял тикет
    status = Column(Enum(TicketStatus), default=TicketStatus.new, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    # Поля для автозакрытия
    autoclose_reminder_sent = Column(Boolean, default=False, nullable=False)
    was_autoclosed = Column(Boolean, default=False, nullable=False)

    user = relationship("User")
    messages = relationship("TicketMessage", back_populates="ticket", cascade="all, delete-orphan")


class MessageAuthor(enum.Enum):
    client = "client"
    admin = "admin"

class TicketMessage(Base):
    __tablename__ = 'ticket_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id'), nullable=False)
    author = Column(Enum(MessageAuthor), nullable=False)
    text = Column(String, nullable=False)
    photo_file_id = Column(String) # Для прикрепленных фото
    created_at = Column(DateTime, default=datetime.datetime.now)

    ticket = relationship("Ticket", back_populates="messages")


class ExecutorSchedule(Base):
    __tablename__ = 'executor_schedules'

    id = Column(Integer, primary_key=True, autoincrement=True)
    executor_tg_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False, unique=True)

    # Храним доступные временные слоты для каждого дня недели
    monday = Column(ARRAY(String), default=[])
    tuesday = Column(ARRAY(String), default=[])
    wednesday = Column(ARRAY(String), default=[])
    thursday = Column(ARRAY(String), default=[])
    friday = Column(ARRAY(String), default=[])
    saturday = Column(ARRAY(String), default=[])
    sunday = Column(ARRAY(String), default=[])

    executor = relationship("User")

class DeclinedOrder(Base):
    __tablename__ = 'declined_orders'

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    executor_tg_id = Column(BigInteger, ForeignKey('users.telegram_id'), nullable=False)