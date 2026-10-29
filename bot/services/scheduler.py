"""APScheduler: obuna eslatmalari, 3 minutlik claim timeout va gemifikatsiya reytinglari."""

from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.database.db_requests import (
    expire_claimed_order,
    list_expired_claimed_orders,
    list_subscription_drivers,
    mark_driver_expired,
    mark_sub_notified,
    set_group_posts,
)
from bot.database.models import DriverStatus, utcnow
from bot.services.leaderboard import (
    run_daily_leaderboard_job,
    run_monthly_leaderboard_job,
    run_weekly_leaderboard_job,
)
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


async def check_order_claim_timeouts(bot: Bot, session_maker: async_sessionmaker) -> None:
    """3 daqiqa ichida 'Ha, oldim' bosilmagan buyurtmalarni qayta guruhga chiqarish."""
    async with session_maker() as session:
        expired = await list_expired_claimed_orders(session, timeout_seconds=180)

    if not expired:
        return

    for exp in expired:
        order_id = exp.id
        old_driver_id = exp.driver_id
        claim_msg_id = exp.claim_message_id
        passenger_id = exp.passenger_id

        async with session_maker() as session:
            updated_order = await expire_claimed_order(session, order_id)
            if updated_order is None:
                continue

            from bot.handlers.group import edit_group_card
            from bot.keyboards.inline_kb import claim_kb

            await edit_group_card(
                bot,
                updated_order,
                updated_order.to_group_text(reactivated=True),
                reply_markup=claim_kb(updated_order.id),
            )
            await set_group_posts(session, updated_order.id, updated_order.posts_map())
            await session.commit()

        if old_driver_id:
            if claim_msg_id:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=old_driver_id,
                        message_id=claim_msg_id,
                        reply_markup=None,
                    )
                except (TelegramBadRequest, TelegramForbiddenError):
                    pass
            try:
                await bot.send_message(
                    chat_id=old_driver_id,
                    text=(
                        f"⏰ <b>Vaqt tugadi!</b>\n\n"
                        f"Siz <b>3 daqiqa</b> ichida <b>#{order_id}</b> buyurtmani tasdiqlamadingiz "
                        f"(«Ha, oldim» tugmasi bosilmadi).\n"
                        f"Buyurtma qaytadan haydovchilar guruhiga chiqarildi."
                    ),
                )
            except (TelegramBadRequest, TelegramForbiddenError):
                pass

        if passenger_id:
            try:
                await bot.send_message(
                    chat_id=passenger_id,
                    text=(
                        f"ℹ️ Haydovchi 3 daqiqa ichida buyurtmangizni tasdiqlamadi.\n"
                        f"<b>#{order_id}</b> buyurtmangiz qaytadan haydovchilar guruhiga chiqarildi."
                    ),
                )
            except (TelegramBadRequest, TelegramForbiddenError):
                pass


def setup_scheduler(bot: Bot, session_maker: async_sessionmaker) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    # 1. Obuna muddati va eslatmalar (har soatda)
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
    # 2. 3 daqiqalik claim timeout (har 10 soniyada)
    scheduler.add_job(
        check_order_claim_timeouts,
        trigger="interval",
        seconds=10,
        args=[bot, session_maker],
        id="order_claim_timeout_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # 3. Kunlik gemifikatsiya reytingi (har kuni soat 21:30 da)
    scheduler.add_job(
        run_daily_leaderboard_job,
        trigger="cron",
        hour=21,
        minute=30,
        args=[bot, session_maker],
        id="daily_leaderboard_cron",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # 4. Haftalik gemifikatsiya reytingi (har yakshanba soat 22:00 da)
    scheduler.add_job(
        run_weekly_leaderboard_job,
        trigger="cron",
        day_of_week="sun",
        hour=22,
        minute=0,
        args=[bot, session_maker],
        id="weekly_leaderboard_cron",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # 5. Oylik gemifikatsiya reytingi (har oyning 1-kuni soat 10:00 da o'tgan oyni sarhisob qiladi)
    scheduler.add_job(
        run_monthly_leaderboard_job,
        trigger="cron",
        day=1,
        hour=10,
        minute=0,
        args=[bot, session_maker],
        id="monthly_leaderboard_cron",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
