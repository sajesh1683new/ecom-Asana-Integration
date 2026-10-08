from datetime import datetime, timezone
from typing import Any
from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session
from backend.app.config import DATA

class Base(DeclarativeBase): pass
engine=create_engine(f'sqlite:///{DATA / "vqa.db"}',connect_args={'check_same_thread':False,'timeout':30})
def now() -> str: return datetime.now(timezone.utc).isoformat()
def session() -> Session: return Session(engine)
def record(obj: Any) -> dict[str,Any]:
    return {c.name:getattr(obj,c.name) for c in obj.__table__.columns}
def all_rows(s: Session, model: Any, **filters: Any) -> list[dict[str,Any]]:
    return [record(o) for o in s.scalars(select(model).filter_by(**filters)).all()]
def get(s: Session, model: Any, id: Any) -> Any:
    return s.get(model,id)
def add(s: Session, model: Any, **values: Any) -> Any:
    obj=model(**values);s.add(obj);s.flush();return obj
def migrate() -> None:
    from alembic.config import Config
    from alembic import command
    config=Config('backend/alembic.ini');command.upgrade(config,'head')
