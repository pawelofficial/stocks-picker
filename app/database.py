import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "stocks.db"
DB_PATH = os.environ.get("STOCKS_DB", str(_DEFAULT_DB))

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables if they don't exist."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI dependency – yields a session, closes on teardown."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
