"""Bot reklamasi: interval, faol vaqt, eski xabarni o'chirish."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import settings
from bot.database.models import BotGroup, utcnow
from bot.services import app_settings

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Tashkent")


def ad_keyboard() -> InlineKeyboardMarkup:
    bot_user = settings.bot_username.lstrip("@")
    url = f"https://t.me/{bot_user}?start=order"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚕 Taksi chaqirish", url=url)]
        ]
    )


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = (value or "07:00").strip().split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 7, 0
    return max(0, min(23, h)), max(0, min(59, m))


def in_active_window(now: datetime | None = None) -> bool:
    cfg = app_settings.ad_config()
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    else:
        now = now.astimezone(TZ)
    fh, fm = _parse_hhmm(cfg["active_from"])
    th, tm = _parse_hhmm(cfg["active_to"])
    minutes = now.hour * 60 + now.minute
    start = fh * 60 + fm
    end = th * 60 + tm
    if start <= end:
        return start <= minutes < end
    # Tun orqali (masalan 22:00–06:00)
    return minutes >= start or minutes < end


def due_for_send(now: datetime | None = None) -> bool:
    cfg = app_settings.ad_config()
    if not cfg["enabled"]:
        return False
    if not in_active_window(now):
        return False
    last_raw = cfg.get("last_sent_at") or ""
    if not last_raw:
        return True
    try:
        last = datetime.fromisoformat(last_raw)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=TZ)
    now = now or datetime.now(TZ)
    hours = max(1, int(cfg["interval_hours"]))
    return (now - last).total_seconds() >= hours * 3600


async def list_ad_groups(session: AsyncSession) -> list[BotGroup]:
    stmt = select(BotGroup).where(
        BotGroup.flag_bot_ads.is_(True),
        BotGroup.approved.is_(True),
    )
    return list((await session.execute(stmt)).scalars().all())


async def send_ad_to_groups(
    bot: Bot,
    session: AsyncSession,
    *,
    force: bool = False,
) -> tuple[int, int]:
    """Reklama yuboradi, eski xabarni o'chiradi. (ok, fail)"""
    cfg = app_settings.ad_config()
    if not force and not cfg["enabled"]:
        return 0, 0
    text = (cfg["text"] or "").strip() or "Botimiz orqali taksi chaqiring!"
    photo = (cfg["photo_file_id"] or "").strip() or None
    kb = ad_keyboard()
    groups = await list_ad_groups(session)
    if not groups:
        # Fallback: config guruhlari (asosiy emas), agar DB bo'sh
        from bot.config import settings as cfg_s

        ok = fail = 0
        for chat_id in cfg_s.resolved_driver_post_group_ids:
            try:
                msg = await _send_one(bot, chat_id, text, photo, kb)
                if msg:
                    ok += 1
                else:
                    fail += 1
            except TelegramRetryAfter as exc:
                logger.warning("Ad 429: wait %s", exc.retry_after)
                fail += 1
            except Exception:
                fail += 1
        return ok, fail

    ok = fail = 0
    for group in groups:
        try:
            # Eski reklama
            if group.last_ad_message_id:
                try:
                    await bot.delete_message(group.chat_id, group.last_ad_message_id)
                except (TelegramBadRequest, TelegramForbiddenError):
                    pass
            msg = await _send_one(bot, group.chat_id, text, photo, kb)
            if msg:
                group.last_ad_message_id = msg.message_id
                ok += 1
            else:
                fail += 1
        except TelegramRetryAfter as exc:
            logger.warning("Ad 429 chat=%s wait=%s", group.chat_id, exc.retry_after)
            fail += 1
        except Exception:
            logger.exception("Ad send fail %s", group.chat_id)
            fail += 1
    await session.flush()
    return ok, fail


async def _send_one(bot: Bot, chat_id: int, text: str, photo: str | None, kb):
    try:
        if photo:
            return await bot.send_photo(
                chat_id, photo=photo, caption=text[:1024], reply_markup=kb
            )
        return await bot.send_message(chat_id, text, reply_markup=kb)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info("Ad yuborilmadi %s: %s", chat_id, exc)
        return None


async def check_bot_ads(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Har daqiqada: muddat/vaqt bo'lsa yuboradi."""
    if not due_for_send():
        return
    async with session_maker() as session:
        ok, fail = await send_ad_to_groups(bot, session, force=False)
        if ok or fail:
            await app_settings.set_setting(
                session, "ad_last_sent_at", datetime.now(TZ).isoformat()
            )
        await session.commit()
    logger.info("Bot ads sent ok=%s fail=%s", ok, fail)


async def preview_text() -> str:
    cfg = app_settings.ad_config()
    return (
        f"📣 <b>Reklama oldindan ko'rish</b>\n\n"
        f"{cfg['text']}\n\n"
        f"Interval: har {cfg['interval_hours']} soat\n"
        f"Vaqt: {cfg['active_from']}–{cfg['active_to']}\n"
        f"Holat: {'✅ yoqilgan' if cfg['enabled'] else '❌ o‘chiq'}"
    )
