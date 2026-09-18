"""SQLAlchemy declarative base plus shared column helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import DeclarativeBase

#: JSON columns that hold python lists / dicts and track in-place mutation.
JsonList = MutableList.as_mutable(JSON)
JsonDict = MutableDict.as_mutable(JSON)


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
