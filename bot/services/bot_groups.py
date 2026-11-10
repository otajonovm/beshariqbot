"""Guruh flaglari va asosiy guruh."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Chat
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import BotGroup, utcnow
from bot.services import app_settings

logger = logging.getLogger(__name__)


async def upsert_group(
    session: AsyncSession,
    chat_id: int,
    *,
    title: str = "",
    approved: bool | None = None,
    chat_type: str | None = None,
) -> BotGroup:
    row = await session.get(BotGroup, chat_id)
    main_id = app_settings.resolved_main_group_id()
    ctype = (chat_type or "supergroup").strip().lower()
    if ctype not in {"group", "supergroup", "channel"}:
        ctype = "supergroup"
    is_channel = ctype == "channel"
    if row is None:
        # Yangi: reklama default ON; kanalda faqat reklama (relay/e'lon o'chiq)
        is_main = chat_id == main_id and not is_channel
        row = BotGroup(
            chat_id=chat_id,
            title=title or "",
            chat_type=ctype,
            flag_relay_source=not is_main and not is_channel,
            flag_driver_posts=not is_main and not is_channel,
            flag_auto_posts=not is_main and not is_channel,
            flag_bot_ads=True,
            flag_delete_original=(
                False if is_channel else chat_id in settings.resolved_sweep_group_ids
            ),
            is_main=is_main,
            approved=True if approved is None else bool(approved),
        )
        session.add(row)
    else:
        if title:
            row.title = title
        if chat_type:
            row.chat_type = ctype
        if approved is not None:
            row.approved = bool(approved)
        row.updated_at = utcnow()
    await session.flush()
    return row


async def ensure_known_groups(session: AsyncSession) -> None:
    """Config + ko'rilgan guruhlarni DB ga seed; reklamani default yoqadi."""
    main_id = app_settings.resolved_main_group_id()
    chat_ids = list(settings.group_ids)
    # Relay ko'rgan guruhlar ham
    try:
        from bot.relay.service import seen_group_ids

        for cid in seen_group_ids():
            if cid not in chat_ids:
                chat_ids.append(cid)
    except Exception:
        pass
    for chat_id in chat_ids:
        await upsert_group(session, chat_id, approved=True)
    # Asosiy belgi
    await session.execute(update(BotGroup).values(is_main=False))
    row = await session.get(BotGroup, main_id)
    if row:
        row.is_main = True
        row.flag_driver_posts = False
        row.flag_auto_posts = False
    # Bir martalik: barcha tasdiqlangan guruhlarda reklama yoqilsin
    if not app_settings.get_cached("ad_all_admin_groups_v1"):
        await session.execute(
            update(BotGroup)
            .where(BotGroup.approved.is_(True))
            .values(flag_bot_ads=True)
        )
        await app_settings.set_setting(session, "ad_all_admin_groups_v1", True)
    await session.flush()


async def list_groups(session: AsyncSession) -> list[BotGroup]:
    await ensure_known_groups(session)
    stmt = select(BotGroup).order_by(BotGroup.is_main.desc(), BotGroup.title.asc())
    return list((await session.execute(stmt)).scalars().all())


async def set_group_flag(
    session: AsyncSession,
    chat_id: int,
    flag: str,
    value: bool,
) -> BotGroup | None:
    row = await session.get(BotGroup, chat_id)
    if row is None:
        return None
    mapping = {
        "relay": "flag_relay_source",
        "driver": "flag_driver_posts",
        "auto": "flag_auto_posts",
        "ads": "flag_bot_ads",
        "delete": "flag_delete_original",
    }
    attr = mapping.get(flag)
    if not attr:
        return None
    setattr(row, attr, bool(value))
    row.updated_at = utcnow()
    await session.flush()
    return row


async def set_main_group(session: AsyncSession, chat_id: int) -> BotGroup | None:
    row = await session.get(BotGroup, chat_id)
    if row is None:
        return None
    await session.execute(update(BotGroup).values(is_main=False))
    row.is_main = True
    row.flag_driver_posts = False
    row.flag_auto_posts = False
    await app_settings.set_setting(session, "main_group_id", chat_id)
    await session.flush()
    return row


async def refresh_group_meta(bot: Bot, session: AsyncSession, chat_id: int) -> BotGroup | None:
    row = await session.get(BotGroup, chat_id)
    if row is None:
        row = await upsert_group(session, chat_id)
    try:
        chat: Chat = await bot.get_chat(chat_id)
        row.title = chat.title or row.title or str(chat_id)
        ctype = getattr(chat, "type", None) or row.chat_type or "supergroup"
        if isinstance(ctype, str):
            row.chat_type = ctype
        try:
            row.member_count = await bot.get_chat_member_count(chat_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        status = member.status
        is_channel = row.chat_type == "channel"
        if status in {"administrator", "creator"}:
            # Kanalda post uchun can_post_messages kerak
            can_post = getattr(member, "can_post_messages", None)
            if is_channel:
                row.can_post = True if can_post is None else bool(can_post)
                if status == "creator":
                    row.can_post = True
            else:
                row.can_post = can_post is not False
            row.can_delete = bool(getattr(member, "can_delete_messages", False))
            if status == "creator":
                row.can_delete = True
        elif status == "member" and not is_channel:
            row.can_post = True
            row.can_delete = False
        else:
            # Kanalda oddiy member post qila olmaydi
            row.can_post = False
            row.can_delete = False
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info("Guruh/kanal meta o'qilmadi %s: %s", chat_id, exc)
        row.can_post = False
        row.can_delete = False
    row.updated_at = utcnow()
    await session.flush()
    return row


def driver_post_chat_ids(groups: list[BotGroup] | None, *, auto: bool = False) -> list[int]:
    """Haydovchi / avto e'lon guruhlari (asosiy emas, kanal emas)."""
    if not groups:
        return list(settings.resolved_driver_post_group_ids)
    out: list[int] = []
    for g in groups:
        if not g.approved:
            continue
        if g.is_main or g.is_channel():
            continue
        if auto and not g.flag_auto_posts:
            continue
        if not auto and not g.flag_driver_posts:
            continue
        out.append(g.chat_id)
    return out or list(settings.resolved_driver_post_group_ids)


def relay_source_ids(groups: list[BotGroup] | None) -> list[int] | None:
    """None = eski config mantiqi. Kanallar relay manbasi emas."""
    if not groups:
        return None
    return [
        g.chat_id
        for g in groups
        if g.approved and g.flag_relay_source and not g.is_main and not g.is_channel()
    ]
