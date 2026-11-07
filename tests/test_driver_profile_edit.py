"""Profil ko'rinishi va maydon yangilash."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.database.db_requests import update_driver_fields
from bot.database.models import Base, Driver, DriverStatus
from bot.handlers.driver import _normalize_phone, _normalize_plate, _profile_text
from bot.keyboards.inline_kb import profile_edit_kb


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


def test_profile_text_lists_fields():
    driver = Driver(
        telegram_id=1,
        full_name="Ali",
        car_model="Cobalt",
        car_number="40 A 123 BA",
        phone="+998901112233",
        car_color="oq",
        seats_count=3,
        last_departure_text="Hozir yuryapman",
        preferred_direction="beshariq_tashkent",
        status=DriverStatus.ACTIVE.value,
    )
    text = _profile_text(driver)
    assert "Ali" in text
    assert "+998901112233" in text
    assert "Cobalt" in text
    assert "oq" in text
    assert "40 A 123 BA" in text
    assert "3" in text
    assert "Hozir yuryapman" in text
    assert "Beshariq" in text


def test_profile_edit_kb_has_fields():
    kb = profile_edit_kb()
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    for key in ("prof:name", "prof:phone", "prof:car", "prof:color", "prof:plate", "prof:seats", "prof:photo", "prof:time", "prof:route"):
        assert key in data


def test_phone_and_plate_normalize():
    assert _normalize_phone("901112233") == "+998901112233"
    assert _normalize_phone("+998 90 111 22 33") == "+998901112233"
    assert _normalize_plate("40 a 123 ba") == "40 A 123 BA"
    assert _normalize_plate("xx") is None


async def test_update_single_field_keeps_others(session: AsyncSession):
    session.add(
        Driver(
            telegram_id=801,
            full_name="Eski",
            car_model="Malibu",
            car_number="01 A 111 AA",
            phone="+998901111111",
            car_color="qora",
            seats_count=4,
            status=DriverStatus.ACTIVE.value,
        )
    )
    await session.commit()

    await update_driver_fields(session, 801, phone="+998902222222")
    await session.commit()
    driver = await session.get(Driver, 801)
    assert driver is not None
    assert driver.phone == "+998902222222"
    assert driver.car_model == "Malibu"
    assert driver.full_name == "Eski"
    assert driver.car_color == "qora"
    assert driver.seats_count == 4
    assert driver.status == DriverStatus.ACTIVE.value


async def test_preferred_direction_and_clear_photo(session: AsyncSession):
    session.add(
        Driver(
            telegram_id=802,
            full_name="Rasmli",
            car_model="Cobalt",
            car_number="40 B 222 BB",
            phone="+998903333333",
            photo_file_id="old_photo",
            status=DriverStatus.ACTIVE.value,
        )
    )
    await session.commit()
    await update_driver_fields(
        session, 802, preferred_direction="tashkent_beshariq", clear_photo=True
    )
    await session.commit()
    driver = await session.get(Driver, 802)
    assert driver is not None
    assert driver.preferred_direction == "tashkent_beshariq"
    assert driver.photo_file_id is None
    assert driver.full_name == "Rasmli"
