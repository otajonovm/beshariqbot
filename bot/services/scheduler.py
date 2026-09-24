"""APScheduler: to'langan obuna muddati tugaganda tekshiruv."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.database.db_requests import (
    list_subscription_drivers,
    mark_driver_expired,
)
from bot.database.models import DriverStatus, utcnow

logger = logging.getLogger(__name__)

EXPIRED_TEXT = (
    "⛔ Obuna muddati tugaganligi sababli haydovchilar guruhidan chiqarildingiz.\n"
    "Qayta ulanish uchun to'lovni amalga oshiring va adminga yozing."
)


async def _kick_from_group(bot: Bot, user_id: int) -> bool:
    """Guruhdan chiqarish: ban + unban (qayta qo'shilish imkoni saqlanadi)."""
    any_ok = False
    for chat_id in settings.group_ids:
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await bot.unban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                only_if_banned=True,
            )
            any_ok = True
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("Haydovchini guruhdan chiqarib bo'lmadi %s / %s: %s", user_id, chat_id, exc)
    return any_ok


async def check_driver_subscriptions(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Faqat muddati o'tgan to'langan obunalarni tekshiradi (sinov yo'q)."""
    async with session_maker() as session:
        drivers = await list_subscription_drivers(session)

    now = utcnow()
    for driver in drivers:
        if driver.status == DriverStatus.BANNED.value:
            continue
        if driver.has_paid_subscription(now):
            continue
        until = driver._aware(driver.subscription_until)
        if until is None:
            # Obuna hech qachon berilmagan — kick qilinmaydi
            continue
        if until > now:
            continue
        if driver.status == DriverStatus.EXPIRED.value:
            continue

        kicked = await _kick_from_group(bot, driver.telegram_id)
        async with session_maker() as session:
            await mark_driver_expired(session, driver.telegram_id)
        try:
            await bot.send_message(driver.telegram_id, EXPIRED_TEXT)
        except TelegramForbiddenError:
            pass
        try:
            await bot.send_message(
                settings.admin_id,
                f"⛔ Obuna tugadi, haydovchi guruhdan chiqarildi"
                f"{' (API OK)' if kicked else ' (API xato)'}:\n"
                f"{driver.full_name} <code>{driver.telegram_id}</code>",
            )
        except TelegramForbiddenError:
            pass


def setup_scheduler(bot: Bot, session_maker: async_sessionmaker) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    scheduler.add_job(
        check_driver_subscriptions,
        trigger="interval",
        hours=1,
        args=[bot, session_maker],
        id="subscription_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
