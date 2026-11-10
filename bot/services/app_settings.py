"""DB sozlamalari — kesh bilan, o'zgarish darhol kuchga kiradi."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.database.models import AppSetting, utcnow

logger = logging.getLogger(__name__)

DEFAULT_AD_CAPTION = (
    "🚕 <b>BESHARIQ ⇄ TOSHKENT</b>\n"
    "\n"
    "Mashinani qidiryapsizmi?\n"
    "📱 Bot orqali <b>tezda</b> toping!\n"
    "\n"
    "✅ Tez va qulay\n"
    "🛡 Ishonchli haydovchilar\n"
    "💰 Qulay narxlar\n"
    "\n"
    "👇 <b>MASHINA TOPISH</b>"
)

DEFAULT_AD_BUTTON = "🚕 Taksi buyurtma berish"

# Kalitlar va defaultlar
SETTING_DEFAULTS: dict[str, Any] = {
    "payments_enabled": True,
    "driver_monthly_fee": None,
    "grace_days": None,
    "ad_enabled": False,
    "ad_text": DEFAULT_AD_CAPTION,
    "ad_photo_file_id": "",
    "ad_interval_hours": 2,  # legacy
    "ad_interval_sec": 7200,  # 2 soat
    "ad_duration_sec": 0,  # 0 = doimiy
    "ad_ends_at": "",
    "ad_button_text": DEFAULT_AD_BUTTON,
    "ad_dispatcher_phone": "",
    "ad_main_group_enabled": False,  # 426 Global alohida
    "ad_active_from": "07:00",
    "ad_active_to": "23:00",
    "ad_last_sent_at": "",
    "main_group_id": None,
}

# Interval tanlovlari (soniya)
AD_INTERVAL_CHOICES: tuple[int, ...] = (
    30,
    60,
    120,
    300,
    600,
    1800,
    3600,
    7200,
    10800,
)

# Davomiylik: 0 = doimiy
AD_DURATION_CHOICES: tuple[int, ...] = (0, 3600, 7200, 10800)

_cache: dict[str, Any] = {}
_loaded = False


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _deserialize(raw: str) -> Any:
    if raw == "":
        return ""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def get_cached(key: str, default: Any = None) -> Any:
    if key in _cache:
        return _cache[key]
    if key in SETTING_DEFAULTS:
        return SETTING_DEFAULTS[key]
    return default


def payments_enabled() -> bool:
    return bool(get_cached("payments_enabled", True))


def monthly_fee_default() -> int:
    cached = get_cached("driver_monthly_fee")
    if cached is None or cached == "":
        return max(0, int(settings.driver_monthly_fee))
    return max(0, int(cached))


def grace_days() -> int:
    cached = get_cached("grace_days")
    if cached is None or cached == "":
        return max(0, int(settings.grace_days))
    return max(0, int(cached))


def resolved_main_group_id() -> int:
    cached = get_cached("main_group_id")
    if cached is None or cached == "" or cached == 0:
        return settings.resolved_main_group_id
    return int(cached)


def resolve_ad_interval_sec() -> int:
    """Soniyada. Eski soatli qiymat bo'lsa — o'tkazadi."""
    raw_sec = get_cached("ad_interval_sec")
    if raw_sec is not None and raw_sec != "":
        try:
            sec = int(raw_sec)
            if sec > 0:
                # Agar kimdir daqiqa saqlagan bo'lsa (1..180) — soniyaga
                if sec <= 180 and sec not in AD_INTERVAL_CHOICES and sec < 30:
                    sec = sec * 60
                return max(30, sec)
        except (TypeError, ValueError):
            pass
    hours = get_cached("ad_interval_hours")
    try:
        h = int(hours or 0)
    except (TypeError, ValueError):
        h = 0
    if h > 0:
        return max(30, h * 3600)
    return 7200


def format_interval_human(sec: int) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec} s"
    if sec < 3600:
        m = sec // 60
        return f"{m} min"
    h = sec // 3600
    return f"{h} soat"


def format_duration_human(sec: int) -> str:
    if not sec:
        return "Doimiy (faol soatlarda)"
    return format_interval_human(sec)


def ad_config() -> dict[str, Any]:
    interval = resolve_ad_interval_sec()
    try:
        duration = int(get_cached("ad_duration_sec") or 0)
    except (TypeError, ValueError):
        duration = 0
    text = str(get_cached("ad_text") or DEFAULT_AD_CAPTION)
    if not text.strip():
        text = DEFAULT_AD_CAPTION
    return {
        "enabled": bool(get_cached("ad_enabled", False)),
        "text": text,
        "photo_file_id": str(get_cached("ad_photo_file_id") or ""),
        "interval_sec": interval,
        "interval_hours": max(1, interval // 3600) if interval >= 3600 else 1,  # legacy UI
        "duration_sec": max(0, duration),
        "ends_at": str(get_cached("ad_ends_at") or ""),
        "button_text": str(get_cached("ad_button_text") or DEFAULT_AD_BUTTON),
        "dispatcher_phone": str(get_cached("ad_dispatcher_phone") or ""),
        "main_group_enabled": bool(get_cached("ad_main_group_enabled", False)),
        "active_from": str(get_cached("ad_active_from") or "07:00"),
        "active_to": str(get_cached("ad_active_to") or "23:00"),
        "last_sent_at": str(get_cached("ad_last_sent_at") or ""),
    }


async def ensure_defaults(session: AsyncSession) -> None:
    existing = {
        row.key: row
        for row in (await session.execute(select(AppSetting))).scalars().all()
    }
    # Legacy soat → soniya migratsiya (bir marta)
    if "ad_interval_sec" not in existing and "ad_interval_hours" in existing:
        try:
            hours = int(_deserialize(existing["ad_interval_hours"].value) or 2)
        except (TypeError, ValueError):
            hours = 2
        session.add(AppSetting(key="ad_interval_sec", value=_serialize(max(30, hours * 3600))))
        existing["ad_interval_sec"] = True  # type: ignore[assignment]

    for key, default in SETTING_DEFAULTS.items():
        if key not in existing:
            value = default
            if key == "payments_enabled":
                value = bool(settings.payments_enabled_default)
            elif key == "ad_interval_hours":
                value = int(settings.ad_interval_hours_default)
            elif key == "ad_interval_sec":
                value = max(30, int(settings.ad_interval_hours_default) * 3600)
            elif key == "ad_active_from":
                value = settings.ad_active_from_default
            elif key == "ad_active_to":
                value = settings.ad_active_to_default
            elif key == "ad_text":
                value = DEFAULT_AD_CAPTION
            session.add(AppSetting(key=key, value=_serialize(value)))
    await session.flush()


async def load_settings(session: AsyncSession) -> dict[str, Any]:
    global _loaded
    await ensure_defaults(session)
    rows = (await session.execute(select(AppSetting))).scalars().all()
    values = dict(SETTING_DEFAULTS)
    for row in rows:
        values[row.key] = _deserialize(row.value)
    _cache.clear()
    _cache.update(values)
    _loaded = True
    return dict(_cache)


async def set_setting(session: AsyncSession, key: str, value: Any) -> AppSetting:
    row = await session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=_serialize(value))
        session.add(row)
    else:
        row.value = _serialize(value)
        row.updated_at = utcnow()
    await session.flush()
    _cache[key] = value
    return row


async def bootstrap_settings(session_maker: async_sessionmaker) -> None:
    try:
        async with session_maker() as session:
            await load_settings(session)
            await session.commit()
        logger.info(
            "App settings loaded: payments=%s ad=%s interval=%ss",
            payments_enabled(),
            ad_config()["enabled"],
            ad_config()["interval_sec"],
        )
    except Exception:
        logger.exception("App settings yuklanmadi — default")
        _cache.clear()
        _cache.update(SETTING_DEFAULTS)
        _cache["payments_enabled"] = bool(settings.payments_enabled_default)
