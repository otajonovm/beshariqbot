"""Pydantic Settings — .env orqali bot sozlamalari."""

import hashlib
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"

DEFAULT_GROUP_IDS = (
    "-1003956911813,-1004421198641,-1002296741378,-1003189462638,-1002200785842"
)
PRIMARY_GROUP_ID = -1003956911813
PRIMARY_GROUP_INVITE = "https://t.me/+nsAZI5HKm4E5OWYy"
# Bo'sh = SUPERGROUP_IDS dan 426. Global (PRIMARY) ni chiqarib tashlash.
# 426 — faqat yo'lovchi/taksi hub; haydovchi e'loni u yerga tushmaydi.
DEFAULT_DRIVER_POST_GROUP_IDS = ""


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
    bot_username: str = Field(default="beshariq_toshkent_taxi_uzbot")
    admin_id: int = Field(..., description="Asosiy super-admin Telegram user ID")
    # Qo'shimcha adminlar (vergul bilan). 5265031638 — to'liq huquqli admin.
    admin_ids_extra: str = Field(
        default="5265031638",
        validation_alias="ADMIN_IDS",
        description="Qo'shimcha admin Telegram ID lari",
    )
    currency: str = Field(default="UZS")
    # Yo'lovchi xabarini asosiy guruhga ko'chirish.
    # MAIN_GROUP_ID bo'sh bo'lsa PRIMARY_GROUP_ID (426. Global) ishlatiladi.
    main_group_id: int | None = Field(default=None)
    # Bo'sh bo'lsa — bot turgan barcha guruhlar (asosiy guruhdan tashqari).
    source_group_ids: str = Field(default="")
    # Admin bo'lmaganlarning xabari o'chirilib asosiy guruhga o'tkaziladi.
    sweep_group_ids: str = Field(default="-1003189462638")
    # Haydovchi e'lonlari (xom + wizard). Bo'sh = barcha SUPERGROUP_IDS minus 426. Global.
    driver_post_group_ids: str = Field(default=DEFAULT_DRIVER_POST_GROUP_IDS)
    # Lichkadagi matn/forward uchun oldindan ko'rish (✅/❌).
    driver_raw_confirm: bool = Field(default=True)
    # Soatiga nechta xom/wizard e'lon (spam limati).
    driver_post_limit_per_hour: int = Field(default=20)
    dedup_minutes: int = Field(default=10)
    relay_min_score: int = Field(default=3)
    relay_max_chars: int = Field(default=400)
    relay_rate_per_minute: int = Field(default=18)
    # auto: Railway'da webhook, lokalda polling. 0 — majburiy polling. 1 — webhook.
    use_webhook: str = Field(default="auto")
    webhook_url: str = Field(default="")
    webhook_secret: str = Field(default="")
    # Railway Postgres DATABASE_URL. Bo'sh bo'lsa — lokal SQLite.
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    @field_validator("bot_token", mode="before")
    @classmethod
    def strip_token(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "supergroup_ids",
        "source_group_ids",
        "sweep_group_ids",
        "driver_post_group_ids",
        "use_webhook",
        "webhook_url",
        mode="before",
    )
    @classmethod
    def strip_ids(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("main_group_id", mode="before")
    @classmethod
    def empty_main_group(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return value
        url = value.strip()
        # postgres://... — SQLAlchemy 2 async uchun asyncpg kerak
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

    @staticmethod
    def _normalize_chat_id(raw: str) -> int | None:
        """4421198641 → -1004421198641 (superguruh)."""
        part = raw.strip()
        if not part:
            return None
        try:
            value = int(part)
        except ValueError:
            return None
        # Musbat yoki qisqa ID — Telegram superguruh shakliga keltirish
        if value > 0:
            value = int(f"-100{value}")
        elif value > -1000000000000 and not str(value).startswith("-100"):
            # masalan -4421198641
            value = int(f"-100{abs(value)}")
        return value

    @property
    def resolved_driver_post_group_ids(self) -> list[int]:
        """Haydovchi e'lonlari: ulangan guruhlar, 426. Global (PRIMARY) dan tashqari.

        Bo'sh DRIVER_POST_GROUP_IDS → SUPERGROUP_IDS / group_ids minus primary.
        Aniq ro'yxat berilsa — shu; primary baribir chiqariladi.
        """
        primary = self.primary_group_id
        raw = (self.driver_post_group_ids or "").strip()
        if not raw or raw == "*":
            return [cid for cid in self.group_ids if cid != primary]
        ids: list[int] = []
        for part in raw.split(","):
            value = self._normalize_chat_id(part)
            if value is None or value in ids or value == primary:
                continue
            ids.append(value)
        return ids

    @property
    def admin_ids(self) -> list[int]:
        """Barcha adminlar: asosiy + ADMIN_IDS (to'liq huquq)."""
        ids: list[int] = [self.admin_id]
        for part in self.admin_ids_extra.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value not in ids:
                ids.append(value)
        return ids

    def is_admin(self, user_id: int | None) -> bool:
        return bool(user_id and user_id in self.admin_ids)

    def resolved_database_url(self) -> str:
        """Postgres DATABASE_URL yoki lokal SQLite."""
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db_path = (DATA_DIR / "bot.db").resolve().as_posix()
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url().startswith("sqlite")

    @property
    def is_railway(self) -> bool:
        """Railway muhiti (filesystem ephemeral)."""
        return bool(
            os.environ.get("RAILWAY_ENVIRONMENT")
            or os.environ.get("RAILWAY_PROJECT_ID")
            or os.environ.get("RAILWAY_SERVICE_ID")
        )

    @property
    def resolved_main_group_id(self) -> int:
        """Yo'naltirish manzili. MAIN_GROUP_ID yo'q bo'lsa 426. Global (PRIMARY_GROUP_ID)."""
        if self.main_group_id:
            return int(self.main_group_id)
        return self.primary_group_id

    @property
    def resolved_source_group_ids(self) -> list[int] | None:
        """None — asosiy guruhdan boshqa hamma guruh. Bo'sh ro'yxat — hech qayerdan emas."""
        raw = (self.source_group_ids or "").strip()
        if not raw or raw == "*":
            return None
        main = self.resolved_main_group_id
        ids: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value != main and value not in ids:
                ids.append(value)
        return ids

    @property
    def resolved_sweep_group_ids(self) -> set[int]:
        """Admin emas yozsa → o'chirib asosiy guruhga o'tkaziladigan guruhlar."""
        main = self.resolved_main_group_id
        ids: set[int] = set()
        for part in (self.sweep_group_ids or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value != main:
                ids.add(value)
        return ids

    def webhook_base(self) -> str | None:
        """Webhook ildiz URL. Polling kerak bo'lsa None."""
        mode = (self.use_webhook or "auto").strip().lower()
        if mode in {"0", "false", "off", "no", "polling"}:
            return None
        url = (self.webhook_url or "").strip().rstrip("/")
        if url:
            return url
        if mode in {"1", "true", "on", "yes", "webhook"} or (mode == "auto" and self.is_railway):
            domain = (
                os.environ.get("RAILWAY_PUBLIC_DOMAIN")
                or os.environ.get("RAILWAY_STATIC_URL")
                or ""
            ).strip().rstrip("/")
            if domain:
                if domain.startswith(("http://", "https://")):
                    return domain
                return f"https://{domain}"
        return None

    def resolved_webhook_secret(self) -> str:
        raw = (self.webhook_secret or "").strip()
        if raw:
            return raw
        digest = hashlib.sha256(f"beshariq-relay:{self.bot_token}".encode()).hexdigest()
        return digest[:32]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
