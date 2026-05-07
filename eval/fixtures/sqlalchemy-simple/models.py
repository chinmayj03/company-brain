from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, BigInteger
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    name = Column(String(255))
    is_active = Column(Boolean, default=True)
    org_id = Column(BigInteger, ForeignKey("organisations.id"))

class Organisation(Base):
    __tablename__ = "organisations"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True)
