"""Xotira ichida dublikat. Bazaga yozilmaydi — yuborishdan oldin kutish yo'q."""

from __future__ import annotations

import time
from collections.abc import Callable


class Dedup:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] | None = None,
        msg_ttl: float = 86_400,
    ) -> None:
        self._now = monotonic or time.monotonic
        self._msg_ttl = msg_ttl
        self._msgs: dict[tuple[int, int], float] = {}
        self._texts: dict[tuple[int, str], float] = {}

    def claim(
        self,
        chat_id: int,
        message_id: int,
        user_id: int,
        norm: str,
        window_sec: float,
    ) -> str | None:
        """None — yuborish mumkin. Aks holda sabab: dup-message yoki dup-text.

        Await yo'q, shuning uchun tekshiruv va belgi bitta hodisa tsiklida atomar.
        """
        now = self._now()
        self._purge(now)
        key = (chat_id, message_id)
        seen = self._msgs.get(key)
        if seen is not None and now - seen < self._msg_ttl:
            return "dup-message"
        text_key = (user_id, norm)
        text_seen = self._texts.get(text_key)
        if text_seen is not None and now - text_seen < window_sec:
            self._msgs[key] = now
            return "dup-text"
        self._msgs[key] = now
        self._texts[text_key] = now
        return None

    def release(self, chat_id: int, message_id: int, user_id: int, norm: str) -> None:
        self._msgs.pop((chat_id, message_id), None)
        self._texts.pop((user_id, norm), None)

    def remember_message(self, chat_id: int, message_id: int) -> None:
        self._msgs[(chat_id, message_id)] = self._now()

    def remember_text(self, user_id: int, norm: str, age_sec: float) -> None:
        if not norm:
            return
        self._texts[(user_id, norm)] = self._now() - max(0.0, age_sec)

    def _purge(self, now: float) -> None:
        if len(self._msgs) < 5000 and len(self._texts) < 5000:
            return
        expired_msgs = [key for key, ts in self._msgs.items() if now - ts >= self._msg_ttl]
        for key in expired_msgs:
            del self._msgs[key]
        expired_text = [key for key, ts in self._texts.items() if now - ts >= self._msg_ttl]
        for key in expired_text:
            del self._texts[key]
