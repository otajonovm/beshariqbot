"""Adminlarga xabar yuborish."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.config import settings

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, text: str) -> None:
    """Barcha adminlarga (to'liq huquq) xabar yuboradi."""
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("Adminga yozilmadi %s: %s", admin_id, exc)
