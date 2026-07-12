from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    perms: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    jobs: Mapped[list["Job"]] = relationship(back_populates="owner")
    worker_assignments: Mapped[list["UserWorker"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserWorker(Base):
    """Restrict which workers may lease jobs for a given panel/Telegram user.

    Empty assignment list = any worker may pick the user's jobs (default).
    """

    __tablename__ = "user_workers"
    __table_args__ = (UniqueConstraint("user_id", "worker_id", name="uq_user_worker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_nodes.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="worker_assignments")
    worker: Mapped["WorkerNode"] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    ip_address: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecuritySettings(Base):
    __tablename__ = "security_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    recaptcha_mode: Mapped[str] = mapped_column(String(8), default="none")  # none | v2 | v3
    recaptcha_site_key: Mapped[str] = mapped_column(String(255), default="")
    recaptcha_secret_key: Mapped[str] = mapped_column(String(255), default="")
    recaptcha_v3_min_score: Mapped[float] = mapped_column(Float, default=0.5)
    max_login_failures: Mapped[int] = mapped_column(Integer, default=5)
    lockout_minutes: Mapped[int] = mapped_column(Integer, default=15)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CaptchaSettings(Base):
    """Global 2captcha / CaptchaAI solvers (primary + backup). Used for all workers/jobs."""

    __tablename__ = "captcha_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    captcha_provider: Mapped[str] = mapped_column(String(32), default="none")
    captcha_key: Mapped[str] = mapped_column(String(255), default="")
    captcha_host: Mapped[str] = mapped_column(String(255), default="")
    captcha_retries: Mapped[int] = mapped_column(Integer, default=2)
    captcha_backup_provider: Mapped[str] = mapped_column(String(32), default="none")
    captcha_backup_key: Mapped[str] = mapped_column(String(255), default="")
    captcha_backup_host: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Package(Base):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    tier: Mapped[int] = mapped_column(Integer, default=1)
    price_usdt: Mapped[float] = mapped_column(Float, default=0)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    threads: Mapped[int] = mapped_column(Integer, default=2)
    max_upload_mb: Mapped[int] = mapped_column(Integer, default=5)
    allowed_engines: Mapped[list] = mapped_column(JSON, default=lambda: ["all"])
    description: Mapped[str] = mapped_column(Text, default="")
    headings: Mapped[list] = mapped_column(JSON, default=list)  # marketing / bot display titles
    features: Mapped[list] = mapped_column(JSON, default=list)  # bullet feature list
    # When true, admins may optionally pin this subscriber to specific workers
    dedicated_worker: Mapped[bool] = mapped_column(Boolean, default=False)
    # Legacy FK — migrated into scrape_defaults; kept nullable for old DBs
    scrape_settings_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_settings.id"), nullable=True)
    # Package default scrape flags (engine, delays, …). Lease base layer before worker overrides.
    scrape_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    # Max searches per job chunk (ceiling). Create may shrink below this to spread across workers.
    chunk_size: Mapped[int] = mapped_column(Integer, default=500)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    package_id: Mapped[int | None] = mapped_column(ForeignKey("packages.id"), nullable=True)
    package_name: Mapped[str] = mapped_column(String(128), default="")
    threads: Mapped[int] = mapped_column(Integer, default=2)
    max_upload_mb: Mapped[int] = mapped_column(Integer, default=5)
    tier: Mapped[int] = mapped_column(Integer, default=1)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="subscriptions")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|paid|approved|cancelled
    payment_method: Mapped[str] = mapped_column(String(32), default="")  # usdt_trc20|usdt_bep20|usdt|manual
    txid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PaymentTxid(Base):
    __tablename__ = "payment_txids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txid: Mapped[str] = mapped_column(String(128), unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingSettings(Base):
    __tablename__ = "billing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # USDT TRC-20 (Tron) — optional on-chain TxID verify via /paid
    usdt_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    usdt_wallet: Mapped[str] = mapped_column(String(128), default="")
    usdt_contract: Mapped[str] = mapped_column(String(128), default="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    usdt_api_base: Mapped[str] = mapped_column(String(255), default="https://apilist.tronscanapi.com")
    usdt_api_key: Mapped[str] = mapped_column(String(255), default="")
    # USDT BEP-20 (BNB Smart Chain) — Etherscan API V2 (chainid=56) + optional RPC; ≥20 confirmations
    usdt_bep20_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    usdt_bep20_wallet: Mapped[str] = mapped_column(String(128), default="")
    usdt_bep20_contract: Mapped[str] = mapped_column(
        String(128), default="0x55d398326f99059fF775485246999027B3197955"
    )
    usdt_bep20_api_base: Mapped[str] = mapped_column(String(255), default="https://api.etherscan.io/v2/api")
    usdt_bep20_api_key: Mapped[str] = mapped_column(String(255), default="")
    usdt_bep20_rpc_url: Mapped[str] = mapped_column(String(255), default="https://bsc-dataseed.binance.org/")
    manual_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_methods: Mapped[list] = mapped_column(JSON, default=list)
    allowed_extensions: Mapped[list] = mapped_column(JSON, default=lambda: [".txt", ".csv"])
    max_upload_mb: Mapped[int] = mapped_column(Integer, default=5)


class ProxyPool(Base):
    __tablename__ = "proxy_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    proxies_text: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workers: Mapped[list["WorkerNode"]] = relationship(back_populates="proxy_pool")


class WorkerNode(Base):
    __tablename__ = "worker_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    token_hash: Mapped[str] = mapped_column(String(255))
    token_prefix: Mapped[str] = mapped_column(String(16), default="")
    # SHA-256 hex of raw token for O(1) auth (not a substitute for rotating leaked tokens)
    token_lookup: Mapped[str] = mapped_column(String(64), default="", index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_draining: Mapped[bool] = mapped_column(Boolean, default=False)
    max_browsers: Mapped[int] = mapped_column(Integer, default=2)
    proxy_pool_id: Mapped[int | None] = mapped_column(ForeignKey("proxy_pools.id"), nullable=True)
    # Legacy FK to scrape_settings — no longer used for leases; worker_config is authoritative
    scrape_settings_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_settings.id"), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cpu_percent: Mapped[float] = mapped_column(Float, default=0)
    mem_percent: Mapped[float] = mapped_column(Float, default=0)
    disk_percent: Mapped[float] = mapped_column(Float, default=0)
    mem_used_gb: Mapped[float] = mapped_column(Float, default=0)
    mem_total_gb: Mapped[float] = mapped_column(Float, default=0)
    disk_used_gb: Mapped[float] = mapped_column(Float, default=0)
    disk_total_gb: Mapped[float] = mapped_column(Float, default=0)
    load_avg_1: Mapped[float] = mapped_column(Float, default=0)
    load_avg_5: Mapped[float] = mapped_column(Float, default=0)
    load_avg_15: Mapped[float] = mapped_column(Float, default=0)
    host_os: Mapped[str] = mapped_column(String(64), default="")
    hostname: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(64), default="")
    # Per-worker scrape flags (engine, delays, headless, …). Merged into lease settings.
    worker_config: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    proxy_pool: Mapped["ProxyPool | None"] = relationship(back_populates="workers")


class ScrapeSettings(Base):
    __tablename__ = "scrape_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="Default")
    slug: Mapped[str] = mapped_column(String(64), default="default", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    engine: Mapped[str] = mapped_column(String(32), default="chrome")
    threads: Mapped[int] = mapped_column(Integer, default=2)
    block_resources: Mapped[str] = mapped_column(String(16), default="media")
    scrape_websites: Mapped[str] = mapped_column(String(8), default="yes")
    max_results: Mapped[int] = mapped_column(Integer, default=0)
    chunk_size: Mapped[int] = mapped_column(Integer, default=500)
    min_delay: Mapped[float] = mapped_column(Float, default=2.0)
    max_delay: Mapped[float] = mapped_column(Float, default=5.0)
    cooldown_every: Mapped[int] = mapped_column(Integer, default=25)
    cooldown_min: Mapped[float] = mapped_column(Float, default=25.0)
    cooldown_max: Mapped[float] = mapped_column(Float, default=60.0)
    # Legacy columns — leases use global CaptchaSettings; kept for one-time migrate/fallback
    captcha_provider: Mapped[str] = mapped_column(String(32), default="none")
    captcha_key: Mapped[str] = mapped_column(String(255), default="")
    captcha_host: Mapped[str] = mapped_column(String(255), default="")
    captcha_retries: Mapped[int] = mapped_column(Integer, default=2)
    captcha_backup_provider: Mapped[str] = mapped_column(String(32), default="none")
    captcha_backup_key: Mapped[str] = mapped_column(String(255), default="")
    captcha_backup_host: Mapped[str] = mapped_column(String(255), default="")
    nav_timeout: Mapped[int] = mapped_column(Integer, default=45)
    proxy_attempts: Mapped[int] = mapped_column(Integer, default=3)
    # Extra scrape/worker flags (defaults for new workers + lease fallback)
    headless: Mapped[bool] = mapped_column(Boolean, default=True)
    no_stealth: Mapped[bool] = mapped_column(Boolean, default=False)
    browser_path: Mapped[str] = mapped_column(String(512), default="")
    geoip: Mapped[bool] = mapped_column(Boolean, default=False)
    preflight_timeout: Mapped[float] = mapped_column(Float, default=12.0)
    no_preflight: Mapped[bool] = mapped_column(Boolean, default=False)
    fresh: Mapped[bool] = mapped_column(Boolean, default=False)
    debug: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|completed|stopped|failed
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    keywords_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    locations_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    total_searches: Mapped[int] = mapped_column(Integer, default=0)
    done_searches: Mapped[int] = mapped_column(Integer, default=0)
    rows_saved: Mapped[int] = mapped_column(Integer, default=0)
    result_zip: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped["User"] = relationship(back_populates="jobs")
    chunks: Mapped[list["JobChunk"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobChunk(Base):
    __tablename__ = "job_chunks"
    __table_args__ = (UniqueConstraint("job_id", "chunk_id", name="uq_job_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[int] = mapped_column(Integer)
    start_index: Mapped[int] = mapped_column(Integer)
    end_index: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|leased|done
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("worker_nodes.id"), nullable=True)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    # Best-effort in-flight progress while state=leased (cleared on ack / reclaim).
    # Not counted into Job.rows_saved — _job_out adds these on top for live UI.
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_rows: Mapped[int] = mapped_column(Integer, default=0)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["Job"] = relationship(back_populates="chunks")


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    token: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(128), default="")
    mode: Mapped[str] = mapped_column(String(16), default="polling")  # polling|webhook
    welcome_text: Mapped[str] = mapped_column(Text, default="Welcome to the GMaps Scraper bot.")
    notify_interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    support_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    support_chat_id: Mapped[str] = mapped_column(String(64), default="")
    public_packages: Mapped[bool] = mapped_column(Boolean, default=True)
    deliver_results_telegram: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_commands_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BotCommand(Base):
    __tablename__ = "bot_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    command: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    response_text: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    audience: Mapped[str] = mapped_column(String(32), default="users")  # everyone|users|admins|subscribers
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BotWorkflow(Base):
    __tablename__ = "bot_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_id: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="ticket",
        order_by="SupportMessage.id",
        cascade="all, delete-orphan",
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    sender: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    admin_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
