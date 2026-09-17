"""APScheduler: 5/7/8 kunlik trial va obuna tekshiruvi."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.database.db_requests import (
    list_trial_drivers,
    mark_driver_expired,
    mark_driver_notified,
)
from bot.database.models import (
    TRIAL_DAYS,
    TRIAL_KICK_DAY,
    TRIAL_REMINDER_DAY,
    DriverStatus,
    utcnow,
)

logger = logging.getLogger(__name__)

DAY5_TEXT = (
    "⏱ Hurmatli haydovchi!\n\n"
    "Sinov muddatingiz tugashiga <b>2 kun</b> qoldi.\n"
    "7-kundan so'ng oylik obuna to'lashingiz kerak bo'ladi. "
    "To'lovni admin orqali amalga oshirasiz."
)

DAY7_TEXT = (
    "💳 Sinov muddati tugadi.\n\n"
    "Oylik obunani to'lash uchun admin bilan bog'laning. "
    "To'lov tasdiqlanmaguncha ertaga guruhdan chiqarilishingiz mumkin."
)

DAY8_TEXT = (
    "⛔ Obuna to'lanmagani sababli haydovchilar guruhidan chiqarildingiz.\n"
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
    async with session_maker() as session:
        drivers = await list_trial_drivers(session)

    now = utcnow()
    for driver in drivers:
        if driver.status == DriverStatus.BANNED.value:
            continue
        if driver.has_paid_subscription(now):
            continue
        if driver.status == DriverStatus.EXPIRED.value:
            continue

        elapsed = driver.trial_elapsed(now)

        if elapsed >= timedelta(days=TRIAL_KICK_DAY):
            kicked = await _kick_from_group(bot, driver.telegram_id)
            async with session_maker() as session:
                await mark_driver_expired(session, driver.telegram_id)
            try:
                await bot.send_message(driver.telegram_id, DAY8_TEXT)
            except TelegramForbiddenError:
                pass
            try:
                await bot.send_message(
                    settings.admin_id,
                    f"⛔ Haydovchi guruhdan chiqarildi"
                    f"{' (API OK)' if kicked else ' (API xato)'}:\n"
                    f"{driver.full_name} <code>{driver.telegram_id}</code>",
                )
            except TelegramForbiddenError:
                pass
            continue

        if elapsed >= timedelta(days=TRIAL_DAYS) and not driver.notified_day7:
            try:
                await bot.send_message(driver.telegram_id, DAY7_TEXT)
            except TelegramForbiddenError:
                logger.info("Day7 xabar yetmadi: %s", driver.telegram_id)
            async with session_maker() as session:
                await mark_driver_notified(session, driver.telegram_id, 7)
            try:
                await bot.send_message(
                    settings.admin_id,
                    f"💳 Sinov tugadi, to'lov kutilmoqda:\n"
                    f"{driver.full_name} <code>{driver.telegram_id}</code>",
                )
            except TelegramForbiddenError:
                pass
            continue

        if elapsed >= timedelta(days=TRIAL_REMINDER_DAY) and not driver.notified_day5:
            try:
                await bot.send_message(driver.telegram_id, DAY5_TEXT)
            except TelegramForbiddenError:
                logger.info("Day5 xabar yetmadi: %s", driver.telegram_id)
            async with session_maker() as session:
                await mark_driver_notified(session, driver.telegram_id, 5)


def setup_scheduler(bot: Bot, session_maker: async_sessionmaker) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    scheduler.add_job(
        check_driver_subscriptions,
        trigger="interval",
        hours=1,
        args=[bot, session_maker],
        id="trial_subscription_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
