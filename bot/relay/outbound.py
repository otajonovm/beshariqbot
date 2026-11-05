"""Asosiy guruhga yuborish.

Oddiy holatda navbat va sleep yo'q: limit ichida bo'lsa sendMessage darhol ketadi.
Limit yoki 429 bo'lsa xabar navbatga tushadi va Retry-After dan keyin chiqadi.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class OutboundQueue:
    def __init__(
        self,
        *,
        limit: int = 18,
        window: float = 60.0,
        sleep: Sleep | None = None,
        monotonic: Clock | None = None,
    ) -> None:
        self.limit = max(1, limit)
        self.window = window
        self._sleep = sleep or asyncio.sleep
        self._now = monotonic or time.monotonic
        self._times: deque[float] = deque()
        self._blocked_until = 0.0
        self._queue: deque[tuple[Bot, dict[str, Any], asyncio.Future[Any]]] = deque()
        self._worker: asyncio.Task[None] | None = None

    def _purge(self, now: float) -> None:
        while self._times and now - self._times[0] >= self.window:
            self._times.popleft()

    def _delay(self, now: float) -> float:
        if now < self._blocked_until:
            return self._blocked_until - now
        self._purge(now)
        if len(self._times) < self.limit:
            return 0.0
        return self.window - (now - self._times[0]) + 0.01

    async def send(self, bot: Bot, **kwargs: Any) -> Any:
        if self._queue:
            return await self._enqueue(bot, kwargs)
        now = self._now()
        if self._delay(now) > 0:
            return await self._enqueue(bot, kwargs)
        self._times.append(now)
        return await self._call(bot, kwargs)

    async def _enqueue(self, bot: Bot, kwargs: dict[str, Any]) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._queue.append((bot, kwargs, future))
        self._ensure_worker()
        return await future

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="relay-outbound")

    async def _run(self) -> None:
        while self._queue:
            delay = self._delay(self._now())
            if delay > 0:
                await self._sleep(delay)
                continue
            bot, kwargs, future = self._queue.popleft()
            self._times.append(self._now())
            try:
                result = await self._call(bot, kwargs)
            except Exception as exc:
                logger.exception("relay queue send failed")
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)

    async def _call(self, bot: Bot, kwargs: dict[str, Any]) -> Any:
        attempts = 0
        while True:
            try:
                return await bot.send_message(**kwargs)
            except TelegramRetryAfter as exc:
                attempts += 1
                retry_after = float(exc.retry_after)
                self._blocked_until = max(self._blocked_until, self._now() + retry_after)
                logger.warning("relay 429 retry_after=%s", exc.retry_after)
                if attempts >= 3:
                    raise
                await self._sleep(retry_after + 0.05)
            except TelegramNetworkError as exc:
                attempts += 1
                logger.warning("relay network: %s", exc)
                if attempts >= 2:
                    raise
                await self._sleep(0.2)
