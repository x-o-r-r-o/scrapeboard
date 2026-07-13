from functools import lru_cache
from pathlib import Path
import os
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

INSECURE_SECRET_KEYS = {
    "",
    "dev-only-change-me-in-production-please-use-long-secret",
    "change-me-to-a-long-random-string",
    "changeme",
    "secret",
}
INSECURE_BOOTSTRAP_PASSWORDS = {
    "",
    "ChangeMeNow!",
    "changeme",
    "admin",
    "password",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Scrapeboard"
    secret_key: str = "dev-only-change-me-in-production-please-use-long-secret"
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR / 'panel.db'}"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,https://scrape.cvmso.com"
    access_token_expire_minutes: int = 60
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "ChangeMeNow!"
    bootstrap_admin_email: str = "admin@localhost"
    uploads_dir: Path = DATA_DIR / "uploads"
    results_dir: Path = DATA_DIR / "results"
    max_login_failures: int = 5
    lockout_minutes: int = 15
    public_url: str = "http://127.0.0.1:5173"
    api_port: int = 3010
    # development | production
    environment: str = "development"
    # Max bytes for a single worker chunk ZIP upload
    worker_upload_max_bytes: int = 50 * 1024 * 1024
    # Max CSV members / uncompressed bytes inside a worker ZIP
    worker_zip_max_members: int = 200
    worker_zip_max_uncompressed_bytes: int = 200 * 1024 * 1024
    # Daily fleet auto-update: queue git update for online workers (UTC hour).
    # Host-side timers on each worker also check git daily independently.
    worker_auto_update_enabled: bool = True
    worker_auto_update_hour_utc: int = 4
    worker_auto_update_ref: str = "main"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        env = (self.environment or "").strip().lower()
        if env in ("production", "prod"):
            return True
        # HTTPS public URL implies production hardening
        return self.public_url.lower().startswith("https://")


def _validate_security(s: Settings) -> None:
    if not s.is_production:
        return
    problems: list[str] = []
    if s.secret_key.strip() in INSECURE_SECRET_KEYS or len(s.secret_key.strip()) < 32:
        problems.append("SECRET_KEY must be a unique random string (>=32 chars) in production")
    if s.bootstrap_admin_password in INSECURE_BOOTSTRAP_PASSWORDS:
        problems.append("BOOTSTRAP_ADMIN_PASSWORD must not be a default/weak value in production")
    if problems:
        for p in problems:
            print(f"[fatal] {p}", file=sys.stderr)
        raise RuntimeError("Refusing to start with insecure production settings: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    # Allow ENVIRONMENT env alias
    if "ENVIRONMENT" in os.environ and "environment" not in os.environ:
        os.environ.setdefault("environment", os.environ["ENVIRONMENT"])
    s = Settings()
    # Keep uploads/results next to the DB file when DATABASE_URL points at panel/data
    if "panel.db" in s.database_url:
        try:
            db_path = s.database_url.split("///", 1)[-1]
            data_root = Path(db_path).resolve().parent
            s.uploads_dir = data_root / "uploads"
            s.results_dir = data_root / "results"
        except Exception:
            pass
    s.uploads_dir.mkdir(parents=True, exist_ok=True)
    s.results_dir.mkdir(parents=True, exist_ok=True)
    _validate_security(s)
    return s
