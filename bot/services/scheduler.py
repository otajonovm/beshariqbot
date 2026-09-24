"""APScheduler: obuna eslatmalari (10/5/1 kun) va muddat tugashi."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.db_requests import (
    list_subscription_drivers,
    mark_driver_expired,
    mark_sub_notified,
)
from bot.database.models import DriverStatus, utcnow
from bot.services.notify import notify_admins

logger = logging.getLogger(__name__)

EXPIRED_TEXT = (
    "⛔ Obuna muddati tugaganligi sababli haydovchilar guruhidan chiqarildingiz.\n"
    "Qayta ulanish uchun to'lovni amalga oshiring va adminga yozing."
)

REMINDER_TEXTS = {
    10: (
        "⏰ Hurmatli haydovchi!\n\n"
        "Obunangiz tugashiga <b>10 kun</b> qoldi.\n"
        "Uzaytirish uchun admin bilan bog'laning."
    ),
    5: (
        "⏰ Hurmatli haydovchi!\n\n"
        "Obunangiz tugashiga <b>5 kun</b> qoldi.\n"
        "Uzaytirish uchun admin bilan bog'laning."
    ),
    1: (
        "⚠️ Hurmatli haydovchi!\n\n"
        "Obunangiz tugashiga <b>1 kun</b> qoldi!\n"
        "Ertaga muddat tugashi mumkin. Admin bilan bog'lanib to'lovni amalga oshiring."
    ),
}


async def _kick_from_group(bot: Bot, user_id: int) -> bool:
    """Guruhdan chiqarish: ban + unban (qayta qo'shilish imkoni saqlanadi)."""
    from bot.config import settings

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


async def _send_reminder(bot: Bot, driver, days: int, session_maker: async_sessionmaker) -> None:
    text = REMINDER_TEXTS[days]
    try:
        await bot.send_message(driver.telegram_id, text)
    except TelegramForbiddenError:
        logger.info("Obuna eslatmasi yetmadi (%s kun): %s", days, driver.telegram_id)
    async with session_maker() as session:
        await mark_sub_notified(session, driver.telegram_id, days)
    await notify_admins(
        bot,
        f"⏰ Obunaga <b>{days} kun</b> qoldi:\n"
        f"{driver.full_name} <code>{driver.telegram_id}</code>\n"
        f"🚘 {driver.car_model} · {driver.car_number}",
    )


async def check_driver_subscriptions(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Obuna 10/5/1 kun eslatmalari + muddati o'tganlarni kick."""
    async with session_maker() as session:
        drivers = await list_subscription_drivers(session)

    now = utcnow()
    for driver in drivers:
        if driver.status == DriverStatus.BANNED.value:
            continue

        until = driver._aware(driver.subscription_until)
        if until is None:
            continue

        if until > now:
            # Qolgan kunlar (to'liq kunlar)
            days_left = (until.date() - now.date()).days
            remaining = until - now

            # 1 kun: oxirgi 24 soat ichida yoki calendar 1 kun
            if remaining <= timedelta(days=1) and not driver.notified_sub_1:
                await _send_reminder(bot, driver, 1, session_maker)
                continue
            if days_left <= 5 and days_left >= 2 and not driver.notified_sub_5:
                await _send_reminder(bot, driver, 5, session_maker)
                continue
            if days_left <= 10 and days_left >= 6 and not driver.notified_sub_10:
                await _send_reminder(bot, driver, 10, session_maker)
            continue

        # Muddat tugagan
        if driver.status == DriverStatus.EXPIRED.value:
            continue

        kicked = await _kick_from_group(bot, driver.telegram_id)
        async with session_maker() as session:
            await mark_driver_expired(session, driver.telegram_id)
        try:
            await bot.send_message(driver.telegram_id, EXPIRED_TEXT)
        except TelegramForbiddenError:
            pass
        await notify_admins(
            bot,
            f"⛔ Obuna tugadi, haydovchi guruhdan chiqarildi"
            f"{' (API OK)' if kicked else ' (API xato)'}:\n"
            f"{driver.full_name} <code>{driver.telegram_id}</code>",
        )


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
