"""Gemifikatsiya usulidagi kunlik, haftalik va oylik haydovchilar statistikasi va reytingi."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.database.db_requests import (
    LeaderboardData,
    LeaderboardEntry,
    get_leaderboard_data,
)
from bot.services.notify import notify_admins

logger = logging.getLogger(__name__)

TZ_TASHKENT = ZoneInfo("Asia/Tashkent")

RANK_BADGES = {
    1: ("🥇", "👑 <b>Oltin Chavandoz (Kun Qahramoni)</b>"),
    2: ("🥈", "⚡ <b>Tezkor Qoplon (Kumush Lider)</b>"),
    3: ("🥉", "🔥 <b>Bronza Ustasi (Uchar Qanot)</b>"),
    4: ("4️⃣", "🎖 <b>Yo'l Barslari</b>"),
    5: ("5️⃣", "🎖 <b>Chaqqon Haydovchi</b>"),
    6: ("6️⃣", "⭐ <b>Faol Haydovchi</b>"),
    7: ("7️⃣", "⭐ <b>Faol Haydovchi</b>"),
    8: ("8️⃣", "⭐ <b>Faol Haydovchi</b>"),
    9: ("9️⃣", "⭐ <b>Faol Haydovchi</b>"),
    10: ("🔟", "⭐ <b>Faol Haydovchi</b>"),
}


def _format_driver_mention(driver_id: int, full_name: str, username: str | None) -> str:
    name = (full_name or "Haydovchi").strip()
    safe_name = html.escape(name)
    mention = f'<a href="tg://user?id={driver_id}">{safe_name}</a>'
    if username:
        clean_user = username.lstrip("@").strip()
        if clean_user:
            mention += f" (@{clean_user})"
    return mention


def get_daily_bounds(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    now = now or datetime.now(TZ_TASHKENT)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    label = now.strftime("%d.%m.%Y")
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        label,
    )


def get_weekly_bounds(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    now = now or datetime.now(TZ_TASHKENT)
    start_local = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    label = f"{start_local.strftime('%d.%m.%Y')} — {end_local.strftime('%d.%m.%Y')}"
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        label,
    )


def get_monthly_bounds(
    now: datetime | None = None, *, previous_month: bool = False
) -> tuple[datetime, datetime, str]:
    now = now or datetime.now(TZ_TASHKENT)
    if previous_month:
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_of_prev_month = first_of_this_month - timedelta(days=1)
        start_local = last_of_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = last_of_prev_month.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        start_local = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    months_uz = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentyabr", 10: "Oktyabr", 11: "Noyabr", 12: "Dekabr"
    }
    label = f"{months_uz.get(start_local.month, '')} {start_local.year}"
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        label,
    )


def render_leaderboard_text(
    data: LeaderboardData,
    period_type: str,
    period_label: str,
) -> str:
    """Gemifikatsiya uslubidagi chiroyli va qiziqarli reyting matnini hosil qiladi."""
    if period_type == "daily":
        header = (
            f"🏆 <b>KUNNING ENG ZO'R HAYDOVCHILARI!</b>\n"
            f"📅 <b>Sana:</b> {period_label}\n\n"
            f"Bugun <b>Beshariq — Toshkent</b> yo'nalishida eng ko'p buyurtma bajargan "
            f"va peshqadamlikni qo'ldan bermagan qahramonlarimiz:\n\n"
        )
        footer = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Bugungi umumiy natijalar:</b>\n"
            f"• Bajarilgan jami safarlar: <b>{data.total_orders} ta</b>\n"
            f"• 🚖 Taksi: <b>{data.total_taxi}</b> | 📦 Pochta: <b>{data.total_parcel}</b>\n\n"
            f"🔥 <i>Barcha peshqadamlarni qizg'in tabriklaymiz! Ertaga 1-o'rinni kim egallaydi? "
            f"Barchaga omad va oq yo'l! 🚖💨</i>"
        )
    elif period_type == "weekly":
        header = (
            f"🌟 <b>HAFTANING ENG KUCHLI HAYDOVCHILARI!</b>\n"
            f"📅 <b>Davr:</b> {period_label}\n\n"
            f"O'tgan 7 kun davomida eng faol, chaqqon va ishonchli xizmat ko'rsatgan "
            f"haftaning mutlaq yetakchilari:\n\n"
        )
        footer = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Haftalik umumiy natijalar:</b>\n"
            f"• Bajarilgan jami safarlar: <b>{data.total_orders} ta</b>\n"
            f"• 🚖 Taksi: <b>{data.total_taxi}</b> | 📦 Pochta: <b>{data.total_parcel}</b>\n\n"
            f"🌟 <i>Haftaning eng kuchli haydovchilariga tashakkur! Yangi haftada yangi marralar sari! 🚀</i>"
        )
    else:  # monthly
        header = (
            f"👑 <b>OY CHEMPIONLARI — GRAND REYTING!</b>\n"
            f"📅 <b>Oy:</b> {period_label}\n\n"
            f"Oy davomida tinimsiz xizmat qilib, haqiqiy rekord o'rnatgan "
            f"yo'llar afsonalari:\n\n"
        )
        footer = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Oylik umumiy natijalar:</b>\n"
            f"• Bajarilgan jami safarlar: <b>{data.total_orders} ta</b>\n"
            f"• 🚖 Taksi: <b>{data.total_taxi}</b> | 📦 Pochta: <b>{data.total_parcel}</b>\n\n"
            f"👑 <i>Oy qahramonlariga cheksiz hurmat va ehtirom! Karvon Taxi jamoasi siz bilan faxrlanadi! 🏆👏</i>"
        )

    if not data.top_drivers:
        empty_text = (
            f"{header}"
            f"Bu davrda hali yakunlangan safarlar qayd etilmadi.\n\n"
            f"🚀 <i>Yangi buyurtmalarni birinchi bo'lib qabul qiling va yetakchilar safidan joy oling! Oq yo'l! 🚖</i>"
        )
        return empty_text

    driver_blocks: list[str] = []
    for rank, d in enumerate(data.top_drivers, start=1):
        badge, title = RANK_BADGES.get(rank, (f"{rank}️⃣", "⭐ <b>Faol Haydovchi</b>"))
        mention = _format_driver_mention(d.driver_id, d.full_name, d.username)
        car_info = f"{d.car_model} ({d.car_number})" if d.car_number and d.car_number != "—" else d.car_model
        block = (
            f"{badge} {title}\n"
            f"👤 Haydovchi: {mention}\n"
            f"🚘 Mashina: <b>{car_info}</b>\n"
            f"📊 Natija: <b>{d.orders_count} ta safar</b> "
            f"(🚖 {d.taxi_count} taksi | 📦 {d.parcel_count} pochta)"
        )
        driver_blocks.append(block)

    return header + "\n\n".join(driver_blocks) + "\n\n" + footer


async def get_leaderboard_text_by_type(
    session: AsyncSession,
    period_type: str = "daily",
    limit: int = 10,
    previous_month: bool = False,
) -> str:
    """Kunlik, haftalik yoki oylik reyting matnini tayyorlaydi."""
    if period_type == "weekly":
        start_utc, end_utc, label = get_weekly_bounds()
    elif period_type == "monthly":
        start_utc, end_utc, label = get_monthly_bounds(previous_month=previous_month)
    else:
        start_utc, end_utc, label = get_daily_bounds()
        period_type = "daily"

    data = await get_leaderboard_data(session, start_utc, end_utc, limit=limit)
    return render_leaderboard_text(data, period_type, label)


async def publish_leaderboard_to_group(bot: Bot, text: str) -> bool:
    """Asosiy haydovchilar guruhiga reytingni e'lon qiladi."""
    group_id = settings.primary_group_id
    try:
        await bot.send_message(
            chat_id=group_id,
            text=text,
            disable_web_page_preview=True,
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.exception("Guruhga reyting yuborilmadi %s: %s", group_id, exc)
        await notify_admins(bot, f"⚠️ Guruhga reyting yuborilmadi (ID: {group_id}):\n<code>{exc}</code>")
        return False


async def run_daily_leaderboard_job(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Avtomatik har kuni kechki vaqtda kunlik natijalarni guruhga e'lon qiladi."""
    async with session_maker() as session:
        text = await get_leaderboard_text_by_type(session, "daily")
    await publish_leaderboard_to_group(bot, text)
    await notify_admins(bot, "✅ Kunlik reyting haydovchilar guruhiga e'lon qilindi.")


async def run_weekly_leaderboard_job(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Avtomatik har yakshanba kechqurun haftalik natijalarni guruhga e'lon qiladi."""
    async with session_maker() as session:
        text = await get_leaderboard_text_by_type(session, "weekly")
    await publish_leaderboard_to_group(bot, text)
    await notify_admins(bot, "✅ Haftalik reyting haydovchilar guruhiga e'lon qilindi.")


async def run_monthly_leaderboard_job(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Avtomatik har oyning 1-kuni o'tgan oy natijalarini guruhga e'lon qiladi."""
    async with session_maker() as session:
        text = await get_leaderboard_text_by_type(session, "monthly", previous_month=True)
    await publish_leaderboard_to_group(bot, text)
    await notify_admins(bot, "✅ Oylik reyting haydovchilar guruhiga e'lon qilindi.")
