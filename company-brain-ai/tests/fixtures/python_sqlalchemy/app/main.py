"""FastAPI + SQLAlchemy fixture for ADR-0042 acceptance tests."""
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from .database import get_db
from .repository import UserRepository

app = FastAPI()


@app.get("/api/v1/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    repo = UserRepository(db)
    return repo.find_by_id(user_id)


@app.get("/api/v1/users")
def list_users(db: Session = Depends(get_db)):
    repo = UserRepository(db)
    return repo.find_all()
