from __future__ import annotations

import asyncio

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from bot.relay.outbound import OutboundQueue


class _Bot:
    def __init__(self, fail_once: bool = False) -> None:
        self.n = 0
        self.fail_once = fail_once

    async def send_message(self, **kwargs: object) -> int:
        self.n += 1
        if self.fail_once and self.n == 1:
            raise TelegramRetryAfter(
                method=SendMessage(chat_id=1, text="x"),
                message="flood",
                retry_after=2,
            )
        return self.n


def test_under_limit_does_not_sleep() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)
        clock["t"] += delay

    async def scenario() -> None:
        bot = _Bot()
        queue = OutboundQueue(limit=2, window=60, sleep=fake_sleep, monotonic=lambda: clock["t"])
        assert await queue.send(bot, chat_id=1, text="a") == 1
        assert slept == []
        assert await queue.send(bot, chat_id=1, text="b") == 2
        assert slept == []
        assert await queue.send(bot, chat_id=1, text="c") == 3
        assert slept and slept[0] > 1

    asyncio.run(scenario())


def test_retry_after_is_waited_then_sent() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)
        clock["t"] += delay

    async def scenario() -> None:
        bot = _Bot(fail_once=True)
        queue = OutboundQueue(limit=10, window=60, sleep=fake_sleep, monotonic=lambda: clock["t"])
        assert await queue.send(bot, chat_id=1, text="a") == 2
        assert bot.n == 2
        assert slept and slept[0] >= 2

    asyncio.run(scenario())
