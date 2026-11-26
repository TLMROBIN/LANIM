from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str):
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    return create_engine(database_url, future=True, **kwargs)


def build_sessionmaker(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=build_engine(database_url), expire_on_commit=False, future=True)


def ensure_schema(engine) -> None:
    """Create missing tables and idempotently add new columns for legacy databases."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    if "conversations" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "student_deleted_at" not in columns:
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(
                    text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS student_deleted_at TIMESTAMP NULL")
                )
            else:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN student_deleted_at DATETIME NULL"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_conversations_student_deleted_at ON conversations (student_deleted_at)")
            )


@contextmanager
def managed_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
