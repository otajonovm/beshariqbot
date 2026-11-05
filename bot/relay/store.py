"""Yuborishdan keyin statistika va kalit so'zlar. Issiq yo'lda chaqirilmaydi."""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import RelayHit, RelayKeyword, async_session_maker
from bot.relay.dedup import Dedup
from bot.relay.detect import load_overrides, mark_disabled, normalize, upsert_extra

logger = logging.getLogger(__name__)
TASHKENT = ZoneInfo("Asia/Tashkent")


async def load_keywords() -> None:
    async with async_session_maker() as session:
        rows = (await session.execute(select(RelayKeyword))).scalars().all()
    load_overrides([(row.kind, row.phrase, row.weight) for row in rows])


async def warm_dedup(dedup: Dedup, minutes: int) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    window = max(1, minutes) * 60
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(RelayHit)
                    .where(RelayHit.created_at >= cutoff)
                    .order_by(RelayHit.id.desc())
                    .limit(8000)
                )
            )
            .scalars()
            .all()
        )
    for row in rows:
        dedup.remember_message(row.source_chat_id, row.source_message_id)
        created = row.created_at
        if created is None or not row.norm_text:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = (now - created).total_seconds()
        if age < window:
            dedup.remember_text(row.user_id, row.norm_text, age)


async def save_hit(
    *,
    chat_id: int,
    message_id: int,
    title: str,
    user_id: int,
    norm_text: str,
    handle_ms: int,
) -> None:
    try:
        async with async_session_maker() as session:
            session.add(
                RelayHit(
                    source_chat_id=chat_id,
                    source_message_id=message_id,
                    source_title=(title or "")[:255],
                    user_id=user_id,
                    norm_text=(norm_text or "")[:420],
                    handle_ms=handle_ms,
                )
            )
            await session.commit()
    except IntegrityError:
        logger.info("relay hit allaqachon yozilgan %s/%s", chat_id, message_id)
    except Exception:
        logger.exception("relay hit saqlanmadi")


async def add_keyword(session: AsyncSession, kind: str, raw: str) -> tuple[bool, str]:
    phrase = normalize(raw)
    if len(phrase) < 3 or len(phrase) > 120:
        return False, "Kalit so'z 3–120 belgi oralig'ida bo'lsin."
    weight = 5 if kind == "passenger" else 8
    existing = await session.scalar(select(RelayKeyword).where(RelayKeyword.phrase == phrase))
    if existing is None:
        session.add(RelayKeyword(kind=kind, phrase=phrase, weight=weight))
    else:
        existing.kind = kind
        existing.weight = weight
    await session.flush()
    upsert_extra(kind, phrase, weight)
    return True, phrase


async def disable_keyword(session: AsyncSession, raw: str) -> tuple[bool, str]:
    phrase = normalize(raw)
    if len(phrase) < 2:
        return False, "So'z yozilmadi."
    was_active = mark_disabled(phrase)
    existing = await session.scalar(select(RelayKeyword).where(RelayKeyword.phrase == phrase))
    if existing is None:
        session.add(RelayKeyword(kind="off", phrase=phrase, weight=0))
    else:
        existing.kind = "off"
    await session.flush()
    if not was_active:
        return False, phrase
    return True, phrase


async def format_relay_stats(session: AsyncSession) -> str:
    start_local = datetime.now(TASHKENT).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(UTC)
    rows = (
        await session.execute(
            select(
                RelayHit.source_chat_id,
                func.max(RelayHit.source_title),
                func.count(RelayHit.id),
                func.avg(RelayHit.handle_ms),
            )
            .where(RelayHit.created_at >= start)
            .group_by(RelayHit.source_chat_id)
            .order_by(func.count(RelayHit.id).desc())
        )
    ).all()
    if not rows:
        return "🚕 <b>Yo'naltirish (bugun)</b>\nHali yo'naltirilmagan."
    total = sum(int(row[2]) for row in rows)
    weighted = 0.0
    lines = [
        "🚕 <b>Yo'naltirish (bugun)</b>",
        f"Jami: <b>{total}</b>",
    ]
    for chat_id, title, count, avg_ms in rows:
        count_i = int(count)
        avg = int(avg_ms or 0)
        weighted += float(avg_ms or 0) * count_i
        name = html.escape(title or "Guruh", quote=False)
        lines.append(f"• {name} — <b>{count_i}</b> (o'rtacha {avg} ms)\n  <code>{chat_id}</code>")
    if total:
        lines.append(f"O'rtacha kechikish: <b>{int(weighted / total)} ms</b>")
    lines.append("<i>Kechikish — xabar qabul qilingandan asosiy guruhga yuborilguncha.</i>")
    return "\n".join(lines)
