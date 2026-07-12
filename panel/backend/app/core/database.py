from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


def _sqlite_columns(sync_conn, table: str) -> set[str]:
    rows = sync_conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _migrate_sqlite(sync_conn) -> None:
    """Add columns introduced after initial create_all (SQLite has no ALTER IF NOT EXISTS)."""
    if sync_conn.dialect.name != "sqlite":
        return

    workers = _sqlite_columns(sync_conn, "worker_nodes")
    if "worker_config" not in workers:
        sync_conn.execute(text("ALTER TABLE worker_nodes ADD COLUMN worker_config JSON DEFAULT '{}'"))
    if "token_lookup" not in workers:
        sync_conn.execute(text("ALTER TABLE worker_nodes ADD COLUMN token_lookup VARCHAR(64) DEFAULT ''"))
    for col, decl in (
        ("disk_percent", "FLOAT DEFAULT 0"),
        ("mem_used_gb", "FLOAT DEFAULT 0"),
        ("mem_total_gb", "FLOAT DEFAULT 0"),
        ("disk_used_gb", "FLOAT DEFAULT 0"),
        ("disk_total_gb", "FLOAT DEFAULT 0"),
        ("load_avg_1", "FLOAT DEFAULT 0"),
        ("load_avg_5", "FLOAT DEFAULT 0"),
        ("load_avg_15", "FLOAT DEFAULT 0"),
        ("host_os", "VARCHAR(64) DEFAULT ''"),
        ("hostname", "VARCHAR(128) DEFAULT ''"),
    ):
        if col not in workers:
            sync_conn.execute(text(f"ALTER TABLE worker_nodes ADD COLUMN {col} {decl}"))

    scrape = _sqlite_columns(sync_conn, "scrape_settings")
    alters = [
        ("headless", "BOOLEAN DEFAULT 1"),
        ("no_stealth", "BOOLEAN DEFAULT 0"),
        ("browser_path", "VARCHAR(512) DEFAULT ''"),
        ("geoip", "BOOLEAN DEFAULT 0"),
        ("preflight_timeout", "FLOAT DEFAULT 12.0"),
        ("no_preflight", "BOOLEAN DEFAULT 0"),
        ("fresh", "BOOLEAN DEFAULT 0"),
        ("debug", "BOOLEAN DEFAULT 0"),
    ]
    for col, decl in alters:
        if col not in scrape:
            sync_conn.execute(text(f"ALTER TABLE scrape_settings ADD COLUMN {col} {decl}"))


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite)
