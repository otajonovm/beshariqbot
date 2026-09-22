"""Pydantic Settings — .env orqali bot sozlamalari."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"

DEFAULT_GROUP_IDS = "-1003956911813,-1002296741378,-1003189462638,-1002200785842"
PRIMARY_GROUP_ID = -1003956911813
PRIMARY_GROUP_INVITE = "https://t.me/+nsAZI5HKm4E5OWYy"


class Settings(BaseSettings):
    """Loyiha konfiguratsiyasi. Tokenlar .env faylidan olinadi."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8-sig",
        extra="ignore",
        case_sensitive=False,
    )

    # false = Telegram polling/xabarlar o'chirilgan (bot uzilgan)
    bot_enabled: bool = Field(default=False, description="Telegram botni yoqish/o'chirish")
    bot_token: str = Field(
        default="",
        description="Telegram Bot API token (bot_enabled=true bo'lsa majburiy)",
    )
    supergroup_ids: str = Field(
        default=DEFAULT_GROUP_IDS,
        description="Haydovchilar guruhlari, vergul bilan",
    )
    primary_group_id: int = Field(default=PRIMARY_GROUP_ID)
    primary_group_invite: str = Field(default=PRIMARY_GROUP_INVITE)
    primary_group_title: str = Field(default="426. Global")
    bot_username: str = Field(default="beshariq_toshkent_taxi_uzbot")
    admin_id: int = Field(default=0, description="Super-admin Telegram user ID")
    currency: str = Field(default="UZS")
    # Heroku Postgres: heroku addons:create heroku-postgresql
    # Agar bo'sh bo'lsa — lokal SQLite (faqat development uchun)
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    @field_validator("bot_token", mode="before")
    @classmethod
    def strip_token(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("bot_enabled", mode="before")
    @classmethod
    def parse_enabled(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value

    @field_validator("supergroup_ids", mode="before")
    @classmethod
    def strip_ids(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return value
        url = value.strip()
        # Heroku beradi: postgres://... — SQLAlchemy 2 async uchun asyncpg kerak
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        # asyncpg libpq'ning sslmode parametrini tushunmaydi — alohida beriladi
        if "sslmode=" in url:
            from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

            parsed = urlparse(url)
            query = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() != "sslmode"]
            url = urlunparse(parsed._replace(query=urlencode(query)))
        return url

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, dotenv_settings, env_settings, file_secret_settings)

    @property
    def group_ids(self) -> list[int]:
        ids: list[int] = []
        for part in self.supergroup_ids.split(","):
            part = part.strip()
            if not part:
                continue
            value = int(part)
            if value not in ids:
                ids.append(value)
        primary = self.primary_group_id
        if primary in ids:
            ids = [primary] + [item for item in ids if item != primary]
        elif primary:
            ids.insert(0, primary)
        return ids

    def resolved_database_url(self) -> str:
        """Heroku/Postgres DATABASE_URL yoki lokal SQLite."""
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = (DATA_DIR / "bot.db").resolve().as_posix()
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url().startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
