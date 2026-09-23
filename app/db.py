"""SQLite engine / session management."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import PROJECT_ROOT, get_settings
from app.models.base import Base

# Import all model modules so that Base.metadata is complete.
from app.models import evidence as _evidence  # noqa: F401
from app.models import events as _events  # noqa: F401
from app.models import lead as _lead  # noqa: F401
from app.models import outreach as _outreach  # noqa: F401

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _resolve_url(url: str) -> str:
    """Make relative sqlite paths relative to the project root, not the cwd."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        if raw and raw != ":memory:" and not raw.startswith("/"):
            return prefix + str(PROJECT_ROOT / raw)
    return url


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionFactory
    if url is not None or _engine is None:
        resolved = _resolve_url(url or get_settings().db_url)
        _engine = create_engine(resolved, future=True)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db(url: str | None = None) -> Engine:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    return engine


def reset_engine() -> None:
    """Drop cached engine/session factory (used by tests)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope; commits on success, rolls back on error."""
    if _SessionFactory is None:
        init_db()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_file_path() -> Path | None:
    url = _resolve_url(get_settings().db_url)
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :])
    return None
