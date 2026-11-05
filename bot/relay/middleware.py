"""Update tashqi middleware. DbSession'dan OLDIN ro'yxatdan o'tadi va birinchi ishlaydi."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from bot.relay.service import relay_message

logger = logging.getLogger(__name__)


class RelayMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # edited_message alohida update. Uni olmasak ham, shu yerda ikkinchi marta yubormaymiz.
        if isinstance(event, Update) and event.message is not None and event.edited_message is None:
            try:
                await relay_message(event.message, data["bot"])
            except Exception:
                logger.exception("relay middleware")
        return await handler(event, data)
