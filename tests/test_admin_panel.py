"""Admin panel: soft-delete, premium override, to'lov migratsiya."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.database.models import Base, Driver, DriverStatus, utcnow
from bot.services import feature_flags as ff
from bot.services.driver_moderation import ban_driver, restore_driver, soft_delete_driver, unban_driver
from bot.services.feature_flags import (
    driver_feature_enabled,
    expire_premium_if_needed,
    set_driver_override,
    set_premium_package,
)
from bot.services.payments import accept_payment
from bot.keyboards.default_kb import BTN_AUTO_POST, driver_panel_kb
from bot.keyboards.inline_kb import admin_menu_kb


@pytest.fixture(autouse=True)
def _reset_flags():
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


def _driver(**kwargs) -> Driver:
    data = dict(
        telegram_id=101,
        full_name="Test Haydovchi",
        car_model="Cobalt",
        car_number="40 A 123 AA",
        phone="+998901112233",
        status=DriverStatus.ACTIVE.value,
        payment_due_at=utcnow() + timedelta(days=7),
    )
    data.update(kwargs)
    return Driver(**data)


@pytest.mark.asyncio
async def test_soft_delete_and_restore(session):
    d = _driver()
    session.add(d)
    await session.commit()

    await soft_delete_driver(session, d)
    await session.commit()
    assert d.status == DriverStatus.DELETED.value
    assert d.deleted_at is not None
    assert d.is_access_valid() is False
    assert d.is_legacy_or_approved() is False

    await restore_driver(session, d)
    await session.commit()
    assert d.status == DriverStatus.ACTIVE.value
    assert d.deleted_at is None
    assert d.is_access_valid() is True


@pytest.mark.asyncio
async def test_ban_unban_stops_access(session):
    d = _driver()
    session.add(d)
    await session.commit()
    await ban_driver(session, d, reason="admin")
    assert d.status == DriverStatus.BANNED.value
    assert d.is_access_valid() is False
    await unban_driver(session, d)
    assert d.status == DriverStatus.ACTIVE.value
    assert d.is_access_valid() is True


@pytest.mark.asyncio
async def test_premium_override_hides_panel_button(session):
    d = _driver()
    session.add(d)
    await session.commit()
    assert driver_feature_enabled("driver_auto_post", d) is True

    await set_driver_override(session, d, "driver_auto_post", False)
    await session.commit()
    assert driver_feature_enabled("driver_auto_post", d) is False

    kb = driver_panel_kb(
        has_last=False,
        auto_active=False,
        show_wizard=True,
        show_raw=True,
        show_auto=driver_feature_enabled("driver_auto_post", d),
        show_profile=True,
    )
    texts = {btn.text for row in kb.keyboard for btn in row}
    assert BTN_AUTO_POST not in texts


@pytest.mark.asyncio
async def test_global_off_beats_driver_on(session):
    d = _driver()
    session.add(d)
    await session.commit()
    await set_driver_override(session, d, "call_button", True)
    ff.apply_snapshot({**{k: True for k in ff.FLAG_KEYS}, "call_button": False})
    assert driver_feature_enabled("call_button", d) is False


@pytest.mark.asyncio
async def test_premium_expiry(session):
    d = _driver(premium_until=utcnow() - timedelta(hours=1))
    session.add(d)
    await session.commit()
    changed = await expire_premium_if_needed(session, d)
    assert changed is True
    assert driver_feature_enabled("driver_auto_post", d) is False
    # Ikkinchi marta — qayta o'chirmaslik
    assert await expire_premium_if_needed(session, d) is False


@pytest.mark.asyncio
async def test_premium_package_enable(session):
    d = _driver()
    session.add(d)
    await session.commit()
    await set_premium_package(session, d, days=30, enable=True)
    assert d.has_active_premium()
    assert driver_feature_enabled("order_button", d) is True


@pytest.mark.asyncio
async def test_payment_accept_unblocks(session):
    d = _driver(
        status=DriverStatus.BANNED.value,
        block_reason="payment",
        payment_due_at=utcnow() - timedelta(days=2),
    )
    session.add(d)
    await session.commit()
    await accept_payment(session, d, admin_id=1, days=30)
    await session.commit()
    assert d.status == DriverStatus.ACTIVE.value
    assert d.block_reason is None
    assert d.payment_due_at > utcnow()


@pytest.mark.asyncio
async def test_migration_payment_due_not_immediate_block(session):
    """Yangi haydovchi: payment_due_at kelajakda — bloklanmasin."""
    d = _driver(payment_due_at=utcnow() + timedelta(days=7))
    session.add(d)
    await session.commit()
    assert d.payment_status() == "to'langan"
    assert d.is_access_valid() is True


def test_admin_menu_has_six_buttons():
    kb = admin_menu_kb()
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert len(labels) == 6
    assert "🚖 Haydovchilar" in labels
    assert "👥 Guruhlar" in labels
    assert "💳 To'lovlar" in labels
    assert "⭐ Premium" in labels
    assert "📣 Reklama" in labels
    assert "📊 Statistika" in labels
