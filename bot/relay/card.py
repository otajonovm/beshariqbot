"""Asosiy guruhdagi karta.

Forward ishlatilmaydi: foydalanuvchi forwardni yopgan bo'lsa haydovchi ismni
va profilni ko'rmaydi. Karta ism, telefon va yozish tugmasini o'zi saqlaydi.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")


@dataclass(slots=True)
class RelayCard:
    user_id: int
    full_name: str
    username: str | None
    chat_id: int
    chat_title: str
    chat_username: str | None
    message_id: int
    body: str
    phones: list[str]
    hide_origin: bool = False


def contact_url(user_id: int, username: str | None) -> str:
    if username and _USERNAME.fullmatch(username):
        return f"https://t.me/{username}"
    return f"tg://user?id={user_id}"


def message_url(chat_id: int, message_id: int, username: str | None) -> str | None:
    if username and _USERNAME.fullmatch(username):
        return f"https://t.me/{username}/{message_id}"
    raw = str(chat_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return None


def render(card: RelayCard) -> tuple[str, InlineKeyboardMarkup]:
    name = html.escape((card.full_name or "").strip() or "Yo'lovchi", quote=False)
    username = card.username if card.username and _USERNAME.fullmatch(card.username) else None
    user_html = f'<a href="tg://user?id={card.user_id}">{name}</a>'
    if username:
        user_html += f" (@{html.escape(username, quote=False)})"
    lines = [
        "🚕 <b>Yangi buyurtma</b>",
        f"👤 {user_html}",
    ]
    if card.phones:
        phones = ", ".join(f"<code>{html.escape(phone, quote=False)}</code>" for phone in card.phones)
        lines.append(f"📞 {phones}")
    title = html.escape((card.chat_title or "").strip() or "Guruh", quote=False)
    lines.append(f"📍 Guruh: {title}")
    body = html.escape(card.body.strip(), quote=False)
    if len(body) > 3500:
        body = body[:3500] + "…"
    lines.append(f"💬 {body}")

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🔴 Yo'lovchiga yozish",
                url=contact_url(card.user_id, username),
                style="danger",
            )
        ]
    ]
    if not card.hide_origin:
        origin = message_url(card.chat_id, card.message_id, card.chat_username)
        if origin:
            rows.append(
                [InlineKeyboardButton(text="🔗 Asl xabar", url=origin, style="primary")]
            )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return "\n".join(lines), markup
