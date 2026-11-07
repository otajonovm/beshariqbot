"""Mavjud haydovchilar qayta ro'yxatdan o'tmasligi va yetishmagan maydonlar."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.database.db_requests import (
    submit_driver_application,
    update_driver_fields,
)
from bot.database.models import Base, Driver, DriverStatus


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


def test_existing_driver_missing_only_plate():
    driver = Driver(
        telegram_id=101,
        full_name="Ali Valiyev",
        car_model="Cobalt",
        car_number="—",
        phone="+998901112233",
        status=DriverStatus.ACTIVE.value,
    )
    assert driver.is_legacy_or_approved()
    assert driver.missing_required_fields() == ["car_number"]
    assert not driver.is_profile_complete()


def test_complete_driver_no_missing_required():
    driver = Driver(
        telegram_id=102,
        full_name="Ali Valiyev",
        car_model="Cobalt",
        car_number="40 A 123 BA",
        phone="+998901112233",
        status=DriverStatus.ACTIVE.value,
    )
    assert driver.missing_required_fields() == []
    assert driver.is_profile_complete()
    assert driver.is_access_valid()


def test_pending_driver_no_panel_access():
    driver = Driver(
        telegram_id=103,
        full_name="Yangi",
        car_model="Nexia",
        car_number="01 A 111 AA",
        phone="+998909998877",
        status=DriverStatus.PENDING.value,
    )
    assert not driver.is_legacy_or_approved()
    assert not driver.is_access_valid()


async def test_update_fields_does_not_overwrite_existing(session: AsyncSession):
    session.add(
        Driver(
            telegram_id=201,
            full_name="Eski Haydovchi",
            car_model="Malibu",
            car_number="50 B 200 BB",
            phone="+998901111111",
            status=DriverStatus.ACTIVE.value,
        )
    )
    await session.commit()

    await update_driver_fields(
        session,
        201,
        car_model="",  # bo'sh — yozilmasin
        car_number="50 B 200 BB",
        phone="+998902222222",
    )
    await session.commit()
    driver = await session.get(Driver, 201)
    assert driver is not None
    assert driver.car_model == "Malibu"
    assert driver.phone == "+998902222222"
    assert driver.status == DriverStatus.ACTIVE.value


async def test_submit_application_keeps_existing_active(session: AsyncSession):
    session.add(
        Driver(
            telegram_id=301,
            full_name="Mavjud",
            car_model="Cobalt",
            car_number="40 A 123 BA",
            phone="+998903333333",
            status=DriverStatus.ACTIVE.value,
        )
    )
    await session.commit()

    driver, is_new = await submit_driver_application(
        session,
        telegram_id=301,
        full_name="Mavjud Yangilangan",
        username="mavjud",
        car_model="Cobalt",
        car_number="40 A 123 BA",
        phone="+998903333333",
    )
    await session.commit()
    assert is_new is False
    assert driver.status == DriverStatus.ACTIVE.value
    assert driver.full_name == "Mavjud Yangilangan"


async def test_new_application_is_pending(session: AsyncSession):
    driver, is_new = await submit_driver_application(
        session,
        telegram_id=401,
        full_name="Yangi Haydovchi",
        username=None,
        car_model="Gentra",
        car_number="01 C 777 CC",
        phone="+998904444444",
    )
    await session.commit()
    assert is_new is True
    assert driver.status == DriverStatus.PENDING.value
    assert not driver.is_access_valid()
