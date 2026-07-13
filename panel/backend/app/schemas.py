from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def _coerce_telegram_id(value: Any, *, allow_group: bool = False) -> str | None:
    """Shared schema coercion — keep digit strings; reject usernames."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("telegram_id must be a numeric Telegram id")
    if isinstance(value, int):
        s = str(value)
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError("telegram_id must be a numeric Telegram id")
        s = str(int(value))
    else:
        s = str(value).strip().replace(" ", "").replace(",", "")
        if not s:
            return None
        if s.startswith("@"):
            raise ValueError("telegram_id must be numeric, not a @username")
        if "e" in s.lower() or "." in s:
            try:
                f = float(s)
            except ValueError as exc:
                raise ValueError("telegram_id must be a numeric Telegram id") from exc
            if not f.is_integer():
                raise ValueError("telegram_id must be a numeric Telegram id")
            s = str(int(f))
    if s.startswith("-"):
        body = s[1:]
        if not allow_group or not body.isdigit():
            raise ValueError("telegram_id must be a numeric Telegram user id")
        return f"-{body}"
    if not s.isdigit():
        raise ValueError("telegram_id must be a numeric Telegram id")
    if s.startswith("0") and len(s) > 1:
        s = str(int(s))
    return s


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_setup_2fa: bool = False
    must_change_password: bool = False
    totp_setup_required: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str | None = None
    recaptcha_token: str | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TotpEnableRequest(BaseModel):
    code: str


class UserOut(BaseModel):
    id: int
    username: str
    email: EmailStr | str
    role: str
    is_active: bool
    must_change_password: bool
    totp_enabled: bool
    telegram_id: str | None
    perms: dict[str, Any]
    worker_ids: list[int] = []
    dedicated_worker: bool = False
    created_at: datetime
    subscription_package: str | None = None
    subscription_id: int | None = None
    subscription_expires_at: datetime | None = None
    has_active_subscription: bool = False

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    """Create panel admin (login) or Telegram user (bot-linked, no panel login fields)."""

    role: Literal["admin", "user"] = "user"
    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: EmailStr | str | None = None
    password: str | None = Field(default=None, min_length=8)
    telegram_id: str | int | None = None
    package_id: int | None = None
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    notify: bool = False
    perms: dict[str, Any] = Field(default_factory=dict)
    worker_ids: list[int] | None = None

    @field_validator("telegram_id", mode="before")
    @classmethod
    def _norm_telegram_id(cls, v: Any) -> str | None:
        return _coerce_telegram_id(v, allow_group=False)

    @model_validator(mode="after")
    def validate_by_role(self) -> "UserCreate":
        if self.role == "admin":
            if not (self.username or "").strip():
                raise ValueError("Admin users require username")
            if not self.email:
                raise ValueError("Admin users require email")
            if not self.password:
                raise ValueError("Admin users require temporary password")
            if self.telegram_id is not None:
                self.telegram_id = str(self.telegram_id)
        else:
            tid = (str(self.telegram_id).strip() if self.telegram_id is not None else "")
            if not tid:
                raise ValueError("Telegram users require telegram_id")
            self.telegram_id = tid
        return self


class UserUpdate(BaseModel):
    email: EmailStr | str | None = None
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
    telegram_id: str | int | None = None
    perms: dict[str, Any] | None = None
    worker_ids: list[int] | None = None
    password: str | None = Field(default=None, min_length=8)
    reset_2fa: bool = False
    username: str | None = Field(default=None, min_length=3, max_length=64)

    @field_validator("telegram_id", mode="before")
    @classmethod
    def _norm_telegram_id(cls, v: Any) -> str | None:
        return _coerce_telegram_id(v, allow_group=False)


class UserPermsSchema(BaseModel):
    keys: list[dict[str, Any]]
    defaults: dict[str, Any]
    engines: list[str]


class SecuritySettingsOut(BaseModel):
    recaptcha_mode: Literal["none", "v2", "v3"]
    recaptcha_site_key: str
    recaptcha_v3_min_score: float
    max_login_failures: int
    lockout_minutes: int
    # secret never returned fully to non-setup; admin gets masked
    recaptcha_secret_configured: bool = False


class SecuritySettingsUpdate(BaseModel):
    recaptcha_mode: Literal["none", "v2", "v3"] | None = None
    recaptcha_site_key: str | None = None
    recaptcha_secret_key: str | None = None
    recaptcha_v3_min_score: float | None = None
    max_login_failures: int | None = None
    lockout_minutes: int | None = None


class CaptchaSettingsOut(BaseModel):
    captcha_provider: str
    captcha_key_configured: bool
    captcha_host: str
    captcha_retries: int
    captcha_backup_provider: str = "none"
    captcha_backup_key_configured: bool = False
    captcha_backup_host: str = ""


class CaptchaSettingsUpdate(BaseModel):
    captcha_provider: str | None = None
    captcha_key: str | None = None
    captcha_host: str | None = None
    captcha_retries: int | None = None
    captcha_backup_provider: str | None = None
    captcha_backup_key: str | None = None
    captcha_backup_host: str | None = None


class ScraperCatalogItem(BaseModel):
    id: str
    label: str
    group: str
    group_label: str
    description: str
    implemented: bool
    risk: str
    inputs: str
    selectable: bool


class ScraperSettingsOut(BaseModel):
    enabled_sources: list[str]
    catalog: list[ScraperCatalogItem] = Field(default_factory=list)


class ScraperSettingsUpdate(BaseModel):
    enabled_sources: list[str]


class PackageOut(BaseModel):
    id: int
    slug: str
    name: str
    tier: int
    price_usdt: float
    duration_days: int
    threads: int
    max_upload_mb: int
    allowed_engines: list
    allowed_sources: list = Field(
        default_factory=lambda: [
            "gmaps",
            "tiktok_shop",
            "google_search",
            "email_harvest",
            "email_validate",
            "youtube",
            "reddit",
            "pinterest",
            "tiktok",
            "facebook_pages",
            "facebook_groups",
            "facebook_posts",
            "facebook_comments",
            "instagram",
            "linkedin",
            "twitter",
        ]
    )
    description: str = ""
    headings: list = Field(default_factory=list)
    features: list = Field(default_factory=list)
    dedicated_worker: bool = False
    scrape_defaults: dict = Field(default_factory=dict)
    chunk_size: int = 500
    is_active: bool

    model_config = {"from_attributes": True}


class PackageCreate(BaseModel):
    slug: str
    name: str
    tier: int = 1
    price_usdt: float
    duration_days: int = 30
    threads: int = 2
    max_upload_mb: int = 5
    allowed_engines: list = Field(default_factory=lambda: ["all"])
    allowed_sources: list = Field(
        default_factory=lambda: [
            "gmaps",
            "tiktok_shop",
            "google_search",
            "email_harvest",
            "email_validate",
            "youtube",
            "reddit",
            "pinterest",
            "tiktok",
            "facebook_pages",
            "facebook_groups",
            "facebook_posts",
            "facebook_comments",
            "instagram",
            "linkedin",
            "twitter",
        ]
    )
    description: str = ""
    headings: list = Field(default_factory=list)
    features: list = Field(default_factory=list)
    dedicated_worker: bool = False
    scrape_defaults: dict | None = None
    chunk_size: int = 500
    is_active: bool = True


class PackageUpdate(BaseModel):
    name: str | None = None
    tier: int | None = None
    price_usdt: float | None = None
    duration_days: int | None = None
    threads: int | None = None
    max_upload_mb: int | None = None
    allowed_engines: list | None = None
    allowed_sources: list | None = None
    description: str | None = None
    headings: list | None = None
    features: list | None = None
    dedicated_worker: bool | None = None
    scrape_defaults: dict | None = None
    chunk_size: int | None = None
    is_active: bool | None = None


class BillingSettingsOut(BaseModel):
    enabled: bool
    usdt_enabled: bool
    usdt_wallet: str
    usdt_contract: str
    usdt_api_base: str
    usdt_api_key_configured: bool
    usdt_bep20_enabled: bool = False
    usdt_bep20_wallet: str = ""
    usdt_bep20_contract: str = "0x55d398326f99059fF775485246999027B3197955"
    usdt_bep20_api_base: str = "https://api.etherscan.io/v2/api"
    usdt_bep20_api_key_configured: bool = False
    usdt_bep20_rpc_url: str = "https://bsc-dataseed.binance.org/"
    manual_enabled: bool
    manual_methods: list
    allowed_extensions: list
    max_upload_mb: int


class BillingSettingsUpdate(BaseModel):
    enabled: bool | None = None
    usdt_enabled: bool | None = None
    usdt_wallet: str | None = None
    usdt_contract: str | None = None
    usdt_api_base: str | None = None
    usdt_api_key: str | None = None
    usdt_bep20_enabled: bool | None = None
    usdt_bep20_wallet: str | None = None
    usdt_bep20_contract: str | None = None
    usdt_bep20_api_base: str | None = None
    usdt_bep20_api_key: str | None = None
    usdt_bep20_rpc_url: str | None = None
    manual_enabled: bool | None = None
    manual_methods: list | None = None
    allowed_extensions: list | None = None
    max_upload_mb: int | None = None


class SubscriptionOut(BaseModel):
    id: int
    package_name: str
    threads: int
    max_upload_mb: int
    tier: int
    starts_at: datetime
    expires_at: datetime
    is_active: bool
    days_left: float = 0


class SubscriptionAdminOut(BaseModel):
    id: int
    user_id: int
    username: str
    telegram_id: str | None = None
    package_id: int | None = None
    package_name: str
    threads: int
    max_upload_mb: int
    tier: int
    starts_at: datetime
    expires_at: datetime
    is_active: bool
    days_left: float = 0
    user_is_active: bool = True


class SubscriptionUpdate(BaseModel):
    package_id: int | None = None
    package_name: str | None = None
    threads: int | None = None
    max_upload_mb: int | None = None
    tier: int | None = None
    expires_at: datetime | None = None
    is_active: bool | None = None


class SubscriptionExtend(BaseModel):
    days: int = Field(ge=1, le=3650, default=30)


class GrantRequest(BaseModel):
    user_id: int | None = None
    telegram_id: str | None = None
    package_id: int
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    notify: bool = True


class TelegramUserCreate(BaseModel):
    telegram_id: str = Field(min_length=3, max_length=64)
    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: str | None = None
    password: str | None = Field(default=None, min_length=8)
    package_id: int | None = None
    duration_days: int | None = Field(default=None, ge=1, le=3650)
    is_active: bool = True
    notify: bool = False
    perms: dict[str, Any] | None = None
    worker_ids: list[int] | None = None


class TelegramUserUpdate(BaseModel):
    telegram_id: str | None = Field(default=None, min_length=3, max_length=64)
    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: EmailStr | str | None = None
    is_active: bool | None = None
    unlink_telegram: bool = False
    password: str | None = Field(default=None, min_length=8)
    reset_2fa: bool = False
    perms: dict[str, Any] | None = None
    worker_ids: list[int] | None = None


class SubscriberOut(BaseModel):
    user_id: int
    username: str
    email: str
    role: str
    is_active: bool
    telegram_id: str | None
    totp_enabled: bool
    created_at: datetime
    subscription: SubscriptionAdminOut | None = None
    has_active_subscription: bool = False
    perms: dict[str, Any] = Field(default_factory=dict)
    worker_ids: list[int] = []
    dedicated_worker: bool = False


class ProxyPoolOut(BaseModel):
    id: int
    name: str
    description: str
    proxy_count: int
    is_active: bool
    worker_ids: list[int] = []
    worker_names: list[str] = []

    model_config = {"from_attributes": True}


class ProxyPoolCreate(BaseModel):
    name: str
    description: str = ""
    proxies_text: str = ""
    is_active: bool = True


class ProxyPoolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    proxies_text: str | None = None
    is_active: bool | None = None


class ProxyPoolAssign(BaseModel):
    worker_ids: list[int] = Field(default_factory=list)


class WorkerConfigUpdate(BaseModel):
    """Per-worker scrape flags. Omitted/null fields are left unchanged on PATCH.

    Captcha is global (Admin → Captcha); not accepted per worker.
    """

    engine: str | None = None
    threads: int | None = None
    block_resources: str | None = None
    scrape_websites: str | None = None
    max_results: int | None = None
    min_delay: float | None = None
    max_delay: float | None = None
    cooldown_every: int | None = None
    cooldown_min: float | None = None
    cooldown_max: float | None = None
    nav_timeout: int | None = None
    proxy_attempts: int | None = None
    headless: bool | None = None
    no_stealth: bool | None = None
    browser_path: str | None = None
    geoip: bool | None = None
    preflight_timeout: float | None = None
    no_preflight: bool | None = None
    fresh: bool | None = None
    debug: bool | None = None


class WorkerUpdateStatusOut(BaseModel):
    status: str = "idle"  # idle|pending|updating|success|failed
    ref: str = "main"
    message: str = ""
    requested_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class WorkerOut(BaseModel):
    id: int
    name: str
    token_prefix: str
    is_enabled: bool
    is_draining: bool
    max_browsers: int
    proxy_pool_id: int | None
    proxy_pool_name: str | None = None
    last_seen_at: datetime | None
    cpu_percent: float
    mem_percent: float
    disk_percent: float = 0
    mem_used_gb: float = 0
    mem_total_gb: float = 0
    disk_used_gb: float = 0
    disk_total_gb: float = 0
    load_avg_1: float = 0
    load_avg_5: float = 0
    load_avg_15: float = 0
    host_os: str = ""
    hostname: str = ""
    version: str
    online: bool = False
    active_leases: int = 0
    worker_config: dict = {}
    update: WorkerUpdateStatusOut = WorkerUpdateStatusOut()

    model_config = {"from_attributes": True}


class WorkerCreate(BaseModel):
    name: str
    max_browsers: int = 2
    proxy_pool_id: int | None = None
    # If omitted, seeded from built-in DEFAULT_WORKER_CONFIG
    worker_config: WorkerConfigUpdate | None = None
    # Optional: seed worker_config from this package's scrape_defaults
    seed_from_package_id: int | None = None


class WorkerCreateResponse(BaseModel):
    worker: WorkerOut
    token: str
    install_hint: str


class WorkerUpdate(BaseModel):
    name: str | None = None
    is_enabled: bool | None = None
    is_draining: bool | None = None
    max_browsers: int | None = None
    proxy_pool_id: int | None = None
    worker_config: WorkerConfigUpdate | None = None
    # When true, replace worker_config with built-in defaults (or seed_from_package_id)
    reset_config_to_defaults: bool = False
    seed_from_package_id: int | None = None


class WorkerFleetUpdateRequest(BaseModel):
    """Admin: queue git update on one or more workers (delivered via heartbeat/lease)."""

    ref: str = "main"  # branch/tag/SHA, or "latest" for current-branch pull
    worker_ids: list[int] | None = None  # None / empty = all workers


class WorkerFleetUpdateResponse(BaseModel):
    ok: bool = True
    ref: str
    queued: int
    workers: list[WorkerOut]


class WorkerUpdateStatusIn(BaseModel):
    status: str  # updating|success|failed
    message: str = ""
    ref: str | None = None


class ScrapeSettingsOut(BaseModel):
    id: int = 1
    name: str = "Default"
    slug: str = "default"
    description: str = ""
    is_default: bool = False
    is_active: bool = True
    engine: str
    threads: int
    block_resources: str
    scrape_websites: str
    max_results: int
    chunk_size: int
    min_delay: float
    max_delay: float
    cooldown_every: int
    cooldown_min: float
    cooldown_max: float
    nav_timeout: int
    proxy_attempts: int
    headless: bool = True
    no_stealth: bool = False
    browser_path: str = ""
    geoip: bool = False
    preflight_timeout: float = 12.0
    no_preflight: bool = False
    fresh: bool = False
    debug: bool = False
    worker_count: int = 0
    package_count: int = 0


class ScrapeSettingsCreate(BaseModel):
    name: str
    slug: str | None = None
    description: str = ""
    clone_from_id: int | None = None
    is_default: bool = False
    is_active: bool = True


class ScrapeSettingsUpdate(BaseModel):
    """Profile scrape flags. Captcha is global (Admin → Captcha); not accepted here."""

    name: str | None = None
    slug: str | None = None
    description: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    engine: str | None = None
    threads: int | None = None
    block_resources: str | None = None
    scrape_websites: str | None = None
    max_results: int | None = None
    chunk_size: int | None = None
    min_delay: float | None = None
    max_delay: float | None = None
    cooldown_every: int | None = None
    cooldown_min: float | None = None
    cooldown_max: float | None = None
    nav_timeout: int | None = None
    proxy_attempts: int | None = None
    headless: bool | None = None
    no_stealth: bool | None = None
    browser_path: str | None = None
    geoip: bool | None = None
    preflight_timeout: float | None = None
    no_preflight: bool | None = None
    fresh: bool | None = None
    debug: bool | None = None
    apply_to_workers: bool = False


class JobWorkerLeaseOut(BaseModel):
    worker_id: int
    worker_name: str
    leased_chunks: int
    online: bool = False


class JobOut(BaseModel):
    id: int
    public_id: str
    name: str | None = None
    owner_id: int
    owner_username: str | None = None
    owner_telegram_id: str | None = None
    source: str = "gmaps"
    channels: list = Field(default_factory=list)
    status: str
    settings: dict
    threads: int = 1
    total_searches: int
    done_searches: int
    rows_saved: int
    result_zip: str | None
    result_exists: bool = False
    result_bytes: int | None = None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    pct: float = 0
    waiting_for_threads: bool = False
    # When queued behind another active job (one-at-a-time policy)
    blocking_job_public_id: str | None = None
    blocking_job_label: str | None = None
    # Admin-only operational detail (omitted / empty for non-admins)
    chunks_pending: int | None = None
    chunks_leased: int | None = None
    chunks_done: int | None = None
    workers: list[JobWorkerLeaseOut] | None = None

    model_config = {"from_attributes": True}


class JobUpdate(BaseModel):
    """Edit a queued job (threads / engine / scrape_websites / optional name). Name may also be set while running."""

    threads: int | None = Field(default=None, ge=1, le=64)
    engine: str | None = None
    scrape_websites: str | None = None
    name: str | None = None


class ThreadQuotaOut(BaseModel):
    thread_allowance: int
    threads_in_use: int
    threads_free: int


class JobFileEntry(BaseModel):
    name: str
    path: str
    size_bytes: int
    kind: str  # zip | part | merged | input | other


class JobFilesOut(BaseModel):
    job_id: int
    public_id: str
    files: list[JobFileEntry]
    total_bytes: int = 0


class StorageOwnerOut(BaseModel):
    user_id: int
    username: str
    telegram_id: str | None
    uploads_bytes: int
    results_bytes: int
    job_count: int


class JobCreate(BaseModel):
    name: str | None = None
    engine: str | None = None
    threads: int | None = None
    scrape_websites: str | None = None
    max_results: int | None = None


class BotSettingsOut(BaseModel):
    enabled: bool
    token_configured: bool
    token_hint: str = ""
    username: str
    mode: str
    welcome_text: str
    notify_interval_sec: int
    support_enabled: bool
    support_chat_id: str
    public_packages: bool
    deliver_results_telegram: bool
    admin_commands_enabled: bool
    suggested_support_chat_id: str | None = None
    admin_setup_hint: str = ""
    runtime_status: str = "stopped"
    runtime_task_running: bool = False
    runtime_error: str = ""
    runtime_last_ok_at: float | None = None
    runtime_updates_handled: int = 0


class BotSettingsUpdate(BaseModel):
    enabled: bool | None = None
    token: str | None = None
    clear_token: bool | None = None
    username: str | None = None
    mode: str | None = None
    welcome_text: str | None = None
    notify_interval_sec: int | None = None
    support_enabled: bool | None = None
    support_chat_id: str | int | None = None
    public_packages: bool | None = None
    deliver_results_telegram: bool | None = None
    admin_commands_enabled: bool | None = None

    @field_validator("support_chat_id", mode="before")
    @classmethod
    def _norm_support_chat_id(cls, v: Any) -> str | None:
        if v is None or v == "":
            return ""
        try:
            return _coerce_telegram_id(v, allow_group=True) or ""
        except ValueError as exc:
            raise ValueError(
                "support_chat_id must be a numeric Telegram user id or group id "
                "(e.g. your admin id from /whoami, or -100… for a group)"
            ) from exc


class BotRuntimeStatusOut(BaseModel):
    status: str
    task_running: bool
    last_error: str
    last_ok_at: float | None
    updates_handled: int
    offset: int | None
    enabled: bool
    token_configured: bool
    username: str
    hint: str = ""
    admin_setup_hint: str = ""
    suggested_support_chat_id: str | None = None


class BotCommandOut(BaseModel):
    id: int
    key: str
    command: str
    title: str
    description: str
    response_text: str
    enabled: bool
    audience: str
    sort_order: int
    is_builtin: bool = False
    handler: Literal["builtin", "static"] = "static"

    model_config = {"from_attributes": True}


class BotCommandCreate(BaseModel):
    key: str = ""
    command: str
    title: str = ""
    description: str = ""
    response_text: str = ""
    enabled: bool = True
    audience: str = "everyone"
    sort_order: int = 0


class BotCommandUpdate(BaseModel):
    command: str | None = None
    title: str | None = None
    description: str | None = None
    response_text: str | None = None
    enabled: bool | None = None
    audience: str | None = None
    sort_order: int | None = None


class BotWorkflowOut(BaseModel):
    id: int
    key: str
    name: str
    description: str
    enabled: bool
    is_demo: bool
    sort_order: int = 0
    definition: dict

    model_config = {"from_attributes": True}


class BotWorkflowCreate(BaseModel):
    key: str
    name: str
    description: str = ""
    enabled: bool = True
    sort_order: int = 0
    definition: dict = {}


class BotWorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    definition: dict | None = None


class MessageOut(BaseModel):
    detail: str


class SupportMessageOut(BaseModel):
    id: int
    ticket_id: int
    sender: str
    admin_user_id: int | None = None
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SupportTicketOut(BaseModel):
    id: int
    user_id: int | None = None
    telegram_id: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    closed_by_id: int | None = None
    messages: list[SupportMessageOut] = []

    model_config = {"from_attributes": True}


class SupportTicketListOut(BaseModel):
    id: int
    user_id: int | None = None
    telegram_id: str
    message: str
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    message_count: int = 0

    model_config = {"from_attributes": True}


class SupportReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SupportCloseIn(BaseModel):
    reason: str = Field(default="", max_length=2000)
