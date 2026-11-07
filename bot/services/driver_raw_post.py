"""Haydovchi lichkasidagi xom e'lonni guruhlarga yuborish."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from bot.config import settings
from bot.database.models import Driver
from bot.services.driver_card import (
    driver_announce_kb,
    format_driver_caption,
    resolve_announce_photo,
)


def _announce_markup(driver: Driver) -> InlineKeyboardMarkup | None:
    return driver_announce_kb(driver)

logger = logging.getLogger(__name__)

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


@dataclass(slots=True)
class RawPayload:
    text: str
    photo_file_id: str | None = None
    departure_text: str | None = None
    seats_hint: str | None = None
    direction: str | None = None


# telegram_id -> oxirgi muvaffaqiyatli e'lon
_last_raw: dict[int, RawPayload] = {}
# telegram_id -> soatlik yuborish vaqtlari (monotonic)
_post_times: dict[int, deque[float]] = defaultdict(deque)
_HOUR = 3600.0


def get_last_raw(driver_id: int) -> RawPayload | None:
    return _last_raw.get(driver_id)


def save_last_raw(driver_id: int, payload: RawPayload) -> None:
    _last_raw[driver_id] = RawPayload(
        text=payload.text,
        photo_file_id=payload.photo_file_id,
        departure_text=payload.departure_text,
        seats_hint=payload.seats_hint,
        direction=payload.direction,
    )


def check_post_limit(driver_id: int, now: float | None = None) -> tuple[bool, int]:
    """(ruxsat, qolgan_soniya). Limit ichida bo'lsa qolgan_soniya=0."""
    limit = max(1, int(settings.driver_post_limit_per_hour))
    now = time.monotonic() if now is None else now
    times = _post_times[driver_id]
    while times and now - times[0] >= _HOUR:
        times.popleft()
    if len(times) < limit:
        return True, 0
    wait = int(_HOUR - (now - times[0])) + 1
    return False, max(1, wait)


def record_post(driver_id: int, now: float | None = None) -> None:
    _post_times[driver_id].append(time.monotonic() if now is None else now)


def compose_body(
    raw_text: str,
    driver: Driver,
    *,
    for_caption: bool = False,
    departure_text: str | None = None,
    seats_hint: str | None = None,
    direction: str | None = None,
) -> str:
    """Guruh stilidagi caption (preview va yuborish bir xil)."""
    _ = for_caption  # har doim caption limiti
    dep = departure_text if departure_text is not None else getattr(
        driver, "last_departure_text", None
    )
    return format_driver_caption(
        driver,
        raw_text,
        departure_text=dep,
        seats_hint=seats_hint,
        direction=direction or getattr(driver, "preferred_direction", None),
    )


def post_targets() -> list[int]:
    """Ulangan guruhlar; 426. Global (faqat taksi hub) chiqarilgan."""
    return list(settings.resolved_driver_post_group_ids)


async def _send_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    photo_file_id: str | None,
    reply_markup: InlineKeyboardMarkup | None,
) -> int:
    """Yuboradi va message_id qaytaradi."""
    attempts = 0
    while True:
        try:
            if photo_file_id:
                msg = await bot.send_photo(
                    chat_id,
                    photo=photo_file_id,
                    caption=text or None,
                    reply_markup=reply_markup,
                )
            else:
                msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)
            return int(msg.message_id)
        except TelegramRetryAfter as exc:
            attempts += 1
            delay = float(exc.retry_after) + 0.05
            logger.warning("driver raw 429 chat=%s retry_after=%s", chat_id, exc.retry_after)
            if attempts >= 4:
                raise
            await asyncio.sleep(delay)
        except TelegramNetworkError as exc:
            attempts += 1
            logger.warning("driver raw network chat=%s: %s", chat_id, exc)
            if attempts >= 3:
                raise
            await asyncio.sleep(0.25)


async def send_one(
    bot: Bot,
    chat_id: int,
    payload: RawPayload,
    driver: Driver,
) -> int:
    """Guruhga yuboradi, message_id qaytaradi."""
    text = compose_body(
        payload.text,
        driver,
        for_caption=True,
        departure_text=payload.departure_text,
        seats_hint=payload.seats_hint,
        direction=payload.direction,
    )
    photo = resolve_announce_photo(driver, payload.photo_file_id)
    markup = _announce_markup(driver)
    return await _send_with_retry(bot, chat_id, text, photo, markup)


async def post_to_main(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
) -> tuple[int | None, str | None, int | None]:
    """Birinchi guruhga darhol. (chat_id, error, message_id)."""
    targets = post_targets()
    if not targets:
        return None, "Guruhlar sozlanmagan", None
    main = targets[0]
    try:
        mid = await send_one(bot, main, payload, driver)
        return main, None, mid
    except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError) as exc:
        logger.warning("raw post main fail %s: %s", main, exc)
        return main, str(exc), None
    except Exception as exc:  # noqa: BLE001
        logger.exception("raw post main unexpected %s", main)
        return main, str(exc), None


async def post_to_rest(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
    *,
    skip_chat_ids: set[int] | None = None,
) -> tuple[list[int], list[tuple[int, str]], dict[int, int]]:
    """Qolgan guruhlarga parallel. Qaytaradi: ok, failed, {chat_id: message_id}."""
    skip = skip_chat_ids or set()
    rest = [cid for cid in post_targets() if cid not in skip]
    if not rest:
        return [], [], {}
    results = await asyncio.gather(
        *[_post_one_safe(bot, chat_id, payload, driver) for chat_id in rest],
        return_exceptions=False,
    )
    ok: list[int] = []
    failed: list[tuple[int, str]] = []
    ids: dict[int, int] = {}
    for chat_id, err, mid in results:
        if err is None and mid is not None:
            ok.append(chat_id)
            ids[chat_id] = mid
        else:
            failed.append((chat_id, err or "xato"))
    return ok, failed, ids


async def broadcast_raw(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
) -> tuple[list[int], list[tuple[int, str]]]:
    ok: list[int] = []
    failed: list[tuple[int, str]] = []

    main_id, main_err, _mid = await post_to_main(bot, driver, payload)
    if main_id is not None and main_err is None:
        ok.append(main_id)
    elif main_id is not None:
        failed.append((main_id, main_err or "xato"))

    skip = {main_id} if main_id is not None else set()
    rest_ok, rest_fail, _ids = await post_to_rest(bot, driver, payload, skip_chat_ids=skip)
    ok.extend(rest_ok)
    failed.extend(rest_fail)

    if ok:
        save_last_raw(driver.telegram_id, payload)
        record_post(driver.telegram_id)

    return ok, failed


async def _post_one_safe(
    bot: Bot,
    chat_id: int,
    payload: RawPayload,
    driver: Driver,
) -> tuple[int, str | None, int | None]:
    try:
        mid = await send_one(bot, chat_id, payload, driver)
        return chat_id, None, mid
    except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError) as exc:
        logger.warning("raw post fail %s: %s", chat_id, exc)
        return chat_id, str(exc), None
    except Exception as exc:  # noqa: BLE001
        logger.exception("raw post unexpected %s", chat_id)
        return chat_id, str(exc), None


def format_result(ok: list[int], failed: list[tuple[int, str]]) -> str:
    lines = [f"✅ Yuborildi: <b>{len(ok)}</b> ta guruh"]
    if failed:
        lines.append(f"❌ Xato: <b>{len(failed)}</b> ta guruh")
        for chat_id, reason in failed[:8]:
            short = reason.replace("<", "").replace(">", "")
            if len(short) > 80:
                short = short[:77] + "..."
            lines.append(f"· <code>{chat_id}</code> — {short}")
        if len(failed) > 8:
            lines.append(f"· … va yana {len(failed) - 8} ta")
    return "\n".join(lines)
