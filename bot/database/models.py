"""SQLAlchemy 2.0 async modellari: User, Driver, Order."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
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

TZ_UTC = timezone.utc


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
    ACTIVE = "active"
    EXPIRED = "expired"
    BANNED = "banned"


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
    trial_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    subscription_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default=DriverStatus.ACTIVE.value)
    notified_day5: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_day7: Mapped[bool] = mapped_column(Boolean, default=False)
    kicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )

    orders: Mapped[list[Order]] = relationship(
        primaryjoin="Driver.telegram_id == foreign(Order.driver_id)",
        back_populates="driver",
    )

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

    def is_profile_complete(self) -> bool:
        """Mashina va telefon to'ldirilganmi (guruh claim stub emas)."""
        return (
            len((self.full_name or "").strip()) >= 3
            and (self.car_model or "").strip() not in _PROFILE_PLACEHOLDERS
            and (self.car_number or "").strip() not in _PROFILE_PLACEHOLDERS
            and (self.phone or "").strip() not in _PROFILE_PLACEHOLDERS
        )

    def is_access_valid(self, now: datetime | None = None) -> bool:
        """Buyurtma olish huquqi: bloklanmagan; muddati o'tgan to'langan obuna bo'lmasin."""
        now = now or utcnow()
        if self.status == DriverStatus.BANNED.value:
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
            f"Mijoz bilan bog'laning. Natijani pastdagi tugmalar orqali belgilang."
        )


_db_url = settings.resolved_database_url()
_engine_kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
if not settings.is_sqlite:
    # Heroku Postgres: idle connectionlarni qayta ishlatish + SSL majburiy
    _engine_kwargs.update(
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        connect_args={"ssl": True},
    )

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

        def _migrate_orders(sync_conn) -> None:  # type: ignore[no-untyped-def]
            columns = [col["name"] for col in inspect(sync_conn).get_columns("orders")]
            if "group_posts" not in columns:
                sync_conn.execute(text("ALTER TABLE orders ADD COLUMN group_posts JSON"))

            # Eski SQLite: orders.driver_id → drivers FK ni olib tashlash
            # (ro'yxatdan o'tmagan haydovchi ham zakas olishi uchun)
            dialect = sync_conn.dialect.name
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
                        rejected_drivers, last_cancel_reason, created_at, updated_at
                    )
                    SELECT
                        id, order_type, status, passenger_id, driver_id, direction,
                        area, area_custom, seats, departure_time, cargo_description,
                        photo_file_id, phone, group_message_id, group_posts,
                        rejected_drivers, last_cancel_reason, created_at, updated_at
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

        await conn.run_sync(_migrate_orders)


async def dispose_db() -> None:
    await engine.dispose()
