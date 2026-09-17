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

    bot_token: str = Field(..., min_length=20, description="Telegram Bot API token")
    supergroup_ids: str = Field(
        default=DEFAULT_GROUP_IDS,
        description="Haydovchilar guruhlari, vergul bilan",
    )
    primary_group_id: int = Field(default=PRIMARY_GROUP_ID)
    primary_group_invite: str = Field(default=PRIMARY_GROUP_INVITE)
    primary_group_title: str = Field(default="426. Global")
    admin_id: int = Field(..., description="Super-admin Telegram user ID")
    currency: str = Field(default="UZS")

    @field_validator("bot_token", mode="before")
    @classmethod
    def strip_token(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("supergroup_ids", mode="before")
    @classmethod
    def strip_ids(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

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

    @property
    def database_url(self) -> str:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = (DATA_DIR / "bot.db").resolve().as_posix()
        return f"sqlite+aiosqlite:///{db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
