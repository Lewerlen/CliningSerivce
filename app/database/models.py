from sqlalchemy import (Column, Integer, String, BigInteger,
                        Float, DateTime, Enum, ForeignKey)
from sqlalchemy.orm import declarative_base, relationship
import datetime
import enum

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
    created_at = Column(DateTime, default=datetime.datetime.now) #

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
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class Order(Base):
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_tg_id = Column(BigInteger, nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.new, nullable=False)

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
    photo_file_id = Column(String)
    total_price = Column(Float)

    created_at = Column(DateTime, default=datetime.datetime.now)

    # Связь с таблицей order_items
    items = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    __tablename__ = 'order_items'

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    service_key = Column(String, nullable=False)  # Например, 'win' или 'sofa'

    # Связь с таблицей orders
    order = relationship("Order", back_populates="items")
