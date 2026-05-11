"""SQLAlchemy models for ADR-0042 fixture."""
from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
