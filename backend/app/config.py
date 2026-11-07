from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: Literal["development", "test", "demo", "production"] = "development"
    database_url: str = "sqlite:///./runtime/development.db"
    storage_dir: Path = Path("runtime/files")
    public_origin: str = "http://localhost:5173"
    demo_lan_origin: str = ""
    secure_cookies: bool = False
    model_mode: Literal["mock", "deepseek"] = "mock"
    embedding_mode: Literal["hash", "bge"] = "hash"
    bge_cache_dir: Path = Path("runtime/bge-cache")
    material_mode: Literal["synthetic", "official"] = "synthetic"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    model_timeout_seconds: int = 25
    model_concurrency: int = 2
    model_daily_limit: int = 500
    mail_mode: Literal["mock", "smtp"] = "mock"
    allow_real_email: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_recipient: str = ""
    weekly_recipient: str = ""
    weekly_mail_enabled: bool = True
    mock_mail_failures: int = 0
    mail_max_attempts: int = 4
    session_hours: int = 24
    admin_hours: int = 8
    max_upload_mb: int = 15
    ocr_enabled: bool = False
    ocr_language: str = "chi_sim+eng"
    ocr_timeout_seconds: int = 30
    tesseract_command: str = "tesseract"
    pdftoppm_command: str = "pdftoppm"

    def validate_runtime(self):
        if self.environment == "production":
            if not self.secure_cookies or not self.public_origin.startswith("https://"):
                raise RuntimeError("production requires HTTPS and Secure cookies")
            if not self.database_url.startswith("postgresql"):
                raise RuntimeError("production requires PostgreSQL")
        if self.model_mode == "deepseek" and not self.deepseek_api_key:
            raise RuntimeError("DeepSeek is selected but credentials are missing")


@lru_cache
def settings() -> Settings:
    return Settings()
