"""Xom e'lon: stil, limit, guruh tartibi."""

from __future__ import annotations

from bot.database.models import Driver, DriverStatus
from bot.services import driver_raw_post as raw


def _driver(**kwargs) -> Driver:
    base = dict(
        telegram_id=5001,
        full_name="Test Haydovchi",
        car_model="Cobalt",
        car_number="40 A 123 BA",
        phone="+998901112233",
        username="test_driver",
        status=DriverStatus.ACTIVE.value,
    )
    base.update(kwargs)
    return Driver(**base)


def test_compose_card_style():
    driver = _driver()
    body = raw.compose_body(
        "Beshariqdan 2 joy\nNarxi 200000",
        driver,
        departure_text="Hozir yuryapman",
    )
    assert "HOZIRGA" in body
    assert "2 ta JOY BOR" in body
    assert "BESHARIQ" in body.upper() or "Beshariq" in body
    assert "+998901112233" in body
    assert "Test Haydovchi" in body
    assert "Cobalt" in body


def test_post_limit_blocks_after_quota(monkeypatch):
    monkeypatch.setattr(raw.settings, "driver_post_limit_per_hour", 2)
    raw._post_times.clear()
    uid = 777
    assert raw.check_post_limit(uid, now=100.0)[0] is True
    raw.record_post(uid, now=100.0)
    raw.record_post(uid, now=110.0)
    ok, wait = raw.check_post_limit(uid, now=120.0)
    assert ok is False
    assert wait > 0
    ok2, _ = raw.check_post_limit(uid, now=100.0 + 3600.0)
    assert ok2 is True


def test_save_and_get_last_raw():
    raw._last_raw.clear()
    payload = raw.RawPayload(text="joy bor", photo_file_id="photo123")
    raw.save_last_raw(42, payload)
    last = raw.get_last_raw(42)
    assert last is not None
    assert last.text == "joy bor"
    assert last.photo_file_id == "photo123"


def test_format_result_mentions_failures():
    text = raw.format_result([1, 2], [(-100, "Forbidden: bot was kicked")])
    assert "Yuborildi: <b>2</b>" in text
    assert "Xato: <b>1</b>" in text
    assert "-100" in text


def test_driver_posts_skip_426_global_include_others():
    targets = raw.post_targets()
    primary = -1003956911813
    assert primary not in targets
    assert -1004421198641 in targets
    for cid in raw.settings.group_ids:
        if cid == primary:
            assert cid not in targets
        else:
            assert cid in targets


def test_normalize_short_group_id():
    from bot.config import Settings

    assert Settings._normalize_chat_id("4421198641") == -1004421198641
    assert Settings._normalize_chat_id("-1004421198641") == -1004421198641
