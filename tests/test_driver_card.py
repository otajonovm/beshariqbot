"""Haydovchi e'lon kartasi stil va telefon."""

from __future__ import annotations

from bot.database.models import Driver, DriverStatus
from bot.services.driver_card import (
    driver_announce_kb,
    format_driver_caption,
    format_when_label,
    normalize_uz_phone,
    phone_tel_url,
    resolve_announce_photo,
)


def _driver(**kwargs) -> Driver:
    base = dict(
        telegram_id=7001,
        full_name="Ali Valiyev",
        car_model="Cobalt",
        car_number="01 R 506 BB",
        phone="901112233",
        username="ali_drv",
        car_color="oq",
        photo_file_id="prof_photo",
        status=DriverStatus.ACTIVE.value,
        last_departure_text="Hozir yuryapman",
    )
    base.update(kwargs)
    return Driver(**base)


def test_normalize_phone_variants():
    assert normalize_uz_phone("901112233") == "+998901112233"
    assert normalize_uz_phone("+998901112233") == "+998901112233"
    assert normalize_uz_phone("998901112233") == "+998901112233"
    assert normalize_uz_phone("90 111 22 33") == "+998901112233"
    assert normalize_uz_phone("") is None


def test_when_label_short():
    assert format_when_label("Hozir yuryapman") == "HOZIRGA"
    assert format_when_label("30 daqiqadan keyin") == "30 DAQIQADAN KEYIN"


def test_caption_style_with_photo_fields():
    driver = _driver()
    text = format_driver_caption(
        driver,
        "Beshariqdan Toshkentga 3 joy bor\nPochta olamiz\nNarxi 200 ming",
        departure_text="Hozir yuryapman",
    )
    assert "👤 Haydovchi: <b>HOZIRGA</b>" in text
    assert "BESHARIQDAN TOSHKENTGA YURAMIZ" in text
    assert "3 ta JOY BOR" in text
    assert "Pochta olamiz" in text
    assert "AVTO COBALT" in text
    assert "Tel:<code>+998901112233</code>" in text
    assert "200 ming" in text or "Narxi" in text or "💰" in text
    assert "Ali Valiyev" in text
    assert "Cobalt 01 R 506 BB" in text
    # Vaqt va joy aralashmasin
    assert "Bo'sh joy:" not in text


def test_caption_without_phone_no_tel_line_style():
    driver = _driver(phone="—")
    text = format_driver_caption(driver, "Toshkentga joy bor", departure_text="Hozir")
    assert "☎️" not in text


def test_kb_with_phone_has_call_and_book():
    driver = _driver()
    kb = driver_announce_kb(driver)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    texts = [b.text for b in flat]
    assert any("Zakas berish" in (t or "") for t in texts)
    assert any(t and t.startswith("📞") for t in texts)
    call = next(b for b in flat if b.text and b.text.startswith("📞"))
    assert call.url and call.url.startswith("https://wa.me/")
    book = next(b for b in flat if b.text and "Zakas" in b.text)
    assert book.url and "start=book_7001" in book.url


def test_kb_without_phone_uses_write():
    driver = _driver(phone="")
    kb = driver_announce_kb(driver)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    assert any(b.text == "✍️ Yozish" for b in flat)


def test_resolve_photo_prefers_payload():
    driver = _driver()
    assert resolve_announce_photo(driver, "msg_photo") == "msg_photo"
    assert resolve_announce_photo(driver, None) == "prof_photo"
    driver.photo_file_id = None
    assert resolve_announce_photo(driver, None) is None


def test_phone_tel_url():
    assert phone_tel_url("+998901112233") == "https://wa.me/998901112233"
