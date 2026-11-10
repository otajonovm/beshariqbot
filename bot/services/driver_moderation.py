"""Haydovchini bloklash / o'chirish / tiklash — faoliyatni to'xtatish."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.db_requests import stop_auto_post
from bot.database.models import Driver, DriverStatus, utcnow
from bot.keyboards.default_kb import main_menu_kb

logger = logging.getLogger(__name__)


async def stop_driver_activity(session: AsyncSession, driver_id: int) -> None:
    """Avto-e'lonni to'xtatish (e'lonlar darhol to'xtasin)."""
    try:
        await stop_auto_post(session, driver_id, reason="manual")
    except Exception:
        logger.debug("auto_post stop fail %s", driver_id, exc_info=True)


async def soft_delete_driver(session: AsyncSession, driver: Driver) -> Driver:
    driver.status = DriverStatus.DELETED.value
    driver.deleted_at = utcnow()
    driver.kicked_at = utcnow()
    await stop_driver_activity(session, driver.telegram_id)
    await session.flush()
    return driver


async def restore_driver(session: AsyncSession, driver: Driver) -> Driver:
    driver.status = DriverStatus.ACTIVE.value
    driver.deleted_at = None
    driver.kicked_at = None
    if driver.block_reason == "payment":
        # Tiklash to'lov blokini ochmaydi — alohida
        pass
    driver.block_reason = None
    await session.flush()
    return driver


async def ban_driver(
    session: AsyncSession,
    driver: Driver,
    *,
    reason: str = "admin",
) -> Driver:
    driver.status = DriverStatus.BANNED.value
    driver.block_reason = reason
    driver.kicked_at = utcnow()
    await stop_driver_activity(session, driver.telegram_id)
    await session.flush()
    return driver


async def unban_driver(session: AsyncSession, driver: Driver) -> Driver:
    if driver.status != DriverStatus.BANNED.value:
        return driver
    driver.status = DriverStatus.ACTIVE.value
    driver.block_reason = None
    driver.kicked_at = None
    await session.flush()
    return driver


async def notify_driver_blocked(
    bot: Bot,
    driver_id: int,
    *,
    reason_text: str,
) -> None:
    try:
        await bot.send_message(
            driver_id,
            reason_text,
            reply_markup=main_menu_kb(),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


async def notify_driver_unblocked(bot: Bot, driver_id: int) -> None:
    from bot.keyboards.default_kb import driver_panel_kb

    try:
        await bot.send_message(
            driver_id,
            "✅ Profilingiz qayta ochildi.",
            reply_markup=driver_panel_kb(),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
