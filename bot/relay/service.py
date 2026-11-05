"""Issiq yo'l: klassifikatsiya (CPU) → dublikat (xotira) → sendMessage.

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
from aiogram.types import LinkPreviewOptions, Message

from bot.config import settings
from bot.relay.card import RelayCard, render
from bot.relay.dedup import Dedup
from bot.relay.detect import classify
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


async def relay_message(message: Message, bot: Bot) -> None:
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
