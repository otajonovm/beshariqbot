"""Debug-mode NDJSON logger (session 2f1753)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

logger = logging.getLogger(__name__)

_LOG_PATH = Path(__file__).resolve().parent.parent / ".cursor" / "debug-2f1753.log"
_SESSION = "2f1753"


def agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str = "pre",
) -> None:
    # #region agent log
    payload = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=False)
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    logger.info("DBG [%s] %s | %s | %s", hypothesis_id, location, message, data or {})
    # #endregion


class DebugUpdateMiddleware(BaseMiddleware):
    """Private update probe — buyurtma oqimi keladimi?"""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # #region agent log
        try:
            if isinstance(event, Update):
                kind = "other"
                chat_type = None
                user_id = None
                text = None
                if event.message is not None:
                    kind = "message"
                    chat_type = event.message.chat.type if event.message.chat else None
                    user_id = event.message.from_user.id if event.message.from_user else None
                    text = (event.message.text or event.message.caption or "")[:40]
                elif event.callback_query is not None:
                    kind = "callback"
                    msg = event.callback_query.message
                    chat_type = msg.chat.type if msg and msg.chat else None
                    user_id = event.callback_query.from_user.id if event.callback_query.from_user else None
                    text = (event.callback_query.data or "")[:40]
                elif event.my_chat_member is not None:
                    kind = "my_chat_member"
                    chat_type = event.my_chat_member.chat.type if event.my_chat_member.chat else None
                if chat_type == "private" or kind == "callback":
                    agent_log(
                        "H7",
                        "debug_agent:DebugUpdateMiddleware",
                        "private_update",
                        {
                            "kind": kind,
                            "chat_type": chat_type,
                            "user_id": user_id,
                            "text": text,
                            "update_id": event.update_id,
                        },
                    )
        except Exception:
            pass
        # #endregion
        return await handler(event, data)
