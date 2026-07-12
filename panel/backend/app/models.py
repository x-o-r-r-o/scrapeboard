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
    payment_method: Mapped[str] = mapped_column(String(32), default="")  # usdt|manual
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
    usdt_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    usdt_wallet: Mapped[str] = mapped_column(String(128), default="")
    usdt_contract: Mapped[str] = mapped_column(String(128), default="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    usdt_api_base: Mapped[str] = mapped_column(String(255), default="https://apilist.tronscanapi.com")
    usdt_api_key: Mapped[str] = mapped_column(String(255), default="")
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
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_draining: Mapped[bool] = mapped_column(Boolean, default=False)
    max_browsers: Mapped[int] = mapped_column(Integer, default=2)
    proxy_pool_id: Mapped[int | None] = mapped_column(ForeignKey("proxy_pools.id"), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cpu_percent: Mapped[float] = mapped_column(Float, default=0)
    mem_percent: Mapped[float] = mapped_column(Float, default=0)
    version: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    proxy_pool: Mapped["ProxyPool | None"] = relationship(back_populates="workers")


class ScrapeSettings(Base):
    __tablename__ = "scrape_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
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
    captcha_provider: Mapped[str] = mapped_column(String(32), default="none")
    captcha_key: Mapped[str] = mapped_column(String(255), default="")
    captcha_host: Mapped[str] = mapped_column(String(255), default="")
    captcha_retries: Mapped[int] = mapped_column(Integer, default=2)
    nav_timeout: Mapped[int] = mapped_column(Integer, default=45)
    proxy_attempts: Mapped[int] = mapped_column(Integer, default=3)


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
    chunks: Mapped[list["JobChunk"]] = relationship(back_populates="job")


class JobChunk(Base):
    __tablename__ = "job_chunks"
    __table_args__ = (UniqueConstraint("job_id", "chunk_id", name="uq_job_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    chunk_id: Mapped[int] = mapped_column(Integer)
    start_index: Mapped[int] = mapped_column(Integer)
    end_index: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|leased|done
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("worker_nodes.id"), nullable=True)
    rows: Mapped[int] = mapped_column(Integer, default=0)
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
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    telegram_id: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
