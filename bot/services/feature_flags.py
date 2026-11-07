"""Super-admin feature flags — o'chiq funksiya tugmalari umuman chiqmasin."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.models import FeatureFlag, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FlagMeta:
    key: str
    title: str
    hides: str  # qisqa: qaysi tugma/yashirinadi


# Default: hammasi yoqilgan
FLAG_DEFS: tuple[FlagMeta, ...] = (
    FlagMeta("driver_auto_post", "Avto-e'lon", "⏰ Avto-e'lon"),
    FlagMeta("driver_wizard", "E'lon wizard", "📢 E'lon berish"),
    FlagMeta("driver_raw_post", "Xom e'lon (DM)", "lichkaga tashlash + 🔁 takrorlash"),
    FlagMeta("driver_profile_edit", "Profil tahrir", "👤 Profilim"),
    FlagMeta("driver_photo_edit", "Rasm tahrir", "✏️ Rasm / 🖼 Rasmlar"),
    FlagMeta("driver_ask_time", "Ketish vaqtini so'rash", "🕐 vaqt tanlash"),
    FlagMeta("call_button", "Qo'ng'iroq tugmasi", "📞 guruhda"),
    FlagMeta("order_button", "Zakas tugmasi", "🚖 Zakas berish"),
    FlagMeta("passenger_relay", "Yo'lovchi relay", "guruh→asosiy relay"),
)

FLAG_KEYS: tuple[str, ...] = tuple(f.key for f in FLAG_DEFS)
FLAG_BY_KEY: dict[str, FlagMeta] = {f.key: f for f in FLAG_DEFS}

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
    lines = ["🎛 <b>Funksiyalar</b>\n"]
    for meta in FLAG_DEFS:
        mark = "✅" if vals.get(meta.key, True) else "❌"
        lines.append(f"{mark} <b>{meta.title}</b> — yashiradi: {meta.hides}")
    lines.append("\nTugmani bosing: yoqish ↔ o'chirish.")
    return "\n".join(lines)


def enabled_map_for_panel() -> dict[str, bool]:
    """Panel klaviatura uchun qulay dict."""
    return {
        "auto_post": is_enabled("driver_auto_post"),
        "wizard": is_enabled("driver_wizard"),
        "raw_post": is_enabled("driver_raw_post"),
        "profile": is_enabled("driver_profile_edit"),
        "photo": is_enabled("driver_photo_edit"),
        "ask_time": is_enabled("driver_ask_time"),
        "call_button": is_enabled("call_button"),
        "order_button": is_enabled("order_button"),
        "relay": is_enabled("passenger_relay"),
    }
