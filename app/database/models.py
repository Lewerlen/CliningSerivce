from sqlalchemy import (Column, Integer, String, BigInteger,
                        Float, DateTime, Enum)
from sqlalchemy.orm import declarative_base
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