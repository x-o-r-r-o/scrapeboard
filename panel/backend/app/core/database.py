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
        ("scrape_settings_id", "INTEGER"),
    ):
        if col not in workers:
            sync_conn.execute(text(f"ALTER TABLE worker_nodes ADD COLUMN {col} {decl}"))

    packages = _sqlite_columns(sync_conn, "packages")
    if "scrape_settings_id" not in packages:
        sync_conn.execute(text("ALTER TABLE packages ADD COLUMN scrape_settings_id INTEGER"))
    for col, decl in (
        ("description", "TEXT DEFAULT ''"),
        ("headings", "JSON DEFAULT '[]'"),
        ("features", "JSON DEFAULT '[]'"),
        ("allowed_engines", "JSON DEFAULT '[\"all\"]'"),
        ("dedicated_worker", "BOOLEAN DEFAULT 0"),
        ("scrape_defaults", "JSON DEFAULT '{}'"),
        ("chunk_size", "INTEGER DEFAULT 500"),
    ):
        if col not in packages:
            sync_conn.execute(text(f"ALTER TABLE packages ADD COLUMN {col} {decl}"))

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
        ("name", "VARCHAR(128) DEFAULT 'Default'"),
        ("slug", "VARCHAR(64) DEFAULT 'default'"),
        ("description", "TEXT DEFAULT ''"),
        ("is_default", "BOOLEAN DEFAULT 0"),
        ("is_active", "BOOLEAN DEFAULT 1"),
        ("captcha_backup_provider", "VARCHAR(32) DEFAULT 'none'"),
        ("captcha_backup_key", "VARCHAR(255) DEFAULT ''"),
        ("captcha_backup_host", "VARCHAR(255) DEFAULT ''"),
        ("created_at", "DATETIME"),
    ]
    for col, decl in alters:
        if col not in scrape:
            sync_conn.execute(text(f"ALTER TABLE scrape_settings ADD COLUMN {col} {decl}"))

    # Ensure row id=1 is marked default when present
    sync_conn.execute(
        text(
            "UPDATE scrape_settings SET name=COALESCE(NULLIF(name,''),'Default'), "
            "slug=COALESCE(NULLIF(slug,''),'default'), is_default=1, is_active=1 WHERE id=1"
        )
    )

    bot_workflows = _sqlite_columns(sync_conn, "bot_workflows")
    if bot_workflows and "sort_order" not in bot_workflows:
        sync_conn.execute(text("ALTER TABLE bot_workflows ADD COLUMN sort_order INTEGER DEFAULT 0"))

    support_tickets = _sqlite_columns(sync_conn, "support_tickets")
    if support_tickets:
        for col, decl in (
            ("updated_at", "DATETIME"),
            ("closed_at", "DATETIME"),
            ("closed_by_id", "INTEGER"),
        ):
            if col not in support_tickets:
                sync_conn.execute(text(f"ALTER TABLE support_tickets ADD COLUMN {col} {decl}"))

    jobs = _sqlite_columns(sync_conn, "jobs")
    if jobs and "name" not in jobs:
        sync_conn.execute(text("ALTER TABLE jobs ADD COLUMN name VARCHAR(128)"))

    job_chunks = _sqlite_columns(sync_conn, "job_chunks")
    if job_chunks:
        for col, decl in (
            ("progress_done", "INTEGER DEFAULT 0"),
            ("progress_rows", "INTEGER DEFAULT 0"),
        ):
            if col not in job_chunks:
                sync_conn.execute(text(f"ALTER TABLE job_chunks ADD COLUMN {col} {decl}"))

    billing = _sqlite_columns(sync_conn, "billing_settings")
    if billing:
        for col, decl in (
            ("usdt_bep20_enabled", "BOOLEAN DEFAULT 0"),
            ("usdt_bep20_wallet", "VARCHAR(128) DEFAULT ''"),
            (
                "usdt_bep20_contract",
                "VARCHAR(128) DEFAULT '0x55d398326f99059fF775485246999027B3197955'",
            ),
            ("usdt_bep20_api_base", "VARCHAR(255) DEFAULT 'https://api.etherscan.io/v2/api'"),
            ("usdt_bep20_api_key", "VARCHAR(255) DEFAULT ''"),
            ("usdt_bep20_rpc_url", "VARCHAR(255) DEFAULT 'https://bsc-dataseed.binance.org/'"),
        ):
            if col not in billing:
                sync_conn.execute(text(f"ALTER TABLE billing_settings ADD COLUMN {col} {decl}"))
        # Migrate legacy BscScan explorer base → Etherscan API V2 (BSC via chainid=56 at runtime)
        cols_after = _sqlite_columns(sync_conn, "billing_settings")
        if "usdt_bep20_api_base" in cols_after:
            sync_conn.execute(
                text(
                    "UPDATE billing_settings SET usdt_bep20_api_base = :new "
                    "WHERE usdt_bep20_api_base IS NULL OR TRIM(usdt_bep20_api_base) = '' "
                    "OR LOWER(TRIM(usdt_bep20_api_base)) IN ("
                    "'https://api.bscscan.com/api', 'http://api.bscscan.com/api', "
                    "'https://api.bscscan.com/api/', 'http://api.bscscan.com/api/'"
                    ")"
                ),
                {"new": "https://api.etherscan.io/v2/api"},
            )


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite)
