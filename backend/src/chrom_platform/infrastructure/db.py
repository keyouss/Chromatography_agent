"""Shared SQLAlchemy primitives for the platform persistence layer.

The application deliberately accepts MySQL URLs only.  A SQLite URL here would
make local success look like production readiness and would also hide MySQL
locking, JSON and index semantics.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, MetaData, create_engine
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC-naive DATETIME values and return aware UTC values."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=False))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return self._as_utc(value).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


MYSQL_TABLE_KWARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def validate_mysql_url(database_url: str | URL) -> URL:
    """Reject unsupported database backends before any connection is made."""

    url = make_url(database_url) if isinstance(database_url, str) else database_url
    if url.get_backend_name() != "mysql":
        raise ValueError(
            f"Chromatography platform persistence requires a MySQL URL, got {url.get_backend_name()!r}"
        )
    return url


def create_database_engine(database_url: str | URL, **kwargs: Any) -> Engine:
    """Create the production SQLAlchemy engine; there is no in-memory fallback."""

    url = validate_mysql_url(database_url)
    return create_engine(url, pool_pre_ping=True, future=True, **kwargs)


def create_session_factory(database_url: str | URL, **kwargs: Any) -> sessionmaker[Session]:
    return sessionmaker(bind=create_database_engine(database_url, **kwargs), expire_on_commit=False)


def check_database_health(engine: Engine) -> tuple[bool, str | None]:
    """Return a non-throwing health result while preserving the real DB error."""

    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - health endpoints must not leak a traceback
        return False, type(exc).__name__
    return True, None
