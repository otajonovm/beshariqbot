"""Haydovchi ketish vaqti shablonlari (Asia/Tashkent)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    TASHKENT = ZoneInfo("Asia/Tashkent")
except Exception:  # Windows / tzdata yo'q
    TASHKENT = timezone(timedelta(hours=5))

DEPARTURE_PREFIX = "🕐 "
MAX_CUSTOM_LEN = 80

# callback suffix → matn
DEPARTURE_TEMPLATES: dict[str, str] = {
    "now": "Hozir yuryapman",
    "30m": "30 daqiqadan keyin",
    "1h": "1 soatdan keyin",
    "2h": "2 soatdan keyin",
    "eve": "Bugun kechqurun",
    "tmr": "Ertaga ertalab",
}


def tashkent_clock_text(now: datetime | None = None) -> str:
    local = (now or datetime.now(TASHKENT)).astimezone(TASHKENT)
    return f"Bugun soat {local.strftime('%H:%M')} da"


def normalize_departure(text: str) -> str | None:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return None
    if cleaned.startswith(DEPARTURE_PREFIX):
        cleaned = cleaned[len(DEPARTURE_PREFIX) :].strip()
    if not cleaned or len(cleaned) > MAX_CUSTOM_LEN:
        return None
    return cleaned


def strip_departure_line(body: str) -> str:
    """Birinchi 🕐 qatorini olib tashlash (takrorlash uchun asl matn)."""
    lines = (body or "").splitlines()
    if not lines:
        return ""
    first = lines[0].strip()
    if first.startswith("🕐") or first.lower().startswith("ketish:"):
        return "\n".join(lines[1:]).lstrip("\n")
    return body


def has_departure_line(body: str, departure: str | None = None) -> bool:
    dep = (departure or "").strip().lower()
    for line in (body or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith("🕐"):
            if not dep or dep in raw.lower():
                return True
        if dep and raw.lower() == dep:
            return True
    return False


def apply_departure(body: str, departure: str | None) -> str:
    """Vaqt qatorini yuqoriga qo'shadi; dublikat qilmaydi."""
    dep = normalize_departure(departure or "") if departure else None
    if not dep:
        return (body or "").rstrip()
    text = (body or "").rstrip()
    if has_departure_line(text, dep):
        return text
    # Eski 🕐 qatori bo'lsa — almashtiramiz
    if text and text.lstrip().startswith("🕐"):
        text = strip_departure_line(text)
    line = f"{DEPARTURE_PREFIX}{dep}"
    if text:
        return f"{line}\n{text}"
    return line


def resolve_template(code: str) -> str | None:
    if code == "clock":
        return tashkent_clock_text()
    return DEPARTURE_TEMPLATES.get(code)
