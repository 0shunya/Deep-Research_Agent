from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().database_url
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            # SQLite ignores ON DELETE CASCADE unless foreign keys are switched on per connection.
            event.listen(_engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests that switch DATABASE_URL)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def SessionLocal() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """A unit of work: commit on success, roll back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
