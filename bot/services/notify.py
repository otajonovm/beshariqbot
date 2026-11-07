"""Adminlarga xabar yuborish."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from bot.config import settings

logger = logging.getLogger(__name__)


async def notify_admins(
    bot: Bot,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Barcha adminlarga (to'liq huquq) xabar yuboradi."""
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("Adminga yozilmadi %s: %s", admin_id, exc)
