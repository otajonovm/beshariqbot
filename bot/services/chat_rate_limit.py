"""Guruhga umumiy daqiqalik limit (avto-e'lon + reklama birga)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

# Telegram ~20/min — biroz pastroq qoldiramiz
_WINDOW = 60.0
_LIMIT = 20

_chat_times: dict[int, deque[float]] = defaultdict(deque)
_chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _purge(chat_id: int, now: float) -> None:
    times = _chat_times[chat_id]
    while times and now - times[0] >= _WINDOW:
        times.popleft()


def chat_send_count(chat_id: int) -> int:
    now = time.monotonic()
    _purge(chat_id, now)
    return len(_chat_times[chat_id])


async def wait_chat_slot(chat_id: int) -> float:
    """Limit ichida bo'lsa 0, aks holda kutib slot oladi. Qaytadi: kutish sekundlari."""
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


def record_chat_send(chat_id: int) -> None:
    """Slot oldindan olinmagan yuborishni qayd etish (kam hollarda)."""
    now = time.monotonic()
    _purge(chat_id, now)
    _chat_times[chat_id].append(now)
