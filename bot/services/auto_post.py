"""Haydovchi avto-e'lon: intervalda guruhlarga qayta yuborish."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.database.db_requests import (
    list_due_auto_posts,
    save_driver_last_announce,
    stop_auto_post,
    update_auto_post_after_send,
)
from bot.database.models import AutoPostSession, AutoPostStatus, Driver, utcnow
from bot.services.driver_raw_post import RawPayload, post_targets, save_last_raw, send_one

logger = logging.getLogger(__name__)

# Faol sessiyalar (panel tugmasi uchun; restart dan keyin scheduler yangilaydi)
_active_driver_ids: set[int] = set()

# chat_id -> yuborish vaqtlari (~20/min/guruh)
_chat_times: dict[int, deque[float]] = defaultdict(deque)
_chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_WINDOW = 60.0
_LIMIT = 18


def is_auto_active(driver_id: int) -> bool:
    return driver_id in _active_driver_ids


def set_auto_active(driver_id: int, active: bool) -> None:
    if active:
        _active_driver_ids.add(driver_id)
    else:
        _active_driver_ids.discard(driver_id)


def sync_active_from_rows(driver_ids: list[int]) -> None:
    _active_driver_ids.clear()
    _active_driver_ids.update(driver_ids)


def _purge(chat_id: int, now: float) -> None:
    times = _chat_times[chat_id]
    while times and now - times[0] >= _WINDOW:
        times.popleft()


async def _wait_chat_slot(chat_id: int) -> float:
    """Limit ichida bo'lsa 0, aks holda kutish sekundlari."""
    async with _chat_locks[chat_id]:
        now = time.monotonic()
        _purge(chat_id, now)
        times = _chat_times[chat_id]
        if len(times) < _LIMIT:
            times.append(now)
            return 0.0
        wait = _WINDOW - (now - times[0]) + 0.05
        await asyncio.sleep(wait)
        now2 = time.monotonic()
        _purge(chat_id, now2)
        times.append(now2)
        return wait


def payload_from_session(sess: AutoPostSession) -> RawPayload:
    return RawPayload(
        text=sess.text or "",
        photo_file_id=sess.photo_file_id,
        departure_text=sess.departure_text,
        seats_hint=sess.seats_hint,
        direction=sess.direction,
    )


def payload_to_dict(payload: RawPayload) -> dict[str, Any]:
    return {
        "text": payload.text,
        "photo_file_id": payload.photo_file_id,
        "departure_text": payload.departure_text,
        "seats_hint": payload.seats_hint,
        "direction": payload.direction,
    }


def payload_from_dict(data: dict[str, Any] | None) -> RawPayload | None:
    if not data or not isinstance(data, dict):
        return None
    text = str(data.get("text") or "")
    photo = data.get("photo_file_id")
    if not text and not photo:
        return None
    return RawPayload(
        text=text,
        photo_file_id=photo,
        departure_text=data.get("departure_text"),
        seats_hint=data.get("seats_hint"),
        direction=data.get("direction"),
    )


def hydrate_last_raw(driver: Driver) -> RawPayload | None:
    """Xotirada yo'q bo'lsa DB last_announce dan yuklaydi."""
    from bot.services.driver_raw_post import get_last_raw

    existing = get_last_raw(driver.telegram_id)
    if existing is not None:
        return existing
    payload = payload_from_dict(getattr(driver, "last_announce", None))
    if payload is not None:
        save_last_raw(driver.telegram_id, payload)
    return payload


def format_interval_human(sec: int) -> str:
    if sec < 60:
        return f"{sec} soniya"
    if sec % 60 == 0:
        m = sec // 60
        return "1 daqiqa" if m == 1 else f"{m} daqiqa"
    return f"{sec} soniya"


def format_duration_human(minutes: int) -> str:
    if minutes % 60 == 0:
        h = minutes // 60
        return "1 soat" if h == 1 else f"{h} soat"
    return f"{minutes} daqiqa"


async def _delete_old(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id or not settings.relay_repost_delete_old:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def _send_chat(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
    chat_id: int,
    old_mid: int | None,
) -> tuple[int | None, str | None, bool]:
    """(new_message_id, error, hit_rate_limit)."""
    waited = await _wait_chat_slot(chat_id)
    hit_rate = waited > 0.5
    try:
        new_mid = await send_one(bot, chat_id, payload, driver)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 0.05)
        try:
            new_mid = await send_one(bot, chat_id, payload, driver)
        except Exception as exc2:  # noqa: BLE001
            return None, str(exc2), True
        hit_rate = True
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError) as exc:
        return None, str(exc), hit_rate
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto post send chat=%s", chat_id)
        return None, str(exc), hit_rate

    # Avval yangi, keyin eski — bo'sh tanaffus bo'lmasin
    await _delete_old(bot, chat_id, old_mid)
    return new_mid, None, hit_rate


async def run_auto_post_cycle(
    bot: Bot,
    session_maker: async_sessionmaker,
    sess: AutoPostSession,
) -> None:
    now = utcnow()
    if sess.ends_at <= now:
        async with session_maker() as session:
            await stop_auto_post(session, sess.driver_id, reason="expired")
            await session.commit()
        set_auto_active(sess.driver_id, False)
        try:
            await bot.send_message(
                sess.driver_id,
                "⏹ Avto-e'lon to'xtadi (vaqt tugadi).",
            )
        except Exception:
            logger.debug("auto expire notify fail", exc_info=True)
        return

    async with session_maker() as session:
        driver = await session.get(Driver, sess.driver_id)
        if driver is None or not driver.is_access_valid():
            await stop_auto_post(session, sess.driver_id, reason="no_access")
            await session.commit()
            set_auto_active(sess.driver_id, False)
            return
        # expire_on_commit=False — maydonlar session yopilgach ham o'qiladi
        driver_ref = driver

    payload = payload_from_session(sess)
    old_ids = {
        int(k): int(v)
        for k, v in (sess.message_ids or {}).items()
        if str(k).lstrip("-").isdigit()
    }
    targets = post_targets()
    new_ids: dict[int, int] = dict(old_ids)
    hit_rate = False
    already_notified = bool(sess.rate_notified)

    for chat_id in targets:
        mid, err, rate = await _send_chat(
            bot, driver_ref, payload, chat_id, old_ids.get(chat_id)
        )
        if rate:
            hit_rate = True
        if mid is not None:
            new_ids[chat_id] = mid
        elif err:
            logger.warning(
                "auto post fail driver=%s chat=%s: %s",
                sess.driver_id,
                chat_id,
                err,
            )

    async with session_maker() as session:
        await update_auto_post_after_send(
            session,
            sess.id,
            message_ids=new_ids,
            sent_at=utcnow(),
            rate_notified=True if hit_rate else None,
        )
        await save_driver_last_announce(
            session, sess.driver_id, payload_to_dict(payload)
        )
        await session.commit()

    save_last_raw(sess.driver_id, payload)
    set_auto_active(sess.driver_id, True)

    if hit_rate and not already_notified:
        try:
            await bot.send_message(
                sess.driver_id,
                "⚠️ Telegram limiti tufayli ba'zi guruhlarga biroz kechikib yuborilmoqda. "
                "Avto-e'lon davom etadi.",
            )
        except Exception:
            logger.debug("rate notify fail", exc_info=True)


def _stub_from_row(s: AutoPostSession) -> AutoPostSession:
    """Session yopilgach ishlatish uchun yengil nusxa."""
    return AutoPostSession(
        id=s.id,
        driver_id=s.driver_id,
        interval_sec=s.interval_sec,
        ends_at=s.ends_at,
        started_at=s.started_at,
        last_sent_at=s.last_sent_at,
        status=s.status,
        text=s.text,
        photo_file_id=s.photo_file_id,
        departure_text=s.departure_text,
        seats_hint=s.seats_hint,
        direction=s.direction,
        message_ids=dict(s.message_ids or {}),
        rate_notified=bool(s.rate_notified),
    )


async def check_auto_posts(bot: Bot, session_maker: async_sessionmaker) -> None:
    from bot.services.feature_flags import is_enabled

    if not is_enabled("driver_auto_post"):
        # Flag o'chiq — faol sessiyalarni yop
        async with session_maker() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(AutoPostSession.driver_id).where(
                    AutoPostSession.status == AutoPostStatus.ACTIVE.value
                )
            )
            ids = [int(x) for x in result.scalars().all()]
            for did in ids:
                await stop_auto_post(session, did, reason="manual")
            await session.commit()
        sync_active_from_rows([])
        return

    async with session_maker() as session:
        due = await list_due_auto_posts(session)
        stubs = [_stub_from_row(s) for s in due]
        # Barcha faol (due bo'lmaganlar ham) — panel cache
        from sqlalchemy import select

        result = await session.execute(
            select(AutoPostSession.driver_id).where(
                AutoPostSession.status == AutoPostStatus.ACTIVE.value
            )
        )
        sync_active_from_rows([int(r) for r in result.scalars().all()])

    for stub in stubs:
        try:
            await run_auto_post_cycle(bot, session_maker, stub)
        except Exception:
            logger.exception("auto post cycle failed session=%s", stub.id)


async def stop_and_notify(
    bot: Bot,
    session_maker: async_sessionmaker,
    driver_id: int,
    *,
    reason: str = "manual",
) -> bool:
    async with session_maker() as session:
        sess = await stop_auto_post(session, driver_id, reason=reason)
        await session.commit()
        stopped = sess is not None
    set_auto_active(driver_id, False)
    if not stopped:
        return False
    texts = {
        "manual": "⏹ Avto-e'lon to'xtatildi.",
        "no_seats": "⏹ Avto-e'lon to'xtadi (joy yo'q).",
        "expired": "⏹ Avto-e'lon to'xtadi (vaqt tugadi).",
        "replaced": "⏹ Oldingi avto-e'lon almashtirildi.",
    }
    try:
        await bot.send_message(driver_id, texts.get(reason, "⏹ Avto-e'lon to'xtatildi."))
    except Exception:
        logger.debug("stop notify fail", exc_info=True)
    return True
