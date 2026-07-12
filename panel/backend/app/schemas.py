from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


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
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr | str
    password: str = Field(min_length=8)
    role: Literal["admin", "user"] = "user"
    telegram_id: str | None = None
    perms: dict[str, Any] = Field(default_factory=dict)


class UserUpdate(BaseModel):
    email: EmailStr | str | None = None
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
    telegram_id: str | None = None
    perms: dict[str, Any] | None = None
    password: str | None = Field(default=None, min_length=8)
    reset_2fa: bool = False


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
    is_active: bool = True


class PackageUpdate(BaseModel):
    name: str | None = None
    tier: int | None = None
    price_usdt: float | None = None
    duration_days: int | None = None
    threads: int | None = None
    max_upload_mb: int | None = None
    allowed_engines: list | None = None
    is_active: bool | None = None


class BillingSettingsOut(BaseModel):
    enabled: bool
    usdt_enabled: bool
    usdt_wallet: str
    usdt_contract: str
    usdt_api_base: str
    usdt_api_key_configured: bool
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


class ProxyPoolOut(BaseModel):
    id: int
    name: str
    description: str
    proxy_count: int
    is_active: bool

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


class WorkerConfigUpdate(BaseModel):
    """Per-worker scrape flags. Omitted/null fields are left unchanged on PATCH."""

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
    captcha_provider: str | None = None
    captcha_key: str | None = None
    captcha_host: str | None = None
    captcha_retries: int | None = None
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


class WorkerOut(BaseModel):
    id: int
    name: str
    token_prefix: str
    is_enabled: bool
    is_draining: bool
    max_browsers: int
    proxy_pool_id: int | None
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

    model_config = {"from_attributes": True}


class WorkerCreate(BaseModel):
    name: str
    max_browsers: int = 2
    proxy_pool_id: int | None = None
    # If omitted, seeded from global Scrape settings
    worker_config: WorkerConfigUpdate | None = None
    use_global_scrape_defaults: bool = True


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
    # When true, replace worker_config from current global Scrape settings
    reset_config_from_global: bool = False


class ScrapeSettingsOut(BaseModel):
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
    captcha_provider: str
    captcha_key_configured: bool
    captcha_host: str
    captcha_retries: int
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


class ScrapeSettingsUpdate(BaseModel):
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
    captcha_provider: str | None = None
    captcha_key: str | None = None
    captcha_host: str | None = None
    captcha_retries: int | None = None
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


class JobOut(BaseModel):
    id: int
    public_id: str
    owner_id: int
    status: str
    settings: dict
    total_searches: int
    done_searches: int
    rows_saved: int
    result_zip: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    pct: float = 0

    model_config = {"from_attributes": True}


class JobCreate(BaseModel):
    engine: str | None = None
    threads: int | None = None
    scrape_websites: str | None = None
    max_results: int | None = None


class BotSettingsOut(BaseModel):
    enabled: bool
    token_configured: bool
    username: str
    mode: str
    welcome_text: str
    notify_interval_sec: int
    support_enabled: bool
    support_chat_id: str
    public_packages: bool
    deliver_results_telegram: bool
    admin_commands_enabled: bool


class BotSettingsUpdate(BaseModel):
    enabled: bool | None = None
    token: str | None = None
    username: str | None = None
    mode: str | None = None
    welcome_text: str | None = None
    notify_interval_sec: int | None = None
    support_enabled: bool | None = None
    support_chat_id: str | None = None
    public_packages: bool | None = None
    deliver_results_telegram: bool | None = None
    admin_commands_enabled: bool | None = None


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

    model_config = {"from_attributes": True}


class BotCommandUpdate(BaseModel):
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
    definition: dict

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    detail: str
