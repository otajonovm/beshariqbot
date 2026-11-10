"""Bot reklamasi: per-group interval, banner+caption, rate-limit, 429."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import BASE_DIR, settings
from bot.database.models import AdClick, AdSendLog, BotGroup, utcnow
from bot.services import app_settings
from bot.services.chat_rate_limit import wait_chat_slot
from bot.services.notify import notify_admins

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Tashkent")
BANNER_PATH = BASE_DIR / "assets" / "ad_banner.jpg"
CAPTION_LIMIT = 1024

_bot_username_cache: str | None = None


async def resolve_bot_username(bot: Bot) -> str:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        me = await bot.get_me()
        if me.username:
            _bot_username_cache = me.username.lstrip("@")
            return _bot_username_cache
    except Exception:
        logger.debug("getMe username fail", exc_info=True)
    return settings.bot_username.lstrip("@")


def default_caption() -> str:
    return app_settings.DEFAULT_AD_CAPTION


def ad_keyboard(
    *,
    bot_username: str | None = None,
    button_text: str | None = None,
    dispatcher_phone: str | None = None,
) -> InlineKeyboardMarkup:
    cfg = app_settings.ad_config()
    user = (bot_username or settings.bot_username).lstrip("@")
    label = (button_text if button_text is not None else cfg["button_text"]) or (
        app_settings.DEFAULT_AD_BUTTON
    )
    url = f"https://t.me/{user}?start=order"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=label[:64], url=url)]
    ]
    phone = (
        dispatcher_phone
        if dispatcher_phone is not None
        else cfg.get("dispatcher_phone") or ""
    ).strip()
    if phone:
        digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
        if digits:
            tel = digits if digits.startswith("+") else f"+{digits}"
            rows.append(
                [InlineKeyboardButton(text="📞 Dispetcher", url=f"tel:{tel}")]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    return minutes >= start or minutes < end


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt


def caption_ok(text: str) -> tuple[bool, str]:
    t = (text or "").strip()
    if len(t) > CAPTION_LIMIT:
        return False, f"Matn {CAPTION_LIMIT} belgidan oshmasin (hozir {len(t)})."
    return True, t


def resolve_banner_source(photo_file_id: str | None) -> str | Path | None:
    """file_id | Path | None (faqat matn)."""
    fid = (photo_file_id or "").strip()
    if fid:
        return fid
    if BANNER_PATH.is_file():
        return BANNER_PATH
    return None


async def list_ad_groups(session: AsyncSession) -> list[BotGroup]:
    """Bot admin bo'lgan guruh + kanallar (flag_bot_ads o'chiq = istisno).

    426 Global: flag yoqiq yoki «426 Global reklama» yoqiq bo'lsa.
    """
    from bot.services.bot_groups import ensure_known_groups

    await ensure_known_groups(session)
    cfg = app_settings.ad_config()
    main_id = app_settings.resolved_main_group_id()
    stmt = select(BotGroup).where(BotGroup.approved.is_(True))
    rows = list((await session.execute(stmt)).scalars().all())
    out: list[BotGroup] = []
    for g in rows:
        # Huquq yo'qligi aniq bilinsa — o'tkazib yubor
        if g.can_post is False:
            continue
        is_main = g.is_main or g.chat_id == main_id
        if is_main and not g.is_channel():
            if g.flag_bot_ads or cfg["main_group_enabled"]:
                out.append(g)
            continue
        # Guruh yoki kanal: reklama flagi yoqiq (default ON)
        if g.flag_bot_ads:
            out.append(g)
    return out


async def schedule_groups(
    session: AsyncSession,
    *,
    from_now: bool = True,
    stagger: bool = True,
) -> None:
    """Yoqilganda next_run_at ni belgilash."""
    now = datetime.now(TZ)
    groups = await list_ad_groups(session)
    for i, g in enumerate(groups):
        delay = (1.5 * i) if stagger else 0.0
        g.ad_next_run_at = now + timedelta(seconds=delay) if from_now else g.ad_next_run_at
        g.updated_at = utcnow()
    await session.flush()


def _effective_interval(group: BotGroup, base_sec: int, now: datetime) -> int:
    boost = _aware(group.ad_boost_until)
    if boost and boost > now:
        return max(30, base_sec * 2)
    return max(30, base_sec)


async def _delete_old(bot: Bot, group: BotGroup) -> None:
    mid = group.last_ad_message_id
    if not mid:
        return
    try:
        await bot.delete_message(group.chat_id, mid)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info("Eski reklama o'chmadi chat=%s mid=%s: %s", group.chat_id, mid, exc)
    except Exception:
        logger.exception("Eski reklama o'chirish xato chat=%s", group.chat_id)


async def _send_ad_message(
    bot: Bot,
    chat_id: int,
    caption: str,
    banner: str | Path | None,
    kb: InlineKeyboardMarkup,
) -> Message:
    cap = caption[:CAPTION_LIMIT]
    if banner is None:
        return await bot.send_message(chat_id, cap, reply_markup=kb)
    if isinstance(banner, Path):
        photo = FSInputFile(str(banner))
        return await bot.send_photo(chat_id, photo=photo, caption=cap, reply_markup=kb)
    return await bot.send_photo(chat_id, photo=str(banner), caption=cap, reply_markup=kb)


async def send_one_group(
    bot: Bot,
    session: AsyncSession,
    group: BotGroup,
    *,
    force: bool = False,
) -> bool:
    """Bitta guruhga reklama. True = muvaffaqiyat."""
    cfg = app_settings.ad_config()
    now = datetime.now(TZ)
    username = await resolve_bot_username(bot)
    kb = ad_keyboard(bot_username=username)
    caption = cfg["text"]
    banner = resolve_banner_source(cfg["photo_file_id"])
    chat_id = group.chat_id

    await wait_chat_slot(chat_id)
    await _delete_old(bot, group)

    try:
        msg = await _send_ad_message(bot, chat_id, caption, banner, kb)
    except TelegramRetryAfter as exc:
        wait = float(exc.retry_after) + 0.2
        logger.warning("Ad 429 chat=%s wait=%s — interval 2x", chat_id, wait)
        await asyncio.sleep(wait)
        # Intervalni vaqtincha 2 barobar
        base = cfg["interval_sec"]
        group.ad_boost_until = now + timedelta(seconds=base * 2)
        group.ad_next_run_at = now + timedelta(seconds=_effective_interval(group, base, now))
        session.add(
            AdSendLog(
                chat_id=chat_id,
                message_id=None,
                ok=False,
                error=f"429:{exc.retry_after}",
            )
        )
        await session.flush()
        try:
            await wait_chat_slot(chat_id)
            msg = await _send_ad_message(bot, chat_id, caption, banner, kb)
        except Exception as exc2:
            logger.info("Ad 429 qayta urinish fail chat=%s: %s", chat_id, exc2)
            return False
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info("Ad yuborilmadi chat=%s: %s", chat_id, exc)
        session.add(
            AdSendLog(
                chat_id=chat_id,
                message_id=None,
                ok=False,
                error=str(exc)[:250],
            )
        )
        if not group.ad_permission_warned:
            group.ad_permission_warned = True
            await notify_admins(
                bot,
                f"⚠️ Reklama yuborilmadi: <b>{group.title or chat_id}</b>\n"
                f"ID: <code>{chat_id}</code>\n"
                f"Bot admin emas yoki rasm/xabar huquqi yo'q.\n"
                f"<code>{exc}</code>",
            )
        # Keyingi urinish — oddiy interval
        base = cfg["interval_sec"]
        group.ad_next_run_at = now + timedelta(seconds=_effective_interval(group, base, now))
        await session.flush()
        return False
    except Exception as exc:
        logger.exception("Ad unexpected chat=%s", chat_id)
        session.add(
            AdSendLog(chat_id=chat_id, message_id=None, ok=False, error=str(exc)[:250])
        )
        await session.flush()
        return False

    group.last_ad_message_id = msg.message_id
    group.ad_permission_warned = False
    base = cfg["interval_sec"]
    group.ad_next_run_at = now + timedelta(seconds=_effective_interval(group, base, now))
    session.add(
        AdSendLog(chat_id=chat_id, message_id=msg.message_id, ok=True, error=None)
    )
    await app_settings.set_setting(session, "ad_last_sent_at", now.isoformat())
    await session.flush()
    return True


async def send_ad_to_groups(
    bot: Bot,
    session: AsyncSession,
    *,
    force: bool = False,
) -> tuple[int, int]:
    """Barcha reklama guruhlariga (jitter bilan). force=Hozir yuborish."""
    cfg = app_settings.ad_config()
    if not force and not cfg["enabled"]:
        return 0, 0
    groups = await list_ad_groups(session)
    if not groups:
        return 0, 0
    ok = fail = 0
    for i, group in enumerate(groups):
        if i > 0:
            await asyncio.sleep(1.0 + random.random())  # 1–2 s jitter
        success = await send_one_group(bot, session, group, force=force)
        if success:
            ok += 1
        else:
            fail += 1
    return ok, fail


async def check_bot_ads(bot: Bot, session_maker: async_sessionmaker) -> None:
    """Har 5–10 s: muddat, faol soat, due guruhlar."""
    cfg = app_settings.ad_config()
    now = datetime.now(TZ)

    # Davomiylik tugashi
    ends_raw = cfg.get("ends_at") or ""
    if cfg["enabled"] and ends_raw:
        try:
            ends = datetime.fromisoformat(ends_raw)
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=TZ)
            if ends <= now:
                async with session_maker() as session:
                    await app_settings.set_setting(session, "ad_enabled", False)
                    await app_settings.set_setting(session, "ad_ends_at", "")
                    await session.commit()
                await notify_admins(
                    bot,
                    "⏸ Reklama muddati tugadi — avtomatik to'xtatildi.",
                )
                return
        except ValueError:
            pass

    if not cfg["enabled"]:
        return
    if not in_active_window(now):
        return

    async with session_maker() as session:
        groups = await list_ad_groups(session)
        due: list[BotGroup] = []
        for g in groups:
            nxt = _aware(g.ad_next_run_at)
            if nxt is None or nxt <= now:
                due.append(g)
        if not due:
            return
        # Bir tickda ko'pi bilan bir nechta, jitter bilan
        ok = fail = 0
        for i, group in enumerate(due[:8]):
            if i > 0:
                await asyncio.sleep(1.0 + random.random())
            if await send_one_group(bot, session, group, force=False):
                ok += 1
            else:
                fail += 1
        await session.commit()
    if ok or fail:
        logger.info("Bot ads tick ok=%s fail=%s", ok, fail)


async def send_preview_to_admin(bot: Bot, admin_id: int) -> None:
    """Adminning o'ziga guruhdagidek post."""
    cfg = app_settings.ad_config()
    username = await resolve_bot_username(bot)
    kb = ad_keyboard(bot_username=username)
    banner = resolve_banner_source(cfg["photo_file_id"])
    await _send_ad_message(bot, admin_id, cfg["text"], banner, kb)


async def record_ad_click(session: AsyncSession, user_id: int) -> None:
    session.add(AdClick(user_id=user_id))
    await session.flush()


async def ad_stats(session: AsyncSession, *, days: int = 1) -> tuple[int, int]:
    """(bosishlar, buyurtmalar source=ad)."""
    from bot.database.models import Order

    start = utcnow() - timedelta(days=max(1, days))
    clicks = int(
        (
            await session.execute(
                select(func.count(AdClick.id)).where(AdClick.created_at >= start)
            )
        ).scalar_one()
        or 0
    )
    orders = int(
        (
            await session.execute(
                select(func.count(Order.id)).where(
                    Order.created_at >= start,
                    Order.source == "ad",
                )
            )
        ).scalar_one()
        or 0
    )
    return clicks, orders


def status_summary_text(*, group_count: int = 0) -> str:
    cfg = app_settings.ad_config()
    on = "🟢 Yoqilgan" if cfg["enabled"] else "🔴 O'chiq"
    ends = cfg["ends_at"]
    ends_s = "—"
    if ends:
        try:
            dt = datetime.fromisoformat(ends)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            ends_s = dt.astimezone(TZ).strftime("%d.%m %H:%M")
        except ValueError:
            ends_s = ends
    last = cfg["last_sent_at"] or "—"
    if last != "—":
        try:
            dt = datetime.fromisoformat(last)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            last = dt.astimezone(TZ).strftime("%d.%m %H:%M")
        except ValueError:
            pass
    banner = "bor" if (cfg["photo_file_id"] or BANNER_PATH.is_file()) else "yo'q"
    text_preview = (cfg["text"] or "").replace("\n", " ")[:80]
    return (
        f"📣 <b>Reklama</b>\n\n"
        f"Holat: {on}\n"
        f"Interval: <b>{app_settings.format_interval_human(cfg['interval_sec'])}</b>\n"
        f"Davomiylik: <b>{app_settings.format_duration_human(cfg['duration_sec'])}</b>"
        + (f" (tugash: {ends_s})" if cfg["duration_sec"] and cfg["enabled"] else "")
        + f"\nFaol soatlar: {cfg['active_from']}–{cfg['active_to']}\n"
        f"Banner: {banner}\n"
        f"Matn: <i>{text_preview}</i>\n"
        f"Guruh/kanallar: <b>{group_count}</b>\n"
        f"426 Global: {'✅' if cfg['main_group_enabled'] else '❌'}\n"
        f"Oxirgi yuborilgan: {last}"
    )


# Eski API mosligi
async def preview_text() -> str:
    return status_summary_text()
