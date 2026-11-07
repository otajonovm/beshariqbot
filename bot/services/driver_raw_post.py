"""Haydovchi lichkasidagi xom e'lonni guruhlarga yuborish."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from html import escape

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from bot.config import settings
from bot.database.models import Driver

logger = logging.getLogger(__name__)

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
FOOTER_MARK = "Bot orqali"


@dataclass(slots=True)
class RawPayload:
    text: str
    photo_file_id: str | None = None


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


def build_footer(driver: Driver) -> str:
    parts: list[str] = []
    phone = (driver.phone or "").strip()
    if phone and phone not in {"—", "-"}:
        parts.append(f"📞 {escape(phone)}")
    username = (driver.username or "").strip().lstrip("@")
    if username:
        parts.append(f"@{escape(username)}")
    line = " · ".join(parts)
    if line:
        return f"\n\n{line}\n{FOOTER_MARK}"
    return f"\n\n{FOOTER_MARK}"


def compose_body(raw_text: str, driver: Driver, *, for_caption: bool = False) -> str:
    """Asl matn saqlanadi; footer sig'masa tashlab ketiladi (matn qisqartirilmaydi)."""
    body = (raw_text or "").rstrip()
    limit = CAPTION_LIMIT if for_caption else TEXT_LIMIT
    footer = build_footer(driver)
    # Escape qilingan matn + HTML footer
    escaped = escape(body) if body else ""
    if escaped:
        candidate = escaped + footer
    else:
        candidate = footer.lstrip("\n")
    if len(candidate) <= limit:
        return candidate
    # Footer sig'masa — faqat asl matn (escape)
    if escaped and len(escaped) <= limit:
        return escaped
    # Juda uzun matn: Telegram o'zi rad etadi; qisqartirmaymiz
    return escaped or body


def post_targets() -> list[int]:
    """Ulangan guruhlar; 426. Global (faqat taksi hub) chiqarilgan."""
    return list(settings.resolved_driver_post_group_ids)


async def _send_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    photo_file_id: str | None,
) -> None:
    attempts = 0
    while True:
        try:
            if photo_file_id:
                await bot.send_photo(
                    chat_id,
                    photo=photo_file_id,
                    caption=text or None,
                )
            else:
                await bot.send_message(chat_id, text)
            return
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
) -> None:
    for_caption = bool(payload.photo_file_id)
    text = compose_body(payload.text, driver, for_caption=for_caption)
    await _send_with_retry(bot, chat_id, text, payload.photo_file_id)


async def post_to_main(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
) -> tuple[int | None, str | None]:
    """Asosiy guruhga darhol. (chat_id, None) yoki (None, error)."""
    targets = post_targets()
    if not targets:
        return None, "Guruhlar sozlanmagan"
    main = targets[0]
    try:
        await send_one(bot, main, payload, driver)
        return main, None
    except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError) as exc:
        logger.warning("raw post main fail %s: %s", main, exc)
        return main, str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("raw post main unexpected %s", main)
        return main, str(exc)


async def post_to_rest(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
    *,
    skip_chat_ids: set[int] | None = None,
) -> tuple[list[int], list[tuple[int, str]]]:
    """Qolgan guruhlarga parallel (asosiy allaqachon yuborilgan deb)."""
    skip = skip_chat_ids or set()
    rest = [cid for cid in post_targets() if cid not in skip]
    if not rest:
        return [], []
    results = await asyncio.gather(
        *[_post_one_safe(bot, chat_id, payload, driver) for chat_id in rest],
        return_exceptions=False,
    )
    ok: list[int] = []
    failed: list[tuple[int, str]] = []
    for chat_id, err in results:
        if err is None:
            ok.append(chat_id)
        else:
            failed.append((chat_id, err))
    return ok, failed


async def broadcast_raw(
    bot: Bot,
    driver: Driver,
    payload: RawPayload,
) -> tuple[list[int], list[tuple[int, str]]]:
    """
    Asosiy guruhga darhol, qolganlariga parallel.
    Qaytaradi: (ok_ids, [(fail_id, reason), ...]).
    """
    ok: list[int] = []
    failed: list[tuple[int, str]] = []

    main_id, main_err = await post_to_main(bot, driver, payload)
    if main_id is not None and main_err is None:
        ok.append(main_id)
    elif main_id is not None:
        failed.append((main_id, main_err or "xato"))

    skip = {main_id} if main_id is not None else set()
    rest_ok, rest_fail = await post_to_rest(bot, driver, payload, skip_chat_ids=skip)
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
) -> tuple[int, str | None]:
    try:
        await send_one(bot, chat_id, payload, driver)
        return chat_id, None
    except (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramNetworkError) as exc:
        logger.warning("raw post fail %s: %s", chat_id, exc)
        return chat_id, str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("raw post unexpected %s", chat_id)
        return chat_id, str(exc)


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
