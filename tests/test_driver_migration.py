"""Drivers jadvali migratsiyasi idempotent: ma'lumot o'chmaydi, dublikat yo'q."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from bot.database.models import Base


def _run_migrate(sync_conn) -> None:
    """models.init_db ichidagi profil migratsiyasining qisqartirilgan nusxasi."""
    from sqlalchemy import inspect

    driver_cols = {col["name"] for col in inspect(sync_conn).get_columns("drivers")}
    legacy_activation = "car_color" not in driver_cols
    for col_name, col_type in (
        ("car_color", "VARCHAR(64)"),
        ("seats_count", "INTEGER"),
        ("photo_file_id", "VARCHAR(256)"),
    ):
        if col_name not in driver_cols:
            sync_conn.execute(text(f"ALTER TABLE drivers ADD COLUMN {col_name} {col_type}"))
    if legacy_activation:
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
        sync_conn.execute(
            text(
                """
                UPDATE drivers
                SET status = 'active'
                WHERE LOWER(COALESCE(status, '')) IN ('trial', 'registered', 'ok')
                """
            )
        )


def test_migration_twice_preserves_drivers():
    engine = create_engine("sqlite:///:memory:")
    # Eski sxema (car_color yo'q)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE drivers (
                    telegram_id INTEGER PRIMARY KEY,
                    full_name VARCHAR(255) NOT NULL,
                    username VARCHAR(64),
                    car_model VARCHAR(128) NOT NULL,
                    car_number VARCHAR(32) NOT NULL,
                    phone VARCHAR(32) NOT NULL,
                    trial_start DATETIME,
                    subscription_until DATETIME,
                    status VARCHAR(32) NOT NULL,
                    notified_day5 BOOLEAN DEFAULT 0,
                    notified_day7 BOOLEAN DEFAULT 0,
                    notified_sub_10 BOOLEAN DEFAULT 0,
                    notified_sub_5 BOOLEAN DEFAULT 0,
                    notified_sub_1 BOOLEAN DEFAULT 0,
                    kicked_at DATETIME,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO drivers (
                    telegram_id, full_name, car_model, car_number, phone, status
                ) VALUES
                (1, 'Ali', 'Cobalt', '40 A 111 AA', '+998901111111', 'active'),
                (2, 'Vali', 'Nexia', '—', '+998902222222', 'active'),
                (3, 'Sami', 'Malibu', '50 B 333 BB', '+998903333333', 'expired'),
                (4, 'Blocked', 'Spark', '01 X 000 XX', '+998904444444', 'banned'),
                (5, 'TrialOld', 'Damas', '90 D 555 DD', '+998905555555', 'trial')
                """
            )
        )

    with engine.begin() as conn:
        _run_migrate(conn)

    with Session(engine) as sess:
        rows = {
            r[0]: r
            for r in sess.execute(
                text(
                    "SELECT telegram_id, full_name, phone, car_number, status, car_color "
                    "FROM drivers ORDER BY telegram_id"
                )
            )
        }
        assert len(rows) == 5
        assert rows[1][4] == "active"
        assert rows[1][2] == "+998901111111"
        assert rows[2][3] == "—"  # yetishmagan raqam saqlangan
        assert rows[3][4] == "expired"  # expired o'zgarmagan
        assert rows[4][4] == "banned"
        assert rows[5][4] == "active"  # trial → active
        assert rows[1][5] is None  # car_color yangi, ustiga yozilmagan

    # Ikkinchi migratsiya: pending qo'shiladi, ustiga yozilmasin
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO drivers (
                    telegram_id, full_name, car_model, car_number, phone, status
                ) VALUES (6, 'Yangi', 'Gentra', '01 C 777 CC', '+998906666666', 'pending')
                """
            )
        )
        _run_migrate(conn)

    with Session(engine) as sess:
        rows = {
            r[0]: r[1]
            for r in sess.execute(text("SELECT telegram_id, status FROM drivers"))
        }
        assert len(rows) == 6
        assert rows[1] == "active"
        assert rows[2] == "active"
        assert rows[3] == "expired"
        assert rows[4] == "banned"
        assert rows[5] == "active"
        assert rows[6] == "pending"  # ikkinchi ishga tushish pendingni buzmasin

        phone = sess.execute(
            text("SELECT phone FROM drivers WHERE telegram_id = 1")
        ).scalar_one()
        assert phone == "+998901111111"


def test_create_all_does_not_drop_data():
    """SQLAlchemy create_all mavjud jadvalni qayta yaratmasin."""
    from datetime import datetime, timezone

    from bot.database.models import Driver, DriverStatus

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        sess.add(
            Driver(
                telegram_id=9,
                full_name="Keep",
                car_model="Cobalt",
                car_number="40 A 999 AA",
                phone="+998909999999",
                status=DriverStatus.ACTIVE.value,
                trial_start=datetime.now(timezone.utc),
            )
        )
        sess.commit()
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        name = sess.execute(
            text("SELECT full_name FROM drivers WHERE telegram_id = 9")
        ).scalar_one()
        assert name == "Keep"
