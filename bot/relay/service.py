"""Issiq yo'l: klassifikatsiya (CPU) → dublikat (xotira) → sendMessage.

Sweep guruhlarda admin emas → darhol o'chirish + asosiy guruhga o'tkazish.
Baza, statistika va admin ogohlantirishi yuborilgandan keyin fonda yoziladi.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Message

from bot.config import settings
from bot.relay.admins import admin_cache
from bot.relay.card import RelayCard, contact_url, render
from bot.relay.dedup import Dedup
from bot.relay.detect import classify, extract_phones, normalize
from bot.relay.gate import rejection_reason
from bot.relay.outbound import OutboundQueue
from bot.relay.store import save_hit
from bot.services.notify import notify_admins

logger = logging.getLogger(__name__)

dedup = Dedup()
outbound = OutboundQueue(limit=settings.relay_rate_per_minute, window=60.0)
_tasks: set[asyncio.Task[Any]] = set()
_seen: dict[int, dict[str, str]] = {}
_last_alert = 0.0
_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_finish)


def _finish(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("relay background: %s", exc)


def note_group(chat_id: int, title: str, status: str | None = None) -> None:
    prev = _seen.get(chat_id)
    _seen[chat_id] = {
        "title": title or (prev["title"] if prev else ""),
        "status": status if status is not None else (prev["status"] if prev else ""),
    }
    if len(_seen) > 100:
        oldest = next(iter(_seen))
        if oldest != chat_id:
            _seen.pop(oldest, None)


def groups_overview() -> str:
    main_id = settings.resolved_main_group_id
    sources = settings.resolved_source_group_ids
    sweep = sorted(settings.resolved_sweep_group_ids)
    lines = [
        "🚕 <b>Yo'naltirish</b>",
        f"Asosiy guruh: <code>{main_id}</code>",
    ]
    if sources is None:
        lines.append("Manba: bot turgan barcha guruhlar (asosiy dan tashqari).")
    elif not sources:
        lines.append("Manba: yo'q. SOURCE_GROUP_IDS ni to'ldiring.")
    else:
        joined = ", ".join(f"<code>{item}</code>" for item in sources)
        lines.append(f"Manba: {joined}")
    if sweep:
        joined = ", ".join(f"<code>{item}</code>" for item in sweep)
        lines.append(f"Tozalash (admin emas → o'chirib o'tkazish): {joined}")
    if _seen:
        lines.append("Ko'rilgan guruhlar:")
        for chat_id, info in list(_seen.items())[:30]:
            title = html.escape(info.get("title") or "Guruh", quote=False)
            status = html.escape(info.get("status") or "xabar kelgan", quote=False)
            lines.append(f"• {title} — <code>{chat_id}</code> — {status}")
    return "\n".join(lines)


async def _alert(bot: Bot, text: str) -> None:
    global _last_alert
    now = time.monotonic()
    if now - _last_alert < 300:
        return
    _last_alert = now
    await notify_admins(bot, text)


def _message_body(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.contact is not None:
        phone = message.contact.phone_number or ""
        name = " ".join(
            part for part in (message.contact.first_name, message.contact.last_name) if part
        )
        return f"📞 Kontakt: {name} {phone}".strip()
    if message.location is not None:
        return "📍 Lokatsiya"
    if message.photo:
        return "🖼 Rasm"
    if message.video:
        return "🎬 Video"
    if message.voice or message.audio:
        return "🎤 Audio"
    if message.video_note:
        return "⏺ Video xabar"
    if message.document:
        return "📎 Fayl"
    if message.sticker:
        return "🔖 Stiker"
    if message.animation:
        return "🎞 GIF"
    return ""


def _has_payload(message: Message, body: str) -> bool:
    if body.strip():
        return True
    return any(
        (
            message.photo,
            message.video,
            message.document,
            message.voice,
            message.audio,
            message.video_note,
            message.sticker,
            message.animation,
            message.contact,
            message.location,
        )
    )


def _contact_markup(user_id: int, username: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✍️ Yo'lovchiga yozish",
                    url=contact_url(user_id, username),
                )
            ]
        ]
    )


async def _delete_fast(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 0.05)
        try:
            await bot.delete_message(chat_id, message_id)
        except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter):
            logger.warning("sweep o'chirilmadi chat=%s msg=%s", chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning("sweep o'chirilmadi chat=%s msg=%s", chat_id, message_id)


def _is_media(message: Message) -> bool:
    return bool(
        message.photo
        or message.video
        or message.document
        or message.voice
        or message.audio
        or message.video_note
        or message.animation
        or message.sticker
    )


async def _publish_sweep(bot: Bot, message: Message, body: str) -> None:
    user = message.from_user
    chat = message.chat
    assert user is not None and chat is not None
    phones = extract_phones(body)
    text, markup = render(
        RelayCard(
            user_id=user.id,
            full_name=user.full_name or "",
            username=user.username,
            chat_id=chat.id,
            chat_title=chat.title or "",
            chat_username=chat.username,
            message_id=message.message_id,
            body=body or "📎 Media",
            phones=phones,
            hide_origin=True,
        )
    )
    main_id = settings.resolved_main_group_id
    # Media hali o'chirilmagan bo'lishi kerak — avval copy.
    if _is_media(message):
        try:
            await bot.copy_message(
                chat_id=main_id,
                from_chat_id=chat.id,
                message_id=message.message_id,
                reply_markup=_contact_markup(user.id, user.username),
            )
            return
        except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter):
            logger.warning("sweep copy bo'lmadi, karta yuboriladi chat=%s", chat.id)
    await outbound.send(
        bot,
        chat_id=main_id,
        text=text,
        reply_markup=markup,
        link_preview_options=_NO_PREVIEW,
    )


async def sweep_message(message: Message, bot: Bot) -> bool:
    """True — sweep guruhda qayta ishlandi (boshqa relay yo'liga o'tmasin)."""
    chat = message.chat
    user = message.from_user
    if chat is None or user is None:
        return False
    if chat.id not in settings.resolved_sweep_group_ids:
        return False
    if chat.type not in {"group", "supergroup"}:
        return False
    if message.edit_date is not None:
        return True
    if user.is_bot:
        return True
    # Guruh nomidan (anonim admin) yozilgan xabar — o'chirmaymiz.
    if message.sender_chat is not None and message.sender_chat.id == chat.id:
        return True

    note_group(chat.id, chat.title or "", "sweep")
    body = _message_body(message)
    if not _has_payload(message, body):
        return True

    started = time.perf_counter()
    if await admin_cache.is_admin(bot, chat.id, user.id):
        return True

    # Xabar ID bo'yicha dublikat; matn dedup sweepda yo'q — har biri o'tadi.
    duplicate = dedup.claim(
        chat.id,
        message.message_id,
        user.id,
        f"sweep:{chat.id}:{message.message_id}",
        window_sec=86_400,
    )
    if duplicate is not None:
        # Takror update — baribir o'chirishga urinamiz.
        spawn(_delete_fast(bot, chat.id, message.message_id))
        return True

    try:
        if _is_media(message):
            # Copy uchun asl xabar kerak — avval yuboramiz, keyin o'chiramiz.
            await _publish_sweep(bot, message, body)
            await _delete_fast(bot, chat.id, message.message_id)
        else:
            # Matn: o'chirish va yuborish parallel — tezlashadi.
            delete_task = asyncio.create_task(_delete_fast(bot, chat.id, message.message_id))
            try:
                await _publish_sweep(bot, message, body)
            finally:
                if not delete_task.done():
                    await delete_task
    except Exception as exc:
        dedup.release(
            chat.id,
            message.message_id,
            user.id,
            f"sweep:{chat.id}:{message.message_id}",
        )
        spawn(_delete_fast(bot, chat.id, message.message_id))
        logger.exception("sweep yuborilmadi chat=%s msg=%s", chat.id, message.message_id)
        spawn(
            _alert(
                bot,
                "⚠️ Sweep yo'naltirish yuborilmadi. "
                f"<code>{settings.resolved_main_group_id}</code>\n"
                f"{html.escape(str(exc)[:300], quote=False)}",
            )
        )
        return

    handle_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "sweep delay_ms=%s chat_id=%s message_id=%s user_id=%s",
        handle_ms,
        chat.id,
        message.message_id,
        user.id,
    )
    spawn(
        save_hit(
            chat_id=chat.id,
            message_id=message.message_id,
            title=chat.title or "",
            user_id=user.id,
            norm_text=normalize(body)[:420] if body else f"sweep:{message.message_id}",
            handle_ms=handle_ms,
        )
    )
    return True


async def relay_message(message: Message, bot: Bot) -> None:
    if await sweep_message(message, bot):
        return

    started = time.perf_counter()
    chat = message.chat
    if chat is None or chat.type not in {"group", "supergroup"}:
        return
    note_group(chat.id, chat.title or "")
    user = message.from_user
    body = message.text if message.text is not None else (message.caption or "")
    reason = rejection_reason(
        chat_type=chat.type,
        chat_id=chat.id,
        main_id=settings.resolved_main_group_id,
        source_ids=settings.resolved_source_group_ids,
        is_bot=bool(user is None or user.is_bot),
        text=body,
        edited=message.edit_date is not None,
    )
    if reason is not None or user is None:
        logger.debug("relay skip %s %s", chat.id, reason)
        return
    decision = classify(
        body,
        max_chars=settings.relay_max_chars,
        min_score=settings.relay_min_score,
    )
    if not decision.accept or decision.normalized.startswith("yangi buyurtma"):
        logger.debug(
            "relay no chat=%s p=%s d=%s reason=%s",
            chat.id,
            decision.passenger,
            decision.driver,
            decision.reason,
        )
        return
    window = max(1, settings.dedup_minutes) * 60
    duplicate = dedup.claim(chat.id, message.message_id, user.id, decision.normalized, window)
    if duplicate is not None:
        logger.debug("relay %s chat=%s msg=%s", duplicate, chat.id, message.message_id)
        return
    text, markup = render(
        RelayCard(
            user_id=user.id,
            full_name=user.full_name or "",
            username=user.username,
            chat_id=chat.id,
            chat_title=chat.title or "",
            chat_username=chat.username,
            message_id=message.message_id,
            body=body,
            phones=list(decision.phones),
        )
    )
    main_id = settings.resolved_main_group_id
    try:
        await outbound.send(
            bot,
            chat_id=main_id,
            text=text,
            reply_markup=markup,
            link_preview_options=_NO_PREVIEW,
        )
    except Exception as exc:
        dedup.release(chat.id, message.message_id, user.id, decision.normalized)
        logger.exception("relay yuborilmadi chat=%s msg=%s", chat.id, message.message_id)
        spawn(
            _alert(
                bot,
                "⚠️ Yo'naltirish yuborilmadi. Asosiy guruh: "
                f"<code>{main_id}</code>\n{html.escape(str(exc)[:300], quote=False)}",
            )
        )
        return
    handle_ms = int((time.perf_counter() - started) * 1000)
    since_ms = -1
    if message.date is not None:
        try:
            since_ms = max(0, int((datetime.now(UTC) - message.date).total_seconds() * 1000))
        except TypeError:
            since_ms = -1
    logger.info(
        "relay delay_ms=%s since_msg_ms=%s chat_id=%s message_id=%s user_id=%s p=%s d=%s",
        handle_ms,
        since_ms,
        chat.id,
        message.message_id,
        user.id,
        decision.passenger,
        decision.driver,
    )
    spawn(
        save_hit(
            chat_id=chat.id,
            message_id=message.message_id,
            title=chat.title or "",
            user_id=user.id,
            norm_text=decision.normalized,
            handle_ms=handle_ms,
        )
    )
