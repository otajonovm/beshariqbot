"""Avto-e'lon: DB sessiya, format, joy yo'q, bitta faol."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.database.db_requests import (
    get_active_auto_post,
    list_due_auto_posts,
    start_auto_post,
    stop_auto_post,
    update_auto_post_after_send,
)
from bot.database.models import AutoPostStatus, Base, Driver, DriverStatus, utcnow
from bot.handlers.auto_post import looks_like_no_seats
from bot.services.auto_post import (
    format_duration_human,
    format_interval_human,
    payload_from_dict,
    payload_to_dict,
)
from bot.services.driver_raw_post import RawPayload


def test_format_interval_and_duration():
    assert format_interval_human(30) == "30 soniya"
    assert format_interval_human(60) == "1 daqiqa"
    assert format_duration_human(60) == "1 soat"
    assert format_duration_human(120) == "2 soat"
    assert format_duration_human(180) == "3 soat"


def test_looks_like_no_seats():
    assert looks_like_no_seats("Joy yo'q")
    assert looks_like_no_seats("joy yoq")
    assert looks_like_no_seats("0 joy")
    assert looks_like_no_seats("Joy qolmadi")
    assert not looks_like_no_seats("2 joy bor")
    assert not looks_like_no_seats("Butun salon")


def test_payload_roundtrip():
    p = RawPayload(
        text="2 joy",
        photo_file_id="ph1",
        departure_text="Hozir",
        seats_hint="2",
        direction="beshariq_tashkent",
    )
    d = payload_to_dict(p)
    back = payload_from_dict(d)
    assert back is not None
    assert back.text == "2 joy"
    assert back.photo_file_id == "ph1"
    assert back.departure_text == "Hozir"


@pytest.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_one_active_session_replaces_previous(session_maker):
    async with session_maker() as session:
        session.add(
            Driver(
                telegram_id=101,
                full_name="A",
                car_model="Cobalt",
                car_number="40 A 001 AA",
                phone="+998901111111",
                status=DriverStatus.ACTIVE.value,
            )
        )
        await session.commit()

        s1 = await start_auto_post(
            session,
            driver_id=101,
            interval_sec=60,
            duration_min=120,
            text="birinchi",
            photo_file_id=None,
            departure_text="Hozir",
            seats_hint="2",
            direction=None,
        )
        await session.commit()
        assert s1.status == AutoPostStatus.ACTIVE.value

        s2 = await start_auto_post(
            session,
            driver_id=101,
            interval_sec=30,
            duration_min=60,
            text="ikkinchi",
            photo_file_id=None,
            departure_text="Keyin",
            seats_hint="1",
            direction=None,
        )
        await session.commit()

        active = await get_active_auto_post(session, 101)
        assert active is not None
        assert active.id == s2.id
        assert active.text == "ikkinchi"
        assert active.interval_sec == 30

        await session.refresh(s1)
        assert s1.status == AutoPostStatus.STOPPED.value
        assert s1.stop_reason == "replaced"


@pytest.mark.asyncio
async def test_list_due_respects_interval(session_maker):
    async with session_maker() as session:
        session.add(
            Driver(
                telegram_id=202,
                full_name="B",
                car_model="Cobalt",
                car_number="40 A 002 AA",
                phone="+998902222222",
                status=DriverStatus.ACTIVE.value,
            )
        )
        await session.commit()
        sess = await start_auto_post(
            session,
            driver_id=202,
            interval_sec=60,
            duration_min=120,
            text="x",
            photo_file_id=None,
            departure_text=None,
            seats_hint=None,
            direction=None,
        )
        await session.commit()
        # Hozir due emas (last_sent_at=now)
        due = await list_due_auto_posts(session)
        assert all(s.id != sess.id for s in due)

        sess.last_sent_at = utcnow() - timedelta(seconds=61)
        await session.commit()
        due2 = await list_due_auto_posts(session)
        assert any(s.id == sess.id for s in due2)


@pytest.mark.asyncio
async def test_stop_and_message_ids(session_maker):
    async with session_maker() as session:
        session.add(
            Driver(
                telegram_id=303,
                full_name="C",
                car_model="Cobalt",
                car_number="40 A 003 AA",
                phone="+998903333333",
                status=DriverStatus.ACTIVE.value,
            )
        )
        await session.commit()
        sess = await start_auto_post(
            session,
            driver_id=303,
            interval_sec=30,
            duration_min=60,
            text="y",
            photo_file_id=None,
            departure_text=None,
            seats_hint=None,
            direction=None,
        )
        await session.commit()
        await update_auto_post_after_send(
            session,
            sess.id,
            message_ids={-1001: 11, -1002: 22},
        )
        await session.commit()
        await session.refresh(sess)
        assert sess.message_ids["-1001"] == 11
        assert sess.message_ids["-1002"] == 22

        stopped = await stop_auto_post(session, 303, reason="no_seats")
        await session.commit()
        assert stopped is not None
        assert stopped.status == AutoPostStatus.FINISHED.value
        assert await get_active_auto_post(session, 303) is None


@pytest.mark.asyncio
async def test_expired_is_due(session_maker):
    async with session_maker() as session:
        session.add(
            Driver(
                telegram_id=404,
                full_name="D",
                car_model="Cobalt",
                car_number="40 A 004 AA",
                phone="+998904444444",
                status=DriverStatus.ACTIVE.value,
            )
        )
        await session.commit()
        sess = await start_auto_post(
            session,
            driver_id=404,
            interval_sec=60,
            duration_min=60,
            text="z",
            photo_file_id=None,
            departure_text=None,
            seats_hint=None,
            direction=None,
        )
        sess.ends_at = utcnow() - timedelta(seconds=1)
        await session.commit()
        due = await list_due_auto_posts(session)
        assert any(s.id == sess.id for s in due)


def test_auto_post_panel_buttons_reserved():
    from bot.keyboards.default_kb import (
        BTN_AUTO_POST,
        BTN_AUTO_STOP,
        BTN_NO_SEATS,
        RESERVED_PRIVATE_TEXTS,
        driver_panel_kb,
    )

    assert BTN_AUTO_POST in RESERVED_PRIVATE_TEXTS
    assert BTN_AUTO_STOP in RESERVED_PRIVATE_TEXTS
    assert BTN_NO_SEATS in RESERVED_PRIVATE_TEXTS
    kb = driver_panel_kb(has_last=True, auto_active=True)
    texts = {btn.text for row in kb.keyboard for btn in row}
    assert BTN_AUTO_POST in texts
    assert BTN_AUTO_STOP in texts
    assert BTN_NO_SEATS in texts
