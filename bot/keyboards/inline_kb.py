"""Inline tugmalar: yo'nalish, hudud, qabul qilish, kelishuv."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import CANCEL_REASON_LABELS


def seats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="seats:1 kishi"),
                InlineKeyboardButton(text="2", callback_data="seats:2 kishi"),
                InlineKeyboardButton(text="3", callback_data="seats:3 kishi"),
                InlineKeyboardButton(text="4", callback_data="seats:4 kishi"),
            ],
            [InlineKeyboardButton(text="Butun salon", callback_data="seats:Butun salon")],
        ]
    )


def skip_photo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Rasm yo'q — davom etish", callback_data="parcel:nophoto")]
        ]
    )


def group_invite_kb(links: list[str] | None = None) -> InlineKeyboardMarkup:
    from bot.config import settings

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"⭐ Asosiy guruh ({settings.primary_group_title})",
                url=settings.primary_group_invite,
            )
        ]
    ]
    seen = {settings.primary_group_invite.rstrip("/")}
    extra = 0
    for url in links or []:
        key = url.rstrip("/")
        if not url or key in seen:
            continue
        seen.add(key)
        extra += 1
        rows.append([InlineKeyboardButton(text=f"👥 Guruh {extra}", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def claim_kb(order_id: int) -> InlineKeyboardMarkup:
    from bot.config import settings

    url = f"https://t.me/{settings.bot_username.lstrip('@')}?start=claim_{order_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤝 Buyurtmani olish",
                    url=url,
                )
            ]
        ]
    )


def deal_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Kelishdik / Safar tasdiqlandi",
                    callback_data=f"deal_ok:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Kelisha olmadik (Bekor qilish)",
                    callback_data=f"deal_cancel:{order_id}",
                )
            ],
        ]
    )


def cancel_reason_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"creason:{order_id}:{code}",
                )
            ]
            for code, label in CANCEL_REASON_LABELS.items()
        ]
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
            [InlineKeyboardButton(text="👥 Guruhlar holati", callback_data="admin:groups")],
            [InlineKeyboardButton(text="🚘 Haydovchilar", callback_data="admin:drivers")],
            [InlineKeyboardButton(text="➕ Obunani uzaytirish", callback_data="admin:extend")],
        ]
    )
