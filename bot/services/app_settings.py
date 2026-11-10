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

# Kalitlar va defaultlar
SETTING_DEFAULTS: dict[str, Any] = {
    "payments_enabled": True,
    "driver_monthly_fee": None,  # None → env DRIVER_MONTHLY_FEE
    "grace_days": None,
    "ad_enabled": False,
    "ad_text": "Botimiz orqali taksi chaqiring — tez va qulay!",
    "ad_photo_file_id": "",
    "ad_interval_hours": 2,
    "ad_active_from": "07:00",
    "ad_active_to": "23:00",
    "main_group_id": None,  # None → config
    "ad_last_sent_at": "",
}

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


def ad_config() -> dict[str, Any]:
    return {
        "enabled": bool(get_cached("ad_enabled", False)),
        "text": str(get_cached("ad_text") or SETTING_DEFAULTS["ad_text"]),
        "photo_file_id": str(get_cached("ad_photo_file_id") or ""),
        "interval_hours": max(1, int(get_cached("ad_interval_hours") or 2)),
        "active_from": str(get_cached("ad_active_from") or "07:00"),
        "active_to": str(get_cached("ad_active_to") or "23:00"),
        "last_sent_at": str(get_cached("ad_last_sent_at") or ""),
    }


async def ensure_defaults(session: AsyncSession) -> None:
    existing = {
        row.key: row
        for row in (await session.execute(select(AppSetting))).scalars().all()
    }
    for key, default in SETTING_DEFAULTS.items():
        if key not in existing:
            # Env bilan boshlang'ich
            value = default
            if key == "payments_enabled":
                value = bool(settings.payments_enabled_default)
            elif key == "ad_interval_hours":
                value = int(settings.ad_interval_hours_default)
            elif key == "ad_active_from":
                value = settings.ad_active_from_default
            elif key == "ad_active_to":
                value = settings.ad_active_to_default
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
        logger.info("App settings loaded: payments=%s ad=%s", payments_enabled(), ad_config()["enabled"])
    except Exception:
        logger.exception("App settings yuklanmadi — default")
        _cache.clear()
        _cache.update(SETTING_DEFAULTS)
        _cache["payments_enabled"] = bool(settings.payments_enabled_default)
