"""SQLAlchemy 2.0 async modellari: User, Driver, Order."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bot.config import settings

TZ_UTC = UTC


def utcnow() -> datetime:
    return datetime.now(TZ_UTC)


class OrderType(str, Enum):
    TAXI = "taxi"
    PARCEL = "parcel"


class OrderStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class DriverStatus(str, Enum):
    PENDING = "pending"  # yangi ariza — admin tasdig'i kutilyapti
    ACTIVE = "active"
    EXPIRED = "expired"
    BANNED = "banned"
    REJECTED = "rejected"
    DELETED = "deleted"  # soft delete — ma'lumot bazada qoladi


# Majburiy / ixtiyoriy profil maydonlari (admin /drivers uchun)
REQUIRED_DRIVER_FIELDS: tuple[str, ...] = ("phone", "car_model", "car_number")
OPTIONAL_DRIVER_FIELDS: tuple[str, ...] = ("car_color", "seats_count", "photo_file_id")
DRIVER_FIELD_LABELS: dict[str, str] = {
    "phone": "telefon",
    "car_model": "mashina rusumi",
    "car_number": "davlat raqami",
    "car_color": "rang",
    "seats_count": "o'rinlar soni",
    "photo_file_id": "rasm",
}


class Direction(str, Enum):
    BESHARIQ_TASHKENT = "beshariq_tashkent"
    TASHKENT_BESHARIQ = "tashkent_beshariq"


DIRECTION_LABELS: dict[str, tuple[str, str]] = {
    Direction.BESHARIQ_TASHKENT.value: ("Beshariq", "Toshkent"),
    Direction.TASHKENT_BESHARIQ.value: ("Toshkent", "Beshariq"),
}

AREA_LABELS: dict[str, str] = {
    "markaz": "Markaz",
    "rapqon": "Rapqon",
    "vatan": "Vatan",
    "qaqir": "Qaqir",
    "yakkatut": "Yakkatut",
    "other": "Boshqa qishloq",
}

CANCEL_REASON_LABELS: dict[str, str] = {
    "price": "💸 Narx to'g'ri kelmadi",
    "time": "⏰ Vaqt to'g'ri kelmadi",
    "phone": "📵 Telefon ko'tarmadi",
}

_PROFILE_PLACEHOLDERS = {"", "—", "-", "n/a", "N/A"}


class Base(DeclarativeBase):
    pass


class User(Base):
    """Mijoz (yo'lovchi) profili."""

    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    orders: Mapped[list[Order]] = relationship(
        back_populates="passenger",
        foreign_keys="Order.passenger_id",
    )


class Driver(Base):
    """Haydovchi profili va (ixtiyoriy) oylik obuna."""

    __tablename__ = "drivers"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    car_model: Mapped[str] = mapped_column(String(128))
    car_number: Mapped[str] = mapped_column(String(32))
    phone: Mapped[str] = mapped_column(String(32))
    # Ixtiyoriy profil maydonlari (migratsiya orqali qo'shiladi)
    car_color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seats_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Oxirgi tanlangan ketish vaqti matni (e'lon shabloni)
    last_departure_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Ixtiyoriy default yo'nalish: beshariq_tashkent | tashkent_beshariq
    preferred_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Oxirgi e'lon payload (restart uchun): text, photo, departure, seats, direction
    last_announce: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trial_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    subscription_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default=DriverStatus.ACTIVE.value)
    notified_day5: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_day7: Mapped[bool] = mapped_column(Boolean, default=False)
    # Obuna tugashiga 10 / 5 / 1 kun qolganda eslatma
    notified_sub_10: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_sub_5: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_sub_1: Mapped[bool] = mapped_column(Boolean, default=False)
    kicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft delete + to'lov / premium (migratsiya orqali)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monthly_fee: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_pay_3: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_pay_1: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_pay_0: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Premium funksiya override: {flag_key: bool}
    feature_overrides: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notified_premium_end: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    orders: Mapped[list[Order]] = relationship(
        primaryjoin="Driver.telegram_id == foreign(Order.driver_id)",
        back_populates="driver",
    )

    @staticmethod
    def _blank(value: object | None) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() in _PROFILE_PLACEHOLDERS
        return False

    def _aware(self, dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_UTC)
        return dt

    def has_paid_subscription(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        until = self._aware(self.subscription_until)
        return until is not None and until > now

    def missing_required_fields(self) -> list[str]:
        """Majburiy maydonlar: telefon, rusum, davlat raqami."""
        missing: list[str] = []
        if self._blank(self.phone):
            missing.append("phone")
        if self._blank(self.car_model):
            missing.append("car_model")
        if self._blank(self.car_number):
            missing.append("car_number")
        return missing

    def missing_optional_fields(self) -> list[str]:
        missing: list[str] = []
        if self._blank(self.car_color):
            missing.append("car_color")
        if self.seats_count is None or self.seats_count <= 0:
            missing.append("seats_count")
        if self._blank(self.photo_file_id):
            missing.append("photo_file_id")
        return missing

    def missing_field_labels(self) -> list[str]:
        keys = self.missing_required_fields() + self.missing_optional_fields()
        return [DRIVER_FIELD_LABELS.get(k, k) for k in keys]

    def is_profile_complete(self) -> bool:
        """Majburiy maydonlar to'ldirilganmi (guruh claim stub emas)."""
        return (
            len((self.full_name or "").strip()) >= 3
            and not self.missing_required_fields()
        )

    def is_soft_deleted(self) -> bool:
        return self.status == DriverStatus.DELETED.value or self.deleted_at is not None

    def is_legacy_or_approved(self) -> bool:
        """Mavjud/tasdiqlangan haydovchi — qayta ariza kerak emas."""
        if self.is_soft_deleted():
            return False
        return self.status in {
            DriverStatus.ACTIVE.value,
            DriverStatus.EXPIRED.value,
        }

    def has_active_premium(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        until = self._aware(self.premium_until)
        return until is not None and until > now

    def payment_status(self, now: datetime | None = None) -> str:
        """to'langan | kutilmoqda | kechikkan | bepul | o'chiq."""
        if self.payment_exempt:
            return "bepul"
        due = self._aware(self.payment_due_at)
        if due is None:
            return "o'chiq"
        now = now or utcnow()
        if due.date() > now.date():
            return "to'langan"
        if due.date() == now.date():
            return "kutilmoqda"
        return "kechikkan"

    def subscription_days_left(self, now: datetime | None = None) -> int | None:
        """Obuna tugashiga qolgan to'liq kunlar; obuna yo'q bo'lsa None."""
        now = now or utcnow()
        until = self._aware(self.subscription_until)
        if until is None or until <= now:
            return None
        return max(0, (until - now).days)

    def is_access_valid(self, now: datetime | None = None) -> bool:
        """Buyurtma olish huquqi: bloklanmagan; muddati o'tgan to'langan obuna bo'lmasin."""
        now = now or utcnow()
        if self.is_soft_deleted():
            return False
        if self.status in {
            DriverStatus.BANNED.value,
            DriverStatus.PENDING.value,
            DriverStatus.REJECTED.value,
        }:
            return False
        if self.has_paid_subscription(now):
            return True
        # Avval to'langan obuna muddati tugagan
        until = self._aware(self.subscription_until)
        if until is not None and until <= now:
            return False
        # Sinov yo'q: aktiv yoki eski trial-expired (obunasiz) — ruxsat
        return self.status in {
            DriverStatus.ACTIVE.value,
            DriverStatus.EXPIRED.value,
        }


class AutoPostStatus(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"
    FINISHED = "finished"


class AutoPostSession(Base):
    """Haydovchi avto-e'lon sessiyasi (restartga chidamli)."""

    __tablename__ = "auto_post_sessions"
    __table_args__ = (Index("ix_auto_post_status_ends", "status", "ends_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    driver_id: Mapped[int] = mapped_column(BigInteger, index=True)
    interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default=AutoPostStatus.ACTIVE.value, index=True
    )
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # E'lon payload
    text: Mapped[str] = mapped_column(Text, default="")
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    departure_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seats_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # chat_id (str) -> message_id
    message_ids: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rate_notified: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    """Taksi yoki pochta buyurtmasi."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_status", "status"),
        Index("ix_orders_type_status", "order_type", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.PENDING.value)
    passenger_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), index=True
    )
    driver_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    direction: Mapped[str] = mapped_column(String(32))
    area: Mapped[str | None] = mapped_column(String(32), nullable=True)
    area_custom: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seats: Mapped[str | None] = mapped_column(String(32), nullable=True)
    departure_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cargo_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str] = mapped_column(String(32))
    group_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_posts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rejected_drivers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_cancel_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # order | ad | book_... — qayerdan kelgani
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )

    passenger: Mapped[User] = relationship(foreign_keys=[passenger_id], back_populates="orders")
    driver: Mapped[Driver | None] = relationship(
        primaryjoin="foreign(Order.driver_id) == Driver.telegram_id",
        back_populates="orders",
    )

    def area_display(self) -> str:
        if self.area == "other" and self.area_custom:
            return self.area_custom
        if self.area:
            return AREA_LABELS.get(self.area, self.area)
        return ""

    def direction_display(self) -> str:
        origin, dest = DIRECTION_LABELS.get(self.direction, ("Beshariq", "Toshkent"))
        area = self.area_display()
        if self.order_type == OrderType.TAXI.value and area:
            if self.direction == Direction.BESHARIQ_TASHKENT.value:
                return f"Beshariq ({area}) ➡️ Toshkent"
            return f"Toshkent ➡️ Beshariq ({area})"
        return f"{origin} ➡️ {dest}"

    def detail_display(self) -> str:
        if self.order_type == OrderType.PARCEL.value:
            return self.cargo_description or "Pochta"
        return self.seats or "—"

    def icon(self) -> str:
        return "📦" if self.order_type == OrderType.PARCEL.value else "🚖"

    def detail_icon(self) -> str:
        return "ℹ️" if self.order_type == OrderType.PARCEL.value else "👥"

    def rejected_id_list(self) -> list[int]:
        raw = self.rejected_drivers or []
        result: list[int] = []
        for item in raw:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result

    def add_rejected_driver(self, driver_id: int) -> None:
        current = self.rejected_id_list()
        if driver_id not in current:
            current.append(driver_id)
        self.rejected_drivers = current

    def posts_map(self) -> dict[int, int]:
        raw = self.group_posts or {}
        result: dict[int, int] = {}
        for key, value in raw.items():
            try:
                result[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    def set_posts_map(self, posts: dict[int, int]) -> None:
        self.group_posts = {str(chat_id): message_id for chat_id, message_id in posts.items()}

    def to_group_text(self, *, reactivated: bool = False) -> str:
        header = (
            f"⚠️ <b>BUYURTMA QAYTA FAOLLASHDI!</b> (#{self.id})"
            if reactivated
            else f"{self.icon()} <b>Yangi buyurtma! (#{self.id})</b>"
        )
        return (
            f"{header}\n"
            f"📍 <b>Yo'nalish:</b> {self.direction_display()}\n"
            f"{self.detail_icon()} <b>Tafsilot:</b> {self.detail_display()}\n"
            f"⚠️ <i>Aloqaga chiqish uchun buyurtmani qabul qiling.</i>"
        )

    def to_claimed_group_text(self, driver_name: str) -> str:
        return (
            f"✅ Buyurtmani <b>{driver_name}</b> qabul qildi (#{self.id})\n"
            f"📍 {self.direction_display()}\n"
            f"{self.detail_icon()} {self.detail_display()}"
        )

    def to_confirmed_group_text(self, driver_name: str) -> str:
        return (
            f"✅ <b>Safar tasdiqlandi</b> (#{self.id})\n"
            f"🚘 Haydovchi: {driver_name}\n"
            f"📍 {self.direction_display()}"
        )

    def to_driver_private_text(self, passenger: User | None = None) -> str:
        passenger_line = ""
        if passenger:
            uname = f" @{passenger.username}" if passenger.username else ""
            passenger_line = f"\n👤 <b>Mijoz:</b> {passenger.full_name}{uname}"
        return (
            f"{self.icon()} <b>Buyurtma sizniki! (#{self.id})</b>\n\n"
            f"📍 <b>Yo'nalish:</b> {self.direction_display()}\n"
            f"{self.detail_icon()} <b>Tafsilot:</b> {self.detail_display()}\n"
            f"📞 <b>Telefon:</b> <code>{self.phone}</code>"
            f"{passenger_line}\n\n"
            f"⚠️ <b>Diqqat:</b> Mijoz bilan bog'lanib, <b>3 daqiqa</b> ichida pastdagi "
            f"<b>«✅ Ha, oldim»</b> tugmasini bosing!\n"
            f"Aks holda buyurtma avtomatik tarzda bekor qilinib, qaytadan guruhga tashlanadi."
        )


class FeatureFlag(Base):
    """Super-admin funksiya yoqish/o'chirish (default: yoqilgan)."""

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppSetting(Base):
    """DB sozlamalari (kesh bilan). value — JSON matn."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BotGroup(Base):
    """Bot ulangan guruh va uning flaglari."""

    __tablename__ = "bot_groups"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    can_post: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    can_delete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Yo'lovchi so'rovlarini asosiy guruhga olib o'tish
    flag_relay_source: Mapped[bool] = mapped_column(Boolean, default=True)
    # Haydovchi e'lonlari
    flag_driver_posts: Mapped[bool] = mapped_column(Boolean, default=True)
    # Avto-e'lon
    flag_auto_posts: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bot reklamasi
    flag_bot_ads: Mapped[bool] = mapped_column(Boolean, default=False)
    # Asl xabarni o'chirish (sweep)
    flag_delete_original: Mapped[bool] = mapped_column(Boolean, default=False)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    # Admin "Yoqasizmi?" tasdig'i
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ad_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PaymentRecord(Base):
    """Haydovchi to'lov tarixi."""

    __tablename__ = "payment_records"
    __table_args__ = (Index("ix_payment_driver_created", "driver_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    driver_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    days: Mapped[int] = mapped_column(Integer, default=30)
    accepted_by: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class AdminActionLog(Base):
    """Admin amallari jurnali."""

    __tablename__ = "admin_action_logs"
    __table_args__ = (Index("ix_admin_log_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class RelayHit(Base):
    """Manba guruhdan asosiy guruhga yuborilgan yo'lovchi xabari."""

    __tablename__ = "relay_hits"
    __table_args__ = (
        UniqueConstraint("source_chat_id", "source_message_id", name="uq_relay_src_msg"),
        Index("ix_relay_hits_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    source_message_id: Mapped[int] = mapped_column(Integer)
    source_title: Mapped[str] = mapped_column(String(255), default="")
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    norm_text: Mapped[str] = mapped_column(String(420), default="")
    handle_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class RelayKeyword(Base):
    """Admin qo'shgan yoki o'chirgan qo'shimcha kalit so'z.

    kind: passenger | driver | off
    """

    __tablename__ = "relay_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    phrase: Mapped[str] = mapped_column(String(160), unique=True)
    weight: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


_db_url = settings.resolved_database_url()
_engine_kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
if not settings.is_sqlite:
    # Postgres pool sozlamalari
    _engine_kwargs.update(
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )
    # Railway internal tarmoqda (postgres.railway.internal) yoki localhost'da SSL shart emas.
    # Boshqa holatlarda (tashqi Postgres, proxy) self-signed sertifikat xatolik bermasligi uchun SSL kontekst moslanadi.
    if "railway.internal" in _db_url or "localhost" in _db_url or "127.0.0.1" in _db_url:
        _engine_kwargs["connect_args"] = {"ssl": False}
    else:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _engine_kwargs["connect_args"] = {"ssl": ctx}

engine: AsyncEngine = create_async_engine(_db_url, **_engine_kwargs)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


if settings.is_sqlite:
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragma)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _migrate_schema(sync_conn) -> None:  # type: ignore[no-untyped-def]
            # Drivers: obuna eslatma ustunlari
            driver_cols = {col["name"] for col in inspect(sync_conn).get_columns("drivers")}
            dialect = sync_conn.dialect.name
            bool_default = "FALSE" if dialect == "postgresql" else "0"
            ts_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
            for col_name in ("notified_sub_10", "notified_sub_5", "notified_sub_1"):
                if col_name not in driver_cols:
                    sync_conn.execute(
                        text(
                            f"ALTER TABLE drivers ADD COLUMN {col_name} "
                            f"BOOLEAN DEFAULT {bool_default}"
                        )
                    )

            # Profil kengaytmasi + bir martalik legacy tasdiqlash.
            # car_color birinchi marta qo'shilganda: mavjud haydovchilar
            # pending/rejected bo'lmasin — active (yoki expired saqlanadi).
            # Qayta ishga tushishda ustun bor → statusga tegilmaydi (idempotent).
            legacy_activation = "car_color" not in driver_cols
            profile_columns: list[tuple[str, str]] = [
                ("car_color", "VARCHAR(64)"),
                ("seats_count", "INTEGER"),
                ("photo_file_id", "VARCHAR(256)"),
                ("last_departure_text", "VARCHAR(120)"),
                ("preferred_direction", "VARCHAR(32)"),
                ("last_announce", "JSON" if dialect == "postgresql" else "TEXT"),
            ]
            for col_name, col_type in profile_columns:
                if col_name not in driver_cols:
                    sync_conn.execute(
                        text(f"ALTER TABLE drivers ADD COLUMN {col_name} {col_type}")
                    )

            # To'lov / premium / soft-delete (idempotent).
            # payment_due_at birinchi marta qo'shilganda: mavjud haydovchilar
            # darhol bloklanmasin — PAYMENT_START_DAYS (default 7) kun keyin.
            payment_bootstrap = "payment_due_at" not in driver_cols
            admin_columns: list[tuple[str, str]] = [
                ("deleted_at", ts_type),
                ("block_reason", "VARCHAR(32)"),
                ("payment_due_at", ts_type),
                ("monthly_fee", "INTEGER"),
                ("payment_exempt", f"BOOLEAN DEFAULT {bool_default}"),
                ("notified_pay_3", f"BOOLEAN DEFAULT {bool_default}"),
                ("notified_pay_1", f"BOOLEAN DEFAULT {bool_default}"),
                ("notified_pay_0", f"BOOLEAN DEFAULT {bool_default}"),
                ("premium_until", ts_type),
                (
                    "feature_overrides",
                    "JSON" if dialect == "postgresql" else "TEXT",
                ),
                ("notified_premium_end", f"BOOLEAN DEFAULT {bool_default}"),
            ]
            for col_name, col_type in admin_columns:
                if col_name not in driver_cols:
                    sync_conn.execute(
                        text(f"ALTER TABLE drivers ADD COLUMN {col_name} {col_type}")
                    )
            if payment_bootstrap:
                from bot.config import settings as _cfg

                start_days = max(1, int(getattr(_cfg, "payment_start_days", 7) or 7))
                if dialect == "postgresql":
                    sync_conn.execute(
                        text(
                            f"""
                            UPDATE drivers
                            SET payment_due_at = NOW() + INTERVAL '{start_days} days'
                            WHERE payment_due_at IS NULL
                              AND LOWER(COALESCE(status, '')) NOT IN ('deleted')
                            """
                        )
                    )
                else:
                    sync_conn.execute(
                        text(
                            f"""
                            UPDATE drivers
                            SET payment_due_at = datetime('now', '+{start_days} days')
                            WHERE payment_due_at IS NULL
                              AND LOWER(COALESCE(status, '')) NOT IN ('deleted')
                            """
                        )
                    )
            if legacy_activation:
                # Faqat "noma'lum" statuslarni active qiladi.
                # banned/expired/active/pending/rejected ga tegilmaydi —
                # deploy paytida pending hali yo'q; keyingi ishga tushishda
                # legacy_activation=False bo'lgani uchun yangi arizalar saqlanadi.
                sync_conn.execute(
                    text(
                        """
                        UPDATE drivers
                        SET status = 'active'
                        WHERE status IS NULL
                           OR TRIM(status) = ''
                           OR LOWER(status) NOT IN (
                                'active', 'expired', 'banned', 'pending', 'rejected'
                           )
                        """
                    )
                )
                # Mavjud qatorlar (banned emas) — tasdiqlangan hisoblanadi.
                # pending/rejected deploydan KEYIN paydo bo'ladi; shu birinchi
                # migratsiyada ular yo'q. Shuning uchun non-banned → kamida active
                # emas, balki faqat yuqoridagi "noma'lum" tozalanadi.
                # Qo'shimcha: agar status 'trial' kabi eski qiymat bo'lsa active.
                sync_conn.execute(
                    text(
                        """
                        UPDATE drivers
                        SET status = 'active'
                        WHERE LOWER(COALESCE(status, '')) IN ('trial', 'registered', 'ok')
                        """
                    )
                )

            order_cols = {col["name"] for col in inspect(sync_conn).get_columns("orders")}
            if "group_posts" not in order_cols:
                sync_conn.execute(text("ALTER TABLE orders ADD COLUMN group_posts JSON"))
            if "claim_message_id" not in order_cols:
                sync_conn.execute(text("ALTER TABLE orders ADD COLUMN claim_message_id INTEGER"))
            if "claimed_at" not in order_cols:
                col_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
                sync_conn.execute(text(f"ALTER TABLE orders ADD COLUMN claimed_at {col_type}"))
            if "source" not in order_cols:
                sync_conn.execute(text("ALTER TABLE orders ADD COLUMN source VARCHAR(32)"))

            # Eski SQLite: orders.driver_id → drivers FK ni olib tashlash
            if dialect != "sqlite":
                return
            fks = sync_conn.execute(text("PRAGMA foreign_key_list(orders)")).fetchall()
            has_driver_fk = any(row[2] == "drivers" for row in fks)
            if not has_driver_fk:
                return
            sync_conn.execute(text("PRAGMA foreign_keys=OFF"))
            sync_conn.execute(
                text(
                    """
                    CREATE TABLE orders_new (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        order_type VARCHAR(16) NOT NULL,
                        status VARCHAR(16) NOT NULL,
                        passenger_id BIGINT NOT NULL,
                        driver_id BIGINT,
                        direction VARCHAR(32) NOT NULL,
                        area VARCHAR(32),
                        area_custom VARCHAR(128),
                        seats VARCHAR(32),
                        departure_time VARCHAR(64),
                        cargo_description TEXT,
                        photo_file_id VARCHAR(256),
                        phone VARCHAR(32) NOT NULL,
                        group_message_id INTEGER,
                        group_posts JSON,
                        rejected_drivers JSON NOT NULL,
                        last_cancel_reason VARCHAR(64),
                        claim_message_id INTEGER,
                        claimed_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        FOREIGN KEY(passenger_id) REFERENCES users (telegram_id)
                    )
                    """
                )
            )
            sync_conn.execute(
                text(
                    """
                    INSERT INTO orders_new (
                        id, order_type, status, passenger_id, driver_id, direction,
                        area, area_custom, seats, departure_time, cargo_description,
                        photo_file_id, phone, group_message_id, group_posts,
                        rejected_drivers, last_cancel_reason, claim_message_id,
                        claimed_at, created_at, updated_at
                    )
                    SELECT
                        id, order_type, status, passenger_id, driver_id, direction,
                        area, area_custom, seats, departure_time, cargo_description,
                        photo_file_id, phone, group_message_id, group_posts,
                        rejected_drivers, last_cancel_reason, claim_message_id,
                        claimed_at, created_at, updated_at
                    FROM orders
                    """
                )
            )
            sync_conn.execute(text("DROP TABLE orders"))
            sync_conn.execute(text("ALTER TABLE orders_new RENAME TO orders"))
            sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status)"))
            sync_conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_orders_type_status "
                    "ON orders (order_type, status)"
                )
            )
            sync_conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_orders_passenger_id ON orders (passenger_id)")
            )
            sync_conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_orders_driver_id ON orders (driver_id)")
            )
            sync_conn.execute(text("PRAGMA foreign_keys=ON"))

        await conn.run_sync(_migrate_schema)


async def dispose_db() -> None:
    await engine.dispose()
