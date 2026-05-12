"""Repository layer reading users.email."""
from sqlalchemy.orm import Session
from .models import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def find_by_id(self, user_id: int):
        return self.db.query(User).filter(User.id == user_id).first()

    def find_all(self):
        return self.db.query(User.email, User.name).all()

    def find_by_email(self, email: str):
        return self.db.query(User).filter(User.email == email).first()
