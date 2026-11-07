"""Ketish vaqti shablonlari va e'longa qo'shish."""

from __future__ import annotations

from datetime import datetime, timezone

from bot.database.models import Driver, DriverStatus
from bot.services.departure import (
    MAX_CUSTOM_LEN,
    apply_departure,
    has_departure_line,
    normalize_departure,
    resolve_template,
    strip_departure_line,
    tashkent_clock_text,
)
from bot.services.driver_raw_post import compose_body


def test_templates_and_clock():
    assert resolve_template("now") == "Hozir yuryapman"
    assert resolve_template("30m") == "30 daqiqadan keyin"
    assert resolve_template("1h") == "1 soatdan keyin"
    assert resolve_template("2h") == "2 soatdan keyin"
    assert resolve_template("eve") == "Bugun kechqurun"
    assert resolve_template("tmr") == "Ertaga ertalab"
    clock = tashkent_clock_text(
        datetime(2026, 11, 7, 11, 30, tzinfo=timezone.utc)
    )  # Toshkent UTC+5 → 16:30
    assert clock == "Bugun soat 16:30 da"


def test_normalize_custom_and_limit():
    assert normalize_departure("  Bugun soat 18:00 da  ") == "Bugun soat 18:00 da"
    assert normalize_departure("🕐 Hozir yuryapman") == "Hozir yuryapman"
    assert normalize_departure("x" * (MAX_CUSTOM_LEN + 1)) is None
    assert normalize_departure("") is None


def test_apply_departure_no_duplicate():
    body = "Beshariqdan 2 joy"
    once = apply_departure(body, "Hozir yuryapman")
    assert once.startswith("🕐 Hozir yuryapman")
    assert "Beshariqdan 2 joy" in once
    twice = apply_departure(once, "Hozir yuryapman")
    assert twice.count("🕐") == 1
    replaced = apply_departure(once, "1 soatdan keyin")
    assert "1 soatdan keyin" in replaced
    assert "Hozir yuryapman" not in replaced


def test_strip_and_has_line():
    text = "🕐 Hozir yuryapman\nCobalt joy bor"
    assert strip_departure_line(text) == "Cobalt joy bor"
    assert has_departure_line(text, "Hozir yuryapman")
    assert not has_departure_line("Cobalt joy bor", "Hozir yuryapman")


def test_compose_body_prepends_departure():
    driver = Driver(
        telegram_id=1,
        full_name="Test",
        car_model="Cobalt",
        car_number="40 A 123 BA",
        phone="+998901112233",
        username="drv",
        status=DriverStatus.ACTIVE.value,
    )
    out = compose_body(
        "Beshariq Toshkent 2 joy",
        driver,
        departure_text="Hozir yuryapman",
    )
    assert out.startswith("🕐 Hozir yuryapman")
    assert "Beshariq Toshkent 2 joy" in out
    assert "Bot orqali" in out
