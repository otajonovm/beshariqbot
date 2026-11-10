"""Super-admin feature flags + haydovchi premium override.

Qoida: global o'chiq → hech kimda ishlamaydi;
global yoqiq → haydovchining shaxsiy sozlamasi (default: yoqiq).
Premium muddati tugasa — premium funksiyalar o'chadi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from bot.database.models import Driver, FeatureFlag, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FlagMeta:
    key: str
    title: str
    hides: str  # qisqa: qaysi tugma/yashirinadi
    premium: bool = False


# Default: hammasi yoqilgan
FLAG_DEFS: tuple[FlagMeta, ...] = (
    FlagMeta("driver_auto_post", "Avto-e'lon", "⏰ Avto-e'lon", premium=True),
    FlagMeta("photo_announce", "Rasmli e'lon", "🖼 e'lon rasmi", premium=True),
    FlagMeta("driver_wizard", "E'lon wizard", "📢 E'lon berish"),
    FlagMeta("driver_raw_post", "Oxirgi e'lonni takrorlash", "🔁 takrorlash + lichka", premium=True),
    FlagMeta("multi_group", "Ko'p guruhga chiqarish", "👥 bir nechta guruh", premium=True),
    FlagMeta("passenger_dm", "So'rovlar lichkaga", "🚕 yo'lovchi so'rovi DM", premium=True),
    FlagMeta("driver_profile_edit", "Profil tahrir", "👤 Profilim"),
    FlagMeta("driver_photo_edit", "Rasm tahrir", "✏️ Rasm / 🖼 Rasmlar"),
    FlagMeta("driver_ask_time", "Ketish vaqtini so'rash", "🕐 vaqt tanlash"),
    FlagMeta("call_button", "Qo'ng'iroq tugmasi", "📞 guruhda", premium=True),
    FlagMeta("order_button", "Zakas tugmasi", "🚖 Zakas berish", premium=True),
    FlagMeta("passenger_relay", "Yo'lovchi relay", "guruh→asosiy relay"),
)

FLAG_KEYS: tuple[str, ...] = tuple(f.key for f in FLAG_DEFS)
FLAG_BY_KEY: dict[str, FlagMeta] = {f.key: f for f in FLAG_DEFS}
PREMIUM_KEYS: tuple[str, ...] = tuple(f.key for f in FLAG_DEFS if f.premium)

_DEFAULT_ON = True
_cache: dict[str, bool] = {k: _DEFAULT_ON for k in FLAG_KEYS}
_loaded = False


def is_enabled(key: str) -> bool:
    """Sinxron o'qish (kesh). Noma'lum kalit → True."""
    if key not in FLAG_KEYS:
        return True
    return bool(_cache.get(key, _DEFAULT_ON))


def snapshot() -> dict[str, bool]:
    return {k: bool(_cache.get(k, _DEFAULT_ON)) for k in FLAG_KEYS}


def apply_snapshot(values: dict[str, bool]) -> None:
    global _loaded
    for k in FLAG_KEYS:
        if k in values:
            _cache[k] = bool(values[k])
        else:
            _cache.setdefault(k, _DEFAULT_ON)
    _loaded = True


def _driver_overrides(driver: Driver | None) -> dict[str, bool]:
    if driver is None:
        return {}
    raw = getattr(driver, "feature_overrides", None) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, bool] = {}
    for k, v in raw.items():
        out[str(k)] = bool(v)
    return out


def premium_active(driver: Driver | None, now: datetime | None = None) -> bool:
    """Premium paket muddati bor va hali tugamagan."""
    if driver is None:
        return False
    return bool(driver.has_active_premium(now))


def driver_feature_enabled(
    key: str,
    driver: Driver | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Global ∧ (premium muddati/override).

    - Global o'chiq → False
    - Premium kalit va muddat berilgan lekin tugagan → False
    - Override bo'lsa shu qiymat
    - Aks holda True (migratsiya: hech narsa o'chmasin)
    """
    if not is_enabled(key):
        return False
    if driver is None:
        return True
    meta = FLAG_BY_KEY.get(key)
    now = now or utcnow()
    # Premium paket muddati aniq berilgan va tugagan
    until = driver._aware(driver.premium_until) if hasattr(driver, "_aware") else driver.premium_until
    if meta and meta.premium and until is not None and until <= now:
        return False
    overrides = _driver_overrides(driver)
    if key in overrides:
        return bool(overrides[key])
    return True


async def set_driver_override(
    session: AsyncSession,
    driver: Driver,
    key: str,
    enabled: bool,
) -> Driver:
    data = dict(_driver_overrides(driver))
    data[key] = bool(enabled)
    driver.feature_overrides = data
    flag_modified(driver, "feature_overrides")
    await session.flush()
    return driver


async def set_premium_package(
    session: AsyncSession,
    driver: Driver,
    *,
    days: int | None,
    enable: bool,
) -> Driver:
    """Premium paket: barcha premium funksiyalar birdan + muddat."""
    now = utcnow()
    data = dict(_driver_overrides(driver))
    if enable:
        for key in PREMIUM_KEYS:
            data[key] = True
        base = driver.premium_until
        if base is not None and getattr(base, "tzinfo", None) is None:
            from bot.database.models import TZ_UTC

            base = base.replace(tzinfo=TZ_UTC)
        if base is not None and base > now:
            start = base
        else:
            start = now
        from datetime import timedelta

        driver.premium_until = start + timedelta(days=max(1, int(days or 30)))
        driver.notified_premium_end = False
    else:
        for key in PREMIUM_KEYS:
            data[key] = False
        driver.premium_until = now
        driver.notified_premium_end = True
    driver.feature_overrides = data
    flag_modified(driver, "feature_overrides")
    await session.flush()
    return driver


async def expire_premium_if_needed(
    session: AsyncSession,
    driver: Driver,
    now: datetime | None = None,
) -> bool:
    """Muddat tugagan premiumni o'chiradi. True = hozir o'chirildi."""
    now = now or utcnow()
    until = driver._aware(driver.premium_until)
    if until is None or until > now:
        return False
    if driver.notified_premium_end:
        return False
    data = dict(_driver_overrides(driver))
    for key in PREMIUM_KEYS:
        data[key] = False
    driver.feature_overrides = data
    flag_modified(driver, "feature_overrides")
    driver.notified_premium_end = True
    await session.flush()
    return True


async def ensure_defaults(session: AsyncSession) -> None:
    existing = {
        row.key: row
        for row in (
            await session.execute(select(FeatureFlag).where(FeatureFlag.key.in_(FLAG_KEYS)))
        ).scalars()
    }
    for meta in FLAG_DEFS:
        if meta.key not in existing:
            session.add(FeatureFlag(key=meta.key, enabled=True))
    await session.flush()


async def load_flags(session: AsyncSession) -> dict[str, bool]:
    await ensure_defaults(session)
    rows = (
        await session.execute(select(FeatureFlag).where(FeatureFlag.key.in_(FLAG_KEYS)))
    ).scalars().all()
    values = {k: _DEFAULT_ON for k in FLAG_KEYS}
    for row in rows:
        values[row.key] = bool(row.enabled)
    apply_snapshot(values)
    return values


async def set_flag(
    session: AsyncSession,
    key: str,
    enabled: bool,
) -> FeatureFlag | None:
    if key not in FLAG_KEYS:
        return None
    await ensure_defaults(session)
    row = await session.get(FeatureFlag, key)
    if row is None:
        row = FeatureFlag(key=key, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
        row.updated_at = utcnow()
    await session.flush()
    _cache[key] = bool(enabled)
    return row


async def bootstrap_flags(session_maker: async_sessionmaker) -> None:
    try:
        async with session_maker() as session:
            await load_flags(session)
            await session.commit()
        logger.info("Feature flags loaded: %s", snapshot())
    except Exception:
        logger.exception("Feature flags yuklanmadi — default ON")
        apply_snapshot({k: True for k in FLAG_KEYS})


def flags_status_lines(values: dict[str, bool] | None = None) -> str:
    vals = values or snapshot()
    lines = ["⭐ <b>Premium / funksiyalar</b>\n"]
    for meta in FLAG_DEFS:
        mark = "✅" if vals.get(meta.key, True) else "❌"
        star = "⭐ " if meta.premium else ""
        lines.append(f"{mark} {star}<b>{meta.title}</b>")
    lines.append("\nTugmani bosing: yoqish ↔ o'chirish.")
    return "\n".join(lines)


def enabled_map_for_panel(driver: Driver | None = None) -> dict[str, bool]:
    """Panel klaviatura uchun qulay dict (haydovchi override bilan)."""
    return {
        "auto_post": driver_feature_enabled("driver_auto_post", driver),
        "wizard": driver_feature_enabled("driver_wizard", driver),
        "raw_post": driver_feature_enabled("driver_raw_post", driver),
        "profile": driver_feature_enabled("driver_profile_edit", driver),
        "photo": driver_feature_enabled("driver_photo_edit", driver),
        "ask_time": driver_feature_enabled("driver_ask_time", driver),
        "call_button": driver_feature_enabled("call_button", driver),
        "order_button": driver_feature_enabled("order_button", driver),
        "relay": driver_feature_enabled("passenger_relay", driver),
        "photo_announce": driver_feature_enabled("photo_announce", driver),
        "multi_group": driver_feature_enabled("multi_group", driver),
        "passenger_dm": driver_feature_enabled("passenger_dm", driver),
    }


def premium_status_lines(driver: Driver) -> str:
    now = utcnow()
    until = driver._aware(driver.premium_until)
    if until and until > now:
        until_s = until.astimezone().strftime("%d.%m.%Y")
        head = f"⭐ Premium: <b>bor</b> (gacha {until_s})"
    else:
        head = "⭐ Premium: <b>yo'q</b>"
    lines = [head, ""]
    for key in PREMIUM_KEYS:
        meta = FLAG_BY_KEY[key]
        on = driver_feature_enabled(key, driver, now=now)
        mark = "✅" if on else "❌"
        lines.append(f"{mark} {meta.title}")
    return "\n".join(lines)
