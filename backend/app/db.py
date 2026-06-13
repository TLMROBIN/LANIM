from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
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
