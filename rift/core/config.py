import secrets
from pathlib import Path
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=True, extra="ignore",
    )

    APP_NAME: str = "RIFT EFFECT"
    APP_VERSION: str = "1.0.0"
    ENV: str = "production"
    DEBUG: bool = False
    SECRET_KEY: str = secrets.token_hex(32)
    DOMAIN: str = "localhost"
    FRONTEND_URL: str = "http://localhost"

    DATABASE_URL: str = "postgresql+asyncpg://rift:riftpass@db:5432/rift"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    REDIS_URL: str = "redis://:redispass@redis:6379/0"
    CELERY_BROKER: str = "redis://:redispass@redis:6379/1"
    CELERY_BACKEND: str = "redis://:redispass@redis:6379/2"

    STORAGE_BACKEND: str = "local"
    STORAGE_ROOT: Path = Path("/data/storage")
    R2_ACCOUNT_ID: Optional[str] = None
    R2_ACCESS_KEY: Optional[str] = None
    R2_SECRET_KEY: Optional[str] = None
    R2_BUCKET: Optional[str] = None
    R2_PUBLIC_URL: Optional[str] = None

    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_STARTER: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_STUDIO: str = ""
    PRICE_PER_VIDEO_CENTS: int = 2500

    PLAN_STARTER_RENDERS: int = 10
    PLAN_PRO_RENDERS: int = 50
    PLAN_STUDIO_RENDERS: int = 200

    JWT_SECRET: str = secrets.token_hex(32)
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_EXPIRE_DAYS: int = 30

    SENDGRID_API_KEY: Optional[str] = None
    EMAIL_FROM: str = "noreply@rifteffect.com"
    EMAIL_FROM_NAME: str = "RIFT EFFECT"

    MAX_UPLOAD_BYTES: int = 4 * 1024 * 1024 * 1024
    MAX_VIDEO_SECONDS: float = 600.0
    OUTPUT_RETAIN_DAYS: int = 7
    UPLOAD_RETAIN_HOURS: int = 48

    RENDER_TIMEOUT: int = 7200
    PREVIEW_TIMEOUT: int = 90
    GPU_MEMORY_FRACTION: float = 0.85

    CORS_ORIGINS: List[str] = ["http://localhost", "http://localhost:8000"]
    TRUSTED_HOSTS: List[str] = ["*"]

    SENTRY_DSN: Optional[str] = None
    FLOWER_USER: str = "admin"
    FLOWER_PASS: str = "flowerpass"

    @field_validator("STORAGE_ROOT", mode="before")
    @classmethod
    def make_dirs(cls, v):
        p = Path(v)
        for d in ["uploads", "renders", "temp", "weights", "previews"]:
            (p / d).mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sync_db_url(self) -> str:
        return self.DATABASE_URL.replace("+asyncpg", "")

    @property
    def r2_endpoint(self) -> Optional[str]:
        if self.R2_ACCOUNT_ID:
            return f"https://{self.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        return None

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    def plan_render_limit(self, plan: str) -> int:
        return {
            "free": 0, "starter": self.PLAN_STARTER_RENDERS,
            "pro": self.PLAN_PRO_RENDERS, "studio": self.PLAN_STUDIO_RENDERS,
        }.get(plan, 0)

    def stripe_price_id(self, plan: str) -> Optional[str]:
        return {
            "starter": self.STRIPE_PRICE_STARTER,
            "pro": self.STRIPE_PRICE_PRO,
            "studio": self.STRIPE_PRICE_STUDIO,
        }.get(plan)


settings = Settings()