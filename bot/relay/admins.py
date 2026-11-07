"""Guruh adminlarini xotirada saqlash — har xabarda getChatMember chaqirmaslik uchun."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
_ADMIN_STATUSES = frozenset({"creator", "administrator"})


class AdminCache:
    def __init__(self, *, ttl: float = 120.0, monotonic: Clock | None = None) -> None:
        self.ttl = ttl
        self._now = monotonic or time.monotonic
        self._ids: dict[int, set[int]] = {}
        self._fetched: dict[int, float] = {}
        self._locks: dict[int, object] = {}

    def known_admin(self, chat_id: int, user_id: int) -> bool | None:
        """True/False — kesh yangi. None — hali yuklanmagan yoki eskirgan."""
        fetched = self._fetched.get(chat_id)
        ids = self._ids.get(chat_id)
        if fetched is None or ids is None:
            return None
        if self._now() - fetched >= self.ttl:
            return None
        return user_id in ids

    def mark(self, chat_id: int, user_id: int, is_admin: bool) -> None:
        ids = self._ids.setdefault(chat_id, set())
        if is_admin:
            ids.add(user_id)
        else:
            ids.discard(user_id)
        if chat_id not in self._fetched:
            self._fetched[chat_id] = self._now()

    def invalidate(self, chat_id: int) -> None:
        self._ids.pop(chat_id, None)
        self._fetched.pop(chat_id, None)

    def apply_status(self, chat_id: int, user_id: int, status: str) -> None:
        self.mark(chat_id, user_id, status in _ADMIN_STATUSES)

    async def is_admin(self, bot: Bot, chat_id: int, user_id: int) -> bool:
        known = self.known_admin(chat_id, user_id)
        if known is not None:
            return known
        await self.refresh(bot, chat_id)
        return user_id in self._ids.get(chat_id, set())

    async def refresh(self, bot: Bot, chat_id: int) -> set[int]:
        try:
            members = await bot.get_chat_administrators(chat_id)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("adminlar olinmadi chat=%s: %s", chat_id, exc)
            # Eskisini saqlab qolamiz, bo'sh deb o'chirmaymiz.
            return self._ids.get(chat_id, set())
        ids = {member.user.id for member in members if member.user is not None}
        self._ids[chat_id] = ids
        self._fetched[chat_id] = self._now()
        return ids


admin_cache = AdminCache()
