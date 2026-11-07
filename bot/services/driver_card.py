"""Guruhdagi haydovchi e'lon kartasi — raqobatchi uslubidagi caption + tugmalar."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.database.models import Driver
from bot.services.departure import normalize_departure, strip_departure_line

CAPTION_LIMIT = 1024

_SEATS_RE = re.compile(
    r"(?P<n>\d{1,2})\s*(?:ta\s+)?(?:joy|o'?rin|orin)(?:lar)?(?:\s*bor)?",
    re.IGNORECASE,
)
_SEATS_ALT = re.compile(r"(?P<n>\d{1,2})\s*ta\b", re.IGNORECASE)
_PRICE_RE = re.compile(
    r"(?:narx[i]?|cena|price)\s*[:\-]?\s*([^\n]{2,40})"
    r"|(\d{2,3}\s*(?:ming|k)\b)"
    r"|(\d{5,7}\s*(?:so'?m|sum|uzs)?\b)",
    re.IGNORECASE,
)
_PARCEL_RE = re.compile(
    r"\b(pochta|posilka|yuk|gruz|посылк|почта)\b"
    r"|pochta\s+olamiz|yuk\s+olamiz|posilka\s+olamiz",
    re.IGNORECASE,
)
_PHONE_DIGITS = re.compile(r"\D+")

_WHEN_SHORT = {
    "hozir yuryapman": "HOZIRGA",
    "hozir": "HOZIRGA",
    "30 daqiqadan keyin": "30 DAQIQADAN KEYIN",
    "1 soatdan keyin": "1 SOATDAN KEYIN",
    "2 soatdan keyin": "2 SOATDAN KEYIN",
    "bugun kechqurun": "BUGUN KECHQURUN",
    "ertaga ertalab": "ERTAGA ERTALAB",
}


@dataclass(slots=True)
class ParsedAnnounce:
    when: str
    route: str
    seats: str | None
    parcel: str | None
    price: str | None
    car_line: str
    phone_e164: str | None
    name: str
    plate_line: str


def normalize_uz_phone(raw: str | None) -> str | None:
    """90... / 998... / +998... → +998XXXXXXXXX."""
    if not raw:
        return None
    digits = _PHONE_DIGITS.sub("", raw.strip())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("998") and len(digits) == 12:
        return f"+{digits}"
    if len(digits) == 9 and digits[0] == "9":
        return f"+998{digits}"
    if len(digits) == 12 and digits.startswith("998"):
        return f"+{digits}"
    if digits.startswith("998") and len(digits) > 12:
        return f"+{digits[:12]}"
    return None


def phone_tel_url(e164: str) -> str:
    """Qo'ng'iroq uchun URL. Bot API http(s) talab qiladi — tel: ishlamaydi.

    wa.me mobil mijozda ochiladi; captionda Tel:+998 ham ko'rinadi.
    """
    digits = e164.lstrip("+")
    return f"https://wa.me/{digits}"


def format_when_label(departure: str | None) -> str:
    dep = normalize_departure(departure or "") or "Hozir"
    key = dep.lower()
    if key in _WHEN_SHORT:
        return _WHEN_SHORT[key]
    if key.startswith("bugun soat"):
        return dep.upper()
    return dep.upper()


def _detect_route(text: str, direction: str | None = None) -> str:
    if direction == "beshariq_tashkent":
        return "BESHARIQDAN TOSHKENTGA YURAMIZ"
    if direction == "tashkent_beshariq":
        return "TOSHKENTDAN BESHARIQGA YURAMIZ"
    low = (text or "").lower().replace("'", "")
    has_b = "beshariq" in low or "besharik" in low or "besharyk" in low
    has_t = "toshkent" in low or "tashkent" in low or "taskent" in low
    if has_b and has_t:
        # Qaysi tomonga?
        b_pos = min(
            (low.find(x) for x in ("beshariq", "besharik", "besharyk") if low.find(x) >= 0),
            default=9999,
        )
        t_pos = min(
            (low.find(x) for x in ("toshkent", "tashkent", "taskent") if low.find(x) >= 0),
            default=9999,
        )
        if "beshariqdan" in low or "beshariqdan" in low.replace("'", ""):
            return "BESHARIQDAN TOSHKENTGA YURAMIZ"
        if "toshkentdan" in low or "tashkentdan" in low:
            return "TOSHKENTDAN BESHARIQGA YURAMIZ"
        if b_pos <= t_pos:
            return "BESHARIQDAN TOSHKENTGA YURAMIZ"
        return "TOSHKENTDAN BESHARIQGA YURAMIZ"
    if has_b:
        return "BESHARIQ YO'NALISHI"
    if has_t:
        return "TOSHKENT YO'NALISHI"
    return "BESHARIQ — TOSHKENT"


def _detect_seats(text: str, seats_hint: str | None = None) -> str | None:
    if seats_hint:
        hint = seats_hint.strip()
        if hint.isdigit():
            return f"{hint} ta JOY BOR"
        low = hint.lower()
        if "butun" in low or "salon" in low:
            return "BUTUN SALON"
        m = re.search(r"\d{1,2}", hint)
        if m:
            return f"{m.group(0)} ta JOY BOR"
        return hint.upper()
    m = _SEATS_RE.search(text or "")
    if m:
        return f"{m.group('n')} ta JOY BOR"
    m2 = _SEATS_ALT.search(text or "")
    if m2 and re.search(r"\bjoy\b", (text or "").lower()):
        return f"{m2.group('n')} ta JOY BOR"
    if re.search(r"\bjoy\s+bor\b", (text or "").lower()):
        return "JOY BOR"
    return None


def _detect_parcel(text: str) -> str | None:
    if not text or not _PARCEL_RE.search(text):
        return None
    low = text.lower()
    if "olamiz" in low or "olaman" in low:
        return "Pochta olamiz"
    return "Pochta bor"


def _detect_price(text: str) -> str | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    chunk = next((g for g in m.groups() if g), None)
    if not chunk:
        return None
    return " ".join(chunk.strip().split())


def parse_announce(
    driver: Driver,
    raw_text: str,
    *,
    departure_text: str | None = None,
    seats_hint: str | None = None,
    direction: str | None = None,
) -> ParsedAnnounce:
    body = strip_departure_line(raw_text or "")
    phone = normalize_uz_phone(driver.phone)
    car = (driver.car_model or "AVTO").strip()
    color = (driver.car_color or "").strip()
    car_line = f"AVTO {car.upper()}"
    if color:
        car_line = f"{car_line} ({color})"
    plate = (driver.car_number or "").strip()
    plate_line = f"{car} {plate}".strip() if plate else car

    return ParsedAnnounce(
        when=format_when_label(departure_text or driver.last_departure_text),
        route=_detect_route(body, direction),
        seats=_detect_seats(body, seats_hint),
        parcel=_detect_parcel(body),
        price=_detect_price(body),
        car_line=car_line,
        phone_e164=phone,
        name=(driver.full_name or "Haydovchi").strip(),
        plate_line=plate_line,
    )


def format_driver_caption(
    driver: Driver,
    raw_text: str,
    *,
    departure_text: str | None = None,
    seats_hint: str | None = None,
    direction: str | None = None,
) -> str:
    """Namuna stilidagi caption (HTML)."""
    p = parse_announce(
        driver,
        raw_text,
        departure_text=departure_text,
        seats_hint=seats_hint,
        direction=direction,
    )
    lines = [
        f"👤 Haydovchi: <b>{escape(p.when)}</b>",
        f"📍 <b>{escape(p.route)}</b>",
    ]
    if p.seats:
        lines.append(f"👥 <b>{escape(p.seats)}</b>")
    if p.parcel:
        lines.append(f"📦 {escape(p.parcel)}")
    lines.append(f"🚖 <b>{escape(p.car_line)}</b>")
    if p.phone_e164:
        lines.append(f"☎️ Tel:<code>{escape(p.phone_e164)}</code>")
    if p.price:
        lines.append(f"💰 {escape(p.price)}")
    lines.append(f"👤 {escape(p.name)}")
    lines.append(f"🚗 {escape(p.plate_line)}")
    if p.phone_e164:
        lines.append(escape(p.phone_e164))

    caption = "\n".join(lines)
    if len(caption) > CAPTION_LIMIT:
        caption = caption[: CAPTION_LIMIT - 1] + "…"
    return caption


def resolve_announce_photo(
    driver: Driver,
    photo_file_id: str | None,
) -> str | None:
    """E'lon rasmi → profil rasmi → None."""
    if photo_file_id:
        return photo_file_id
    pid = (driver.photo_file_id or "").strip()
    return pid or None


def driver_announce_kb(driver: Driver) -> InlineKeyboardMarkup:
    """🚖 Zakas berish + 📞 tel (telefon bo'lmasa — ✍️ Yozish)."""
    bot_user = settings.bot_username.lstrip("@")
    book_url = f"https://t.me/{bot_user}?start=book_{driver.telegram_id}"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🚖 Zakas berish", url=book_url)],
    ]
    phone = normalize_uz_phone(driver.phone)
    if phone:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📞 {phone}",
                    url=phone_tel_url(phone),
                )
            ]
        )
    else:
        from bot.relay.card import contact_url

        rows.append(
            [
                InlineKeyboardButton(
                    text="✍️ Yozish",
                    url=contact_url(driver.telegram_id, driver.username),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
