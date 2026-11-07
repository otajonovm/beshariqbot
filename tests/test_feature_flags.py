"""Feature flags: panel tugmalari yashiriladi, stub yo'q."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.database.models import Base, FeatureFlag
from bot.keyboards.default_kb import (
    BTN_ANNOUNCE,
    BTN_AUTO_POST,
    BTN_PROFILE,
    BTN_REPEAT_LAST,
    driver_panel_kb,
)
from bot.keyboards.inline_kb import profile_edit_kb
from bot.services import feature_flags as ff
from bot.services.driver_card import driver_announce_kb
from bot.database.models import Driver, DriverStatus


@pytest.fixture(autouse=True)
def _reset_flag_cache():
    ff.apply_snapshot({k: True for k in ff.FLAG_KEYS})
    yield
    ff.apply_snapshot({k: True for k in ff.FLAG_KEYS})


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


def _btn_texts(kb) -> set[str]:
    return {btn.text for row in kb.keyboard for btn in row}


def test_panel_hides_buttons_when_flags_off():
    full = driver_panel_kb(
        has_last=True,
        auto_active=True,
        show_wizard=True,
        show_raw=True,
        show_auto=True,
        show_profile=True,
    )
    texts = _btn_texts(full)
    assert BTN_ANNOUNCE in texts
    assert BTN_REPEAT_LAST in texts
    assert BTN_AUTO_POST in texts
    assert BTN_PROFILE in texts

    hidden = driver_panel_kb(
        has_last=True,
        auto_active=True,
        show_wizard=False,
        show_raw=False,
        show_auto=False,
        show_profile=False,
    )
    texts2 = _btn_texts(hidden)
    assert BTN_ANNOUNCE not in texts2
    assert BTN_REPEAT_LAST not in texts2
    assert BTN_AUTO_POST not in texts2
    assert "⏹ To'xtatish" not in texts2
    assert BTN_PROFILE not in texts2
    # Stub matn yo'q
    joined = " ".join(texts2).lower()
    assert "o'chiq" not in joined
    assert "hozircha" not in joined


def test_profile_kb_hides_photo_row():
    with_photo = profile_edit_kb(show_photo=True)
    texts = {b.text for row in with_photo.inline_keyboard for b in row}
    assert "✏️ Rasm" in texts

    no_photo = profile_edit_kb(show_photo=False)
    texts2 = {b.text for row in no_photo.inline_keyboard for b in row}
    assert "✏️ Rasm" not in texts2


def test_announce_kb_respects_call_order_flags():
    driver = Driver(
        telegram_id=1,
        full_name="A",
        car_model="C",
        car_number="40 A 1 AA",
        phone="+998901112233",
        status=DriverStatus.ACTIVE.value,
    )
    ff.apply_snapshot({**{k: True for k in ff.FLAG_KEYS}})
    kb = driver_announce_kb(driver)
    assert kb is not None
    texts = {b.text for row in kb.inline_keyboard for b in row}
    assert any("Zakas" in t for t in texts)
    assert any("📞" in t for t in texts)

    ff.apply_snapshot({**{k: True for k in ff.FLAG_KEYS}, "order_button": False})
    kb2 = driver_announce_kb(driver)
    texts2 = {b.text for row in kb2.inline_keyboard for b in row}
    assert not any("Zakas" in t for t in texts2)
    assert any("📞" in t for t in texts2)

    ff.apply_snapshot(
        {**{k: True for k in ff.FLAG_KEYS}, "order_button": False, "call_button": False}
    )
    assert driver_announce_kb(driver) is None


@pytest.mark.asyncio
async def test_set_flag_persists(session):
    await ff.ensure_defaults(session)
    await session.commit()
    row = await ff.set_flag(session, "driver_wizard", False)
    await session.commit()
    assert row is not None
    assert ff.is_enabled("driver_wizard") is False
    stored = await session.get(FeatureFlag, "driver_wizard")
    assert stored is not None
    assert stored.enabled is False

    await ff.load_flags(session)
    assert ff.is_enabled("driver_wizard") is False
    assert ff.is_enabled("driver_auto_post") is True


def test_flag_meta_covers_requested_keys():
    needed = {
        "driver_auto_post",
        "driver_wizard",
        "driver_raw_post",
        "driver_profile_edit",
        "driver_photo_edit",
        "driver_ask_time",
        "call_button",
        "order_button",
        "passenger_relay",
    }
    assert needed <= set(ff.FLAG_KEYS)
