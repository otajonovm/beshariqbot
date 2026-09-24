"""Guruhdagi buyurtma kartochkasi va race-condition himoyalangan claim."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.db_requests import (
    claim_order,
    get_driver,
    release_claim,
    set_group_posts,
)
from bot.database.models import Order
from bot.keyboards.inline_kb import claim_kb, deal_kb

logger = logging.getLogger(__name__)
router = Router(name="group")


def _order_group_ids() -> list[int]:
    """Zakas faqat asosiy guruhga tushadi."""
    return [settings.primary_group_id]


async def create_driver_invite_links(_bot: Bot) -> list[str]:
    return [settings.primary_group_invite]


@router.my_chat_member()
async def on_bot_chat_member(event: ChatMemberUpdated) -> None:
    if event.chat.type not in {"group", "supergroup"}:
        return
    if event.new_chat_member.status not in {"administrator", "member", "creator"}:
        return
    known = event.chat.id in settings.group_ids
    status = "ulangan" if known else "noma'lum guruh (sozlamada yo'q)"
    logger.info("Bot guruhga qo'shildi: %s %s (%s)", event.chat.id, event.chat.title, status)
    try:
        await event.bot.send_message(
            settings.admin_id,
            f"Bot guruhga qo'shildi: <b>{event.chat.title or 'guruh'}</b>\n"
            f"ID: <code>{event.chat.id}</code>\n"
            f"{'✅ Sozlamada bor' if known else '⚠️ SUPERGROUP_IDS ga qo\'shilmagan'}",
        )
    except TelegramForbiddenError:
        pass


async def _send_card(
    bot: Bot,
    chat_id: int,
    order: Order,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> Message | None:
    try:
        if order.photo_file_id:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=order.photo_file_id,
                caption=text,
                reply_markup=reply_markup,
            )
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.exception("Guruhga yuborilmadi %s / #%s: %s", chat_id, order.id, exc)
        return None


async def publish_order_to_group(bot: Bot, session: AsyncSession, order: Order) -> bool:
    text = order.to_group_text()
    markup = claim_kb(order.id)
    posts: dict[int, int] = {}
    errors: list[str] = []
    for chat_id in _order_group_ids():
        msg = await _send_card(bot, chat_id, order, text, markup)
        if msg is not None:
            posts[chat_id] = msg.message_id
        else:
            errors.append(str(chat_id))
    if posts:
        order.set_posts_map(posts)
        await set_group_posts(session, order.id, posts)
        await session.commit()
    if errors:
        try:
            await bot.send_message(
                settings.admin_id,
                f"⚠️ Buyurtma #{order.id} ba'zi guruhlarga yuborilmadi:\n"
                + "\n".join(f"<code>{item}</code>" for item in errors),
            )
        except TelegramForbiddenError:
            pass
    return bool(posts)


async def edit_group_card(
    bot: Bot,
    order: Order,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    posts = order.posts_map()
    if not posts:
        return
    updated: dict[int, int] = dict(posts)
    for chat_id, message_id in posts.items():
        try:
            if order.photo_file_id:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
        except TelegramBadRequest as exc:
            logger.warning("Tahrir bo'lmadi %s / #%s: %s", chat_id, order.id, exc)
            msg = await _send_card(bot, chat_id, order, text, reply_markup)
            if msg is not None:
                updated[chat_id] = msg.message_id
    order.set_posts_map(updated)


async def process_claim(
    bot: Bot,
    session: AsyncSession,
    *,
    order_id: int,
    user: User,
) -> str:
    """
    Buyurtmani qabul qilish. Natija matni foydalanuvchiga ko'rsatiladi.
    """
    result, order = await claim_order(
        session,
        order_id,
        user.id,
        full_name=user.full_name or "",
        username=user.username,
    )

    if result == "not_found":
        return "Buyurtma topilmadi."
    if result == "cooldown":
        return "Siz bu buyurtmani avval bekor qilgansiz. Boshqa haydovchilar olishi mumkin."
    if result == "taken":
        return "Kechirasiz, ushbu buyurtmani boshqa haydovchi oldi!"
    if result != "ok" or order is None:
        return "Xatolik. Qayta urinib ko'ring."

    driver = await get_driver(session, user.id)
    driver_name = (
        driver.full_name if driver and driver.full_name else (user.full_name or "Haydovchi")
    )
    await edit_group_card(bot, order, order.to_claimed_group_text(driver_name))
    await set_group_posts(session, order.id, order.posts_map())
    await session.commit()

    passenger = order.passenger
    private_text = order.to_driver_private_text(passenger)
    try:
        await bot.send_message(
            chat_id=user.id,
            text=private_text,
            reply_markup=deal_kb(order.id),
        )
    except TelegramForbiddenError:
        await release_claim(session, order.id, user.id)
        await edit_group_card(
            bot,
            order,
            order.to_group_text(),
            reply_markup=claim_kb(order.id),
        )
        await set_group_posts(session, order.id, order.posts_map())
        await session.commit()
        return "Bot sizga lichka yozolmayapti. /start bosing va qayta urinib ko'ring."

    if passenger:
        try:
            await bot.send_message(
                passenger.telegram_id,
                f"🚘 Haydovchi <b>{driver_name}</b> buyurtmangizni (#{order.id}) qabul qildi.\n"
                "Tez orada siz bilan bog'lanadi.",
            )
        except TelegramForbiddenError:
            logger.info("Mijoz %s botni bloklagan", passenger.telegram_id)

    await _maybe_remind_profile(bot, session, user.id)
    return f"✅ Buyurtma #{order.id} sizniki! Tafsilotlar yuqorida."


async def _maybe_remind_profile(bot: Bot, session: AsyncSession, user_id: int) -> None:
    driver = await get_driver(session, user_id)
    if driver is not None and driver.is_profile_complete():
        return
    try:
        await bot.send_message(
            user_id,
            "ℹ️ Profilingiz hali to'liq emas.\n"
            "Botda <b>🚘 Haydovchi sifatida ulanish</b> tugmasini bosib "
            "ism, mashina va telefonni to'ldiring.",
        )
    except TelegramForbiddenError:
        pass


@router.callback_query(F.data.startswith("claim:"))
async def claim_order_cb(callback: CallbackQuery, session: AsyncSession) -> None:
    """Eski callback tugmalar uchun (agar guruhda qolgan bo'lsa)."""
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri buyurtma.", show_alert=True)
        return

    text = await process_claim(callback.bot, session, order_id=order_id, user=user)
    await callback.answer(text[:200], show_alert=True)
