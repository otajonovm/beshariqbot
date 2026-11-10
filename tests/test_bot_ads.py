"""Reklama: interval soniya, caption limiti, legacy migratsiya."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.database.models import AdClick, Base, BotGroup
from bot.services import app_settings as apps
from bot.services import bot_ads
from bot.services.chat_rate_limit import chat_send_count, wait_chat_slot
from bot.keyboards.inline_kb import admin_ad_interval_kb, admin_ads_menu_kb


TZ = ZoneInfo("Asia/Tashkent")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    apps._cache.clear()
    apps._cache.update(dict(apps.SETTING_DEFAULTS))
    yield
    apps._cache.clear()
    apps._cache.update(dict(apps.SETTING_DEFAULTS))


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


def test_interval_choices_include_30s_and_hours():
    assert 30 in apps.AD_INTERVAL_CHOICES
    assert 60 in apps.AD_INTERVAL_CHOICES
    assert 3600 in apps.AD_INTERVAL_CHOICES
    assert 10800 in apps.AD_INTERVAL_CHOICES


def test_legacy_hours_to_seconds():
    apps._cache["ad_interval_sec"] = ""
    apps._cache["ad_interval_hours"] = 2
    assert apps.resolve_ad_interval_sec() == 7200


def test_interval_sec_direct():
    apps._cache["ad_interval_sec"] = 30
    assert apps.resolve_ad_interval_sec() == 30


def test_format_interval():
    assert "s" in apps.format_interval_human(30)
    assert "min" in apps.format_interval_human(60)
    assert "soat" in apps.format_interval_human(3600)


def test_caption_limit():
    ok, _ = bot_ads.caption_ok("qisqa")
    assert ok is True
    long = "x" * 1025
    ok2, msg = bot_ads.caption_ok(long)
    assert ok2 is False
    assert "1024" in msg


def test_ad_keyboard_has_order_button():
    kb = bot_ads.ad_keyboard(bot_username="testbot", button_text="🚕 Taksi buyurtma berish")
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Taksi" in t for t in texts)
    url = kb.inline_keyboard[0][0].url
    assert url.endswith("start=order")
    assert "testbot" in url


def test_ad_keyboard_dispatcher_optional():
    kb = bot_ads.ad_keyboard(
        bot_username="x",
        dispatcher_phone="+998901112233",
    )
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "📞 Dispetcher" in texts
    urls = [b.url for row in kb.inline_keyboard for b in row]
    assert any(u and u.startswith("https://wa.me/") for u in urls)
    assert not any(u and u.startswith("tel:") for u in urls)


def test_active_window():
    apps._cache["ad_active_from"] = "07:00"
    apps._cache["ad_active_to"] = "23:00"
    noon = datetime(2026, 5, 1, 12, 0, tzinfo=TZ)
    night = datetime(2026, 5, 1, 3, 0, tzinfo=TZ)
    assert bot_ads.in_active_window(noon) is True
    assert bot_ads.in_active_window(night) is False


def test_admin_interval_kb_has_many_options():
    kb = admin_ad_interval_kb()
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("30 s" in t or t == "30 s" for t in labels)
    assert any("1 min" in t for t in labels)
    assert any("1 soat" in t for t in labels)


def test_ads_menu_has_required_buttons():
    kb = admin_ads_menu_kb(enabled=False, interval_label="30 s", duration_label="Doimiy")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Yoqish" in t for t in labels)
    assert any("Interval" in t for t in labels)
    assert any("Davomiylik" in t for t in labels)
    assert any("Banner" in t for t in labels)
    assert any("Ko'rish" in t or "Ko‘rish" in t for t in labels)
    assert any("Hozir" in t for t in labels)


@pytest.mark.asyncio
async def test_list_ad_groups_respects_flag(session):
    # Bir martalik migratsiyani o'tkazib yuborish (flaglarni saqlash)
    apps._cache["ad_all_admin_groups_v1"] = True
    session.add(
        BotGroup(chat_id=-1001, title="A", flag_bot_ads=True, approved=True, can_post=True)
    )
    session.add(
        BotGroup(chat_id=-1002, title="B", flag_bot_ads=False, approved=True, can_post=True)
    )
    session.add(
        BotGroup(chat_id=-1003, title="C", flag_bot_ads=True, approved=True, can_post=False)
    )
    await session.commit()
    groups = await bot_ads.list_ad_groups(session)
    ids = {g.chat_id for g in groups}
    assert -1001 in ids
    assert -1002 not in ids  # admin o'chirgan
    assert -1003 not in ids  # yozish huquqi yo'q


@pytest.mark.asyncio
async def test_list_ad_groups_includes_all_when_flag_on(session):
    apps._cache["ad_all_admin_groups_v1"] = True
    for i, cid in enumerate((-2001, -2002, -2003)):
        session.add(
            BotGroup(
                chat_id=cid,
                title=f"G{i}",
                flag_bot_ads=True,
                approved=True,
                can_post=True,
            )
        )
    await session.commit()
    groups = await bot_ads.list_ad_groups(session)
    assert {g.chat_id for g in groups} >= {-2001, -2002, -2003}


@pytest.mark.asyncio
async def test_list_ad_groups_includes_channels(session):
    apps._cache["ad_all_admin_groups_v1"] = True
    session.add(
        BotGroup(
            chat_id=-3001,
            title="Taxi Channel",
            chat_type="channel",
            flag_bot_ads=True,
            approved=True,
            can_post=True,
        )
    )
    session.add(
        BotGroup(
            chat_id=-3002,
            title="No post channel",
            chat_type="channel",
            flag_bot_ads=True,
            approved=True,
            can_post=False,
        )
    )
    await session.commit()
    groups = await bot_ads.list_ad_groups(session)
    ids = {g.chat_id for g in groups}
    assert -3001 in ids
    assert -3002 not in ids


@pytest.mark.asyncio
async def test_record_ad_click(session):
    await bot_ads.record_ad_click(session, 42)
    await session.commit()
    clicks, orders = await bot_ads.ad_stats(session, days=1)
    assert clicks == 1
    assert orders == 0


@pytest.mark.asyncio
async def test_shared_rate_limit_records():
    chat = -999888
    before = chat_send_count(chat)
    await wait_chat_slot(chat)
    assert chat_send_count(chat) == before + 1
