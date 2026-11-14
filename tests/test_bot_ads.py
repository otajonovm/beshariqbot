"""Reklama: interval soniya, caption limiti, legacy migratsiya, sikl."""

from __future__ import annotations

import inspect
import json
from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage, SendPhoto
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.database.models import AdSendLog, AppSetting, Base, BotGroup
from bot.services import app_settings as apps
from bot.services import bot_ads
from bot.services import chat_rate_limit
from bot.services.chat_rate_limit import chat_send_count, wait_chat_slot
from bot.keyboards.inline_kb import admin_ad_interval_kb, admin_ads_menu_kb


TZ = ZoneInfo("Asia/Tashkent")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    apps._cache.clear()
    apps._cache.update(dict(apps.SETTING_DEFAULTS))
    bot_ads._bot_username_cache = None
    yield
    apps._cache.clear()
    apps._cache.update(dict(apps.SETTING_DEFAULTS))
    bot_ads._bot_username_cache = None


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
    assert kb.inline_keyboard[0][0].text == "🚕 Taksi buyurtma berish"
    url = kb.inline_keyboard[0][0].url
    assert url == "https://t.me/testbot?start=order"


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


def test_legacy_minutes_cache_to_seconds():
    apps._cache["ad_interval_sec"] = ""
    apps._cache["ad_interval_minutes"] = 2
    assert apps.resolve_ad_interval_sec() == 120


def test_tiny_stored_seconds_treated_as_minutes():
    """30 dan kichik va tanlovda yo'q qiymat — eski daqiqa."""
    apps._cache["ad_interval_sec"] = 2
    assert apps.resolve_ad_interval_sec() == 120
    apps._cache["ad_interval_sec"] = 30
    assert apps.resolve_ad_interval_sec() == 30


def test_dispatcher_phone_lands_in_caption_not_tel_url():
    cap = bot_ads.compose_caption("Salom", "+998901112233")
    assert "📞 Dispetcher" in cap
    assert "+998901112233" in cap
    assert "tel:" not in cap
    # Matnda raqam bor bo'lsa ikkinchi marta qo'shilmaydi
    again = bot_ads.compose_caption(cap, "+998901112233")
    assert again.count("+998901112233") == 1


@pytest.mark.asyncio
async def test_minutes_row_migrates_once(session):
    session.add(AppSetting(key="ad_interval_minutes", value=json.dumps(5)))
    await session.commit()
    apps._cache.clear()
    await apps.ensure_defaults(session)
    await session.commit()
    await apps.load_settings(session)
    assert apps.resolve_ad_interval_sec() == 300

    await apps.set_setting(session, "ad_interval_sec", 30)
    await session.commit()
    await apps.ensure_defaults(session)
    await session.commit()
    assert apps.resolve_ad_interval_sec() == 30
    # daqiqa qatori joyida qoladi, lekin soniya ustun
    row = await session.get(AppSetting, "ad_interval_minutes")
    assert row is not None
    assert json.loads(row.value) == 5


def _msg(mid: int, markup):
    return SimpleNamespace(message_id=mid, reply_markup=markup)


def _forbidden():
    return TelegramForbiddenError(
        method=SendMessage(chat_id=1, text="x"),
        message="not enough rights to send photos",
    )


def _retry(seconds: int = 2):
    return TelegramRetryAfter(
        method=SendPhoto(chat_id=1, photo="x"),
        message="flood",
        retry_after=seconds,
    )


@pytest.mark.asyncio
async def test_delete_failure_still_sends(session, monkeypatch):
    apps._cache["ad_interval_sec"] = 30
    apps._cache["ad_text"] = "Salom"
    bot_ads._bot_username_cache = "testbot"
    sent: list[int] = []

    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="testbot")

        async def delete_message(self, chat_id, message_id):
            raise RuntimeError(f"cannot delete {message_id}")

        async def send_photo(self, chat_id, photo=None, caption=None, reply_markup=None, **kwargs):
            sent.append(chat_id)
            return _msg(77, reply_markup)

        async def send_message(self, *args, **kwargs):
            raise AssertionError("text fallback")

    group = BotGroup(
        chat_id=-6101,
        title="Del",
        flag_bot_ads=True,
        approved=True,
        can_post=True,
        last_ad_message_id=15,
    )
    session.add(group)
    await session.commit()
    ok = await bot_ads.send_one_group(Bot(), session, group)
    assert ok is True
    assert sent == [-6101]
    assert group.last_ad_message_id == 77


@pytest.mark.asyncio
async def test_retry_after_waits_and_doubles_interval(session, monkeypatch):
    apps._cache["ad_interval_sec"] = 30
    apps._cache["ad_text"] = "Salom"
    bot_ads._bot_username_cache = "testbot"
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(float(seconds))

    monkeypatch.setattr(bot_ads.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="testbot")

        async def delete_message(self, chat_id, message_id):
            return None

        async def send_photo(self, chat_id, photo=None, caption=None, reply_markup=None, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _retry(2)
            return _msg(90, reply_markup)

        async def send_message(self, *args, **kwargs):
            raise AssertionError("text fallback")

    group = BotGroup(
        chat_id=-6102,
        title="429",
        flag_bot_ads=True,
        approved=True,
        can_post=True,
        last_ad_message_id=4,
    )
    session.add(group)
    await session.commit()
    before = datetime.now(TZ)
    ok = await bot_ads.send_one_group(Bot(), session, group)
    assert ok is True
    assert calls["n"] == 2
    assert slept and slept[0] >= 2
    assert group.ad_boost_until is not None
    nxt = group.ad_next_run_at
    assert nxt is not None
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=TZ)
    # Vaqtincha 2× : 30s → 60s
    assert 55 <= (nxt - before).total_seconds() <= 70


@pytest.mark.asyncio
async def test_missing_photo_rights_warns_once(session, monkeypatch):
    apps._cache["ad_interval_sec"] = 30
    apps._cache["ad_text"] = "Salom"
    bot_ads._bot_username_cache = "testbot"
    notes: list[str] = []

    async def fake_notify(bot, text, reply_markup=None):
        notes.append(text)

    monkeypatch.setattr(bot_ads, "notify_admins", fake_notify)

    class Bot:
        async def get_me(self):
            return SimpleNamespace(username="testbot")

        async def delete_message(self, chat_id, message_id):
            return None

        async def send_photo(self, chat_id, photo=None, caption=None, reply_markup=None, **kwargs):
            raise _forbidden()

        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            raise _forbidden()

    group = BotGroup(
        chat_id=-6103,
        title="No rights",
        flag_bot_ads=True,
        approved=True,
        can_post=True,
    )
    session.add(group)
    await session.commit()
    assert await bot_ads.send_one_group(Bot(), session, group) is False
    assert await bot_ads.send_one_group(Bot(), session, group) is False
    assert len(notes) == 1
    assert "-6103" in notes[0]
    assert group.ad_permission_warned is True


def _memory_engine():
    """Bitta :memory: ulanish — ikkinchi sessiya bo'sh bazaga tushmasin."""
    return create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


@pytest.mark.asyncio
async def test_skip_group_without_post_rights_warns_once(monkeypatch):
    """can_post=False: yuborilmaydi, admin bir marta ogohlantiriladi."""
    apps._cache["ad_enabled"] = True
    apps._cache["ad_interval_sec"] = 30
    apps._cache["ad_all_admin_groups_v1"] = True
    apps._cache["ad_main_group_enabled"] = False
    apps._cache["ad_active_from"] = "00:00"
    apps._cache["ad_active_to"] = "23:59"
    notes: list[str] = []

    async def fake_notify(bot, text, reply_markup=None):
        notes.append(text)

    monkeypatch.setattr(bot_ads, "notify_admins", fake_notify)
    monkeypatch.setattr("bot.relay.service.seen_group_ids", lambda: [])

    class Bot:
        async def send_photo(self, *args, **kwargs):
            raise AssertionError("post rights yo'q guruhga yuborilmasin")

        async def send_message(self, *args, **kwargs):
            raise AssertionError("post rights yo'q guruhga yuborilmasin")

        async def get_me(self):
            return SimpleNamespace(username="testbot", id=1)

    from bot.services.bot_groups import ensure_known_groups

    engine = _memory_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await ensure_known_groups(session)
        await session.execute(update(BotGroup).values(flag_bot_ads=False, can_post=False))
        session.add(
            BotGroup(
                chat_id=-6104,
                title="Skip me",
                flag_bot_ads=True,
                approved=True,
                can_post=False,
                ad_permission_warned=False,
            )
        )
        await session.commit()

    await bot_ads.check_bot_ads(Bot(), maker)
    await bot_ads.check_bot_ads(Bot(), maker)
    assert len(notes) == 1
    assert "-6104" in notes[0]
    await engine.dispose()


@pytest.mark.asyncio
async def test_30s_cycle_three_groups_ten_minutes(monkeypatch):
    """30 s × 3 guruh × 10 daqiqa: eski post o'chadi, 429 yo'q, relay/avto kutmaydi.

    Devor soati suriladi — haqiqiy 10 daqiqa kutilmaydi.
    """
    engine = _memory_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    test_ids = (-7201, -7202, -7203)
    apps._cache["ad_enabled"] = True
    apps._cache["ad_interval_sec"] = 30
    apps._cache["ad_duration_sec"] = 0
    apps._cache["ad_ends_at"] = ""
    apps._cache["ad_all_admin_groups_v1"] = True
    apps._cache["ad_main_group_enabled"] = False
    apps._cache["ad_active_from"] = "00:00"
    apps._cache["ad_active_to"] = "23:59"
    apps._cache["ad_photo_file_id"] = ""
    apps._cache["ad_text"] = "Salom reklama"
    apps._cache["ad_dispatcher_phone"] = ""
    bot_ads._bot_username_cache = "cyclebot"

    clock = {"now": datetime(2026, 6, 1, 12, 0, tzinfo=TZ)}
    mono = {"t": 10_000.0}
    sleeps: list[float] = []
    send_times: list[float] = []

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            current = clock["now"]
            if tz is not None:
                return current.astimezone(tz)
            return current

    async def fake_sleep(seconds):
        sec = float(seconds)
        sleeps.append(sec)
        mono["t"] += sec

    def fake_mono():
        return mono["t"]

    jitter = {"i": 0}

    def fake_random():
        value = (0.0, 0.5)[jitter["i"] % 2]
        jitter["i"] += 1
        return value

    monkeypatch.setattr(bot_ads, "datetime", FrozenDateTime)
    monkeypatch.setattr(bot_ads.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(bot_ads.random, "random", fake_random)
    monkeypatch.setattr(chat_rate_limit.time, "monotonic", fake_mono)
    monkeypatch.setattr("bot.relay.service.seen_group_ids", lambda: [])

    class Bot:
        def __init__(self):
            self.ops: list[tuple[int, str, int]] = []
            self._n = 1000

        async def get_me(self):
            return SimpleNamespace(username="cyclebot", id=1)

        async def delete_message(self, chat_id, message_id):
            self.ops.append((int(chat_id), "del", int(message_id)))

        async def send_photo(self, chat_id, photo=None, caption=None, reply_markup=None, **kwargs):
            self._n += 1
            self.ops.append((int(chat_id), "send", self._n))
            send_times.append(mono["t"])
            return _msg(self._n, reply_markup)

        async def send_message(self, chat_id, text=None, reply_markup=None, **kwargs):
            raise AssertionError(f"matn-only kutilmagan chat={chat_id}")

        async def edit_message_reply_markup(self, **kwargs):
            raise AssertionError("markup tushib qoldi")

    from bot.services.bot_groups import ensure_known_groups

    async with maker() as session:
        await ensure_known_groups(session)
        await session.execute(update(BotGroup).values(flag_bot_ads=False, can_post=False))
        for cid in test_ids:
            session.add(
                BotGroup(
                    chat_id=cid,
                    title=f"G{cid}",
                    flag_bot_ads=True,
                    approved=True,
                    can_post=True,
                    ad_permission_warned=False,
                )
            )
        await session.commit()

    bot = Bot()
    cycles = 20  # 10 daqiqa / 30 s
    try:
        for _ in range(cycles):
            await bot_ads.check_bot_ads(bot, maker)
            clock["now"] = clock["now"] + timedelta(seconds=30)
            mono["t"] += 30

        sends = [op for op in bot.ops if op[1] == "send"]
        assert len(sends) == cycles * 3
        assert {op[0] for op in sends} == set(test_ids)
        assert all(1.0 <= s <= 2.0 for s in sleeps), sleeps
        assert len(sleeps) == cycles * 2

        per: dict[int, list[tuple[str, int]]] = defaultdict(list)
        for cid, kind, mid in bot.ops:
            per[cid].append((kind, mid))
        for cid in test_ids:
            events = per[cid]
            assert events[0] == ("send", events[0][1])
            prev = events[0][1]
            idx = 1
            while idx < len(events):
                assert events[idx] == ("del", prev)
                assert events[idx + 1][0] == "send"
                prev = events[idx + 1][1]
                idx += 2
            assert sum(1 for kind, _ in events if kind == "send") == cycles

        deltas = [send_times[i + 1] - send_times[i] for i in range(len(send_times) - 1)]
        for i, delta in enumerate(deltas):
            if (i + 1) % 3 == 0:
                assert abs(delta - 30) < 0.01
            else:
                assert 1.0 <= delta <= 2.0

        # 30 s da guruhga 2 ta reklama — 20/min limitga tegmaydi.
        # Avto-e'lon shu limiterdan o'tadi va kutmasligi kerak.
        before_sleeps = len(sleeps)
        auto_wait = await wait_chat_slot(test_ids[0])
        assert auto_wait == 0.0
        assert len(sleeps) == before_sleeps

        # Relay bu limiterga kirmaydi (asosiy guruhga alohida navbat).
        relay_src = inspect.getsource(__import__("bot.relay.service", fromlist=["service"]))
        relay_out = inspect.getsource(__import__("bot.relay.outbound", fromlist=["outbound"]))
        assert "wait_chat_slot" not in relay_src
        assert "wait_chat_slot" not in relay_out
        relay_wait = await wait_chat_slot(-1003956911813)
        assert relay_wait == 0.0

        async with maker() as session:
            ok_n = int(
                (
                    await session.execute(
                        select(func.count(AdSendLog.id)).where(AdSendLog.ok.is_(True))
                    )
                ).scalar_one()
            )
            bad_n = int(
                (
                    await session.execute(
                        select(func.count(AdSendLog.id)).where(AdSendLog.ok.is_(False))
                    )
                ).scalar_one()
            )
            assert ok_n == cycles * 3
            assert bad_n == 0
            rows = list(
                (
                    await session.execute(select(BotGroup).where(BotGroup.chat_id.in_(test_ids)))
                ).scalars()
            )
            last = {op[0]: op[2] for op in sends}
            for row in rows:
                assert row.last_ad_message_id == last[row.chat_id]
    finally:
        for cid in (*test_ids, -1003956911813):
            chat_rate_limit._chat_times.pop(cid, None)
        await engine.dispose()
