from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
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
    return s
