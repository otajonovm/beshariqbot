"""Haydovchi to'lov eslatmalari va avto-blok."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.database.models import Driver, DriverStatus, PaymentRecord, utcnow
from bot.services import app_settings
from bot.services.notify import notify_admins

logger = logging.getLogger(__name__)
TZ_TASHKENT = ZoneInfo("Asia/Tashkent")


def admin_contact_text() -> str:
    raw = (settings.admin_contact or "").strip()
    if raw:
        return raw
    # Birinchi admin ID
    aids = settings.admin_ids
    if aids:
        return f"tg://user?id={aids[0]}"
    return "admin"


def driver_fee(driver: Driver) -> int:
    if driver.monthly_fee is not None and driver.monthly_fee >= 0:
        return int(driver.monthly_fee)
    return app_settings.monthly_fee_default()


def fmt_money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        from bot.database.models import TZ_UTC

        dt = dt.replace(tzinfo=TZ_UTC)
    return dt.astimezone(TZ_TASHKENT).strftime("%d.%m.%Y")


async def accept_payment(
    session: AsyncSession,
    driver: Driver,
    *,
    admin_id: int,
    days: int = 30,
    amount: int | None = None,
) -> PaymentRecord:
    """To'lov qabul: muddat uzayadi, to'lov bloki ochiladi."""
    now = utcnow()
    days = max(1, int(days))
    fee = amount if amount is not None else driver_fee(driver)
    due = driver._aware(driver.payment_due_at)
    base = due if due and due > now else now
    driver.payment_due_at = base + timedelta(days=days)
    driver.notified_pay_3 = False
    driver.notified_pay_1 = False
    driver.notified_pay_0 = False
    # To'lov uchun avto-blok ochiladi
    if driver.status == DriverStatus.BANNED.value and driver.block_reason == "payment":
        driver.status = DriverStatus.ACTIVE.value
        driver.block_reason = None
        driver.kicked_at = None
    rec = PaymentRecord(
        driver_id=driver.telegram_id,
        amount=fee,
        days=days,
        accepted_by=admin_id,
        note=f"+{days} kun",
    )
    session.add(rec)
    await session.flush()
    return rec


async def set_payment_due(
    session: AsyncSession,
    driver: Driver,
    due_at: datetime,
) -> Driver:
    driver.payment_due_at = due_at
    driver.notified_pay_3 = False
    driver.notified_pay_1 = False
    driver.notified_pay_0 = False
    await session.flush()
    return driver


async def list_payment_drivers(session: AsyncSession) -> list[Driver]:
    stmt = select(Driver).where(
        Driver.status.notin_(
            [
                DriverStatus.DELETED.value,
                DriverStatus.PENDING.value,
                DriverStatus.REJECTED.value,
            ]
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def check_driver_payments(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Kunlik: 3/1/0 kun eslatma + GRACE dan keyin avto-blok + admin hisobot."""
    if not app_settings.payments_enabled():
        return

    now = utcnow()
    today = now.astimezone(TZ_TASHKENT).date()
    grace = app_settings.grace_days()
    due_today: list[Driver] = []
    overdue: list[Driver] = []
    auto_blocked: list[Driver] = []

    async with session_maker() as session:
        drivers = await list_payment_drivers(session)

        for driver in drivers:
            if driver.payment_exempt:
                continue
            if driver.status == DriverStatus.BANNED.value and driver.block_reason != "payment":
                continue
            due = driver._aware(driver.payment_due_at)
            if due is None:
                continue

            due_date = due.astimezone(TZ_TASHKENT).date()
            days_left = (due_date - today).days
            fee = driver_fee(driver)
            fee_s = fmt_money(fee)
            due_s = due_date.strftime("%d.%m.%Y")

            if days_left == 3 and not driver.notified_pay_3:
                await _dm(
                    bot,
                    driver.telegram_id,
                    f"To'lov sanangiz 3 kundan keyin: <b>{due_s}</b>, summa <b>{fee_s}</b> so'm.",
                )
                driver.notified_pay_3 = True

            if days_left == 1 and not driver.notified_pay_1:
                await _dm(
                    bot,
                    driver.telegram_id,
                    f"Ertaga to'lov kuni: <b>{due_s}</b>, summa <b>{fee_s}</b> so'm. "
                    f"To'lov qilib adminga yozing: {admin_contact_text()}",
                )
                driver.notified_pay_1 = True

            if days_left == 0 and not driver.notified_pay_0:
                await _dm(
                    bot,
                    driver.telegram_id,
                    f"Bugun to'lov kuni: <b>{due_s}</b>, summa <b>{fee_s}</b> so'm. "
                    f"To'lov qilib adminga yozing: {admin_contact_text()}",
                )
                driver.notified_pay_0 = True
                due_today.append(driver)

            if days_left < 0:
                overdue.append(driver)
                # Grace tugadi → avto-blok
                if days_left <= -grace and driver.status != DriverStatus.BANNED.value:
                    from bot.services.driver_moderation import stop_driver_activity

                    driver.status = DriverStatus.BANNED.value
                    driver.block_reason = "payment"
                    driver.kicked_at = now
                    await session.flush()
                    await stop_driver_activity(session, driver.telegram_id)
                    await _dm(
                        bot,
                        driver.telegram_id,
                        "To'lov qilinmagani uchun vaqtincha bloklandingiz. "
                        f"To'lov qilib adminga yozing: {admin_contact_text()}",
                    )
                    auto_blocked.append(driver)

        await session.commit()

    # Admin kunlik hisobot
    if due_today or overdue or auto_blocked:
        lines = ["💳 <b>Kunlik to'lov hisoboti</b>\n"]
        if due_today:
            lines.append("<b>Bugun to'lashi kerak:</b>")
            for d in due_today[:20]:
                lines.append(f"• {d.full_name} · {fmt_money(driver_fee(d))} so'm")
        if overdue:
            lines.append("\n<b>Kechikkan:</b>")
            for d in overdue[:20]:
                lines.append(f"• {d.full_name} · {fmt_date(d.payment_due_at)}")
        if auto_blocked:
            lines.append("\n<b>Avto-blok:</b>")
            for d in auto_blocked[:20]:
                lines.append(f"• {d.full_name} <code>{d.telegram_id}</code>")
        await notify_admins(bot, "\n".join(lines))


async def _dm(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.info("To'lov xabari yetmadi: %s", chat_id)
