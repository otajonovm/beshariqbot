"""Taksi va pochta buyurtma FSM jarayoni (faqat shaxsiy chat)."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.db_requests import create_order, upsert_user
from bot.database.models import DIRECTION_LABELS, OrderType
from bot.keyboards.default_kb import (
    BTN_BT,
    BTN_CANCEL,
    BTN_PARCEL,
    BTN_TB,
    DIRECTION_BY_BTN,
    contact_kb,
    main_menu_kb,
)
from bot.keyboards.inline_kb import seats_kb, skip_photo_kb
from bot.states.order_states import ParcelOrder, TaxiOrder

logger = logging.getLogger(__name__)
router = Router(name="passenger")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

PHONE_RE = re.compile(r"^\+?\d{9,15}$")


def _normalize_phone(raw: str) -> str | None:
    cleaned = re.sub(r"[\s\-()]", "", raw.strip())
    if not PHONE_RE.match(cleaned):
        return None
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif cleaned.startswith("9") and len(cleaned) == 9:
        cleaned = "+998" + cleaned
    elif cleaned.startswith("998") and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def _route_label(direction: str) -> str:
    origin, dest = DIRECTION_LABELS[direction]
    return f"{origin} ➡️ {dest}"


async def _cancel_fsm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Amal bekor qilindi. Bosh menyu:", reply_markup=main_menu_kb())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return
    await upsert_user(
        session,
        telegram_id=user.id,
        full_name=user.full_name,
        username=user.username,
    )
    await session.commit()
    await message.answer(
        "Assalomu alaykum!\n\n"
        "<b>Karvon Taxi: Beshariq — Toshkent</b>\n"
        "Faqat taksi va pochta xizmati.\n\n"
        "Yo'nalishni tanlang:",
        reply_markup=main_menu_kb(),
    )


@router.message(F.text == BTN_CANCEL)
async def cancel_flow(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Bosh menyu:", reply_markup=main_menu_kb())
        return
    await _cancel_fsm(message, state)


@router.message(F.text.in_({BTN_BT, BTN_TB}))
async def on_direction_button(message: Message, state: FSMContext) -> None:
    direction = DIRECTION_BY_BTN[message.text or ""]
    label = _route_label(direction)

    if await state.get_state() == ParcelOrder.direction.state:
        await state.update_data(direction=direction, photo_file_id=None)
        await state.set_state(ParcelOrder.description)
        await message.answer(
            f"📦 Yo'nalish: <b>{label}</b>\n\n"
            "Yuk haqida qisqacha yozing (hujjat, sumka, quti...).\n"
            "Ixtiyoriy rasm ham yuborishingiz mumkin.",
            reply_markup=skip_photo_kb(),
        )
        return

    await state.clear()
    await state.update_data(direction=direction)
    await state.set_state(TaxiOrder.seats)
    await message.answer(
        f"🚖 Yo'nalish: <b>{label}</b>\n\nNecha kishi ketadi?",
        reply_markup=seats_kb(),
    )


@router.message(F.text == BTN_PARCEL)
async def start_parcel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ParcelOrder.direction)
    await message.answer(
        "📦 <b>Pochta yuborish</b>\n\nYo'nalishni pastdagi tugmadan tanlang:",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(TaxiOrder.seats, F.data.startswith("seats:"))
async def taxi_seats(callback: CallbackQuery, state: FSMContext) -> None:
    seats = (callback.data or "").split(":", 1)[1]
    await state.update_data(seats=seats)
    await state.set_state(TaxiOrder.contact)
    await callback.message.edit_text(f"👥 Odam soni: <b>{seats}</b>")
    await callback.message.answer(
        "Aloqa uchun telefon raqamingizni yuboring:",
        reply_markup=contact_kb(),
    )
    await callback.answer()


@router.message(TaxiOrder.contact, F.contact)
async def taxi_contact_btn(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _finish_taxi(message, state, session, contact_phone=message.contact.phone_number)


@router.message(TaxiOrder.contact, F.text)
async def taxi_contact_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await _cancel_fsm(message, state)
        return
    phone = _normalize_phone(message.text or "")
    if not phone:
        await message.answer(
            "Raqamni tugma orqali yuboring yoki +998XXXXXXXXX formatida yozing.",
            reply_markup=contact_kb(),
        )
        return
    await _finish_taxi(message, state, session, contact_phone=phone)


async def _finish_taxi(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    contact_phone: str,
) -> None:
    user = message.from_user
    if user is None:
        return
    if message.contact and message.contact.user_id and message.contact.user_id != user.id:
        await message.answer("Faqat o'z raqamingizni yuboring.", reply_markup=contact_kb())
        return

    phone = _normalize_phone(contact_phone) or contact_phone
    data = await state.get_data()
    await upsert_user(session, user.id, user.full_name, user.username, phone=phone)
    order = await create_order(
        session,
        order_type=OrderType.TAXI.value,
        passenger_id=user.id,
        direction=data["direction"],
        phone=phone,
        seats=data.get("seats"),
    )
    await session.commit()
    await state.clear()

    from bot.handlers.group import publish_order_to_group

    posted = await publish_order_to_group(message.bot, session, order)
    if posted:
        await message.answer(
            f"✅ Buyurtma qabul qilindi! Raqam: <b>#{order.id}</b>\n\n"
            "Haydovchilar guruhiga yuborildi. Tez orada siz bilan bog'lanishadi.",
            reply_markup=main_menu_kb(),
        )
    else:
        await message.answer(
            f"✅ Buyurtma saqlandi (#{order.id}), lekin guruhga yuborishda xatolik bo'ldi. "
            "Admin tez orada ko'rib chiqadi.",
            reply_markup=main_menu_kb(),
        )


@router.callback_query(ParcelOrder.description, F.data == "parcel:nophoto")
async def parcel_skip_photo(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("cargo_description"):
        await callback.answer("Avval yuk tavsifini yozing.", show_alert=True)
        return
    await state.set_state(ParcelOrder.contact)
    await callback.message.edit_text("Tavsif qabul qilindi.")
    await callback.message.answer(
        "Aloqa uchun telefon raqamingizni yuboring:",
        reply_markup=contact_kb(),
    )
    await callback.answer()


@router.message(ParcelOrder.description, F.photo)
async def parcel_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    caption = (message.caption or "").strip()
    data = await state.get_data()
    description = caption or data.get("cargo_description") or "Rasmli posilka"
    await state.update_data(photo_file_id=photo.file_id, cargo_description=description)
    await state.set_state(ParcelOrder.contact)
    await message.answer(
        "📦 Rasm va tavsif qabul qilindi.\n\nAloqa uchun telefon raqamingizni yuboring:",
        reply_markup=contact_kb(),
    )


@router.message(ParcelOrder.description, F.text)
async def parcel_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == BTN_CANCEL:
        await _cancel_fsm(message, state)
        return
    if len(text) < 2 or len(text) > 500:
        await message.answer("Tavsif 2–500 belgi orasida bo'lsin.")
        return
    await state.update_data(cargo_description=text)
    await message.answer(
        "Tavsif qabul qilindi. Ixtiyoriy rasm yuboring yoki davom eting:",
        reply_markup=skip_photo_kb(),
    )


@router.message(ParcelOrder.contact, F.contact)
async def parcel_contact_btn(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _finish_parcel(message, state, session, contact_phone=message.contact.phone_number)


@router.message(ParcelOrder.contact, F.photo)
async def parcel_late_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    caption = (message.caption or "").strip()
    data = await state.get_data()
    description = caption or data.get("cargo_description") or "Rasmli posilka"
    await state.update_data(photo_file_id=photo.file_id, cargo_description=description)
    await message.answer("Rasm qo'shildi. Endi telefon raqamingizni yuboring:", reply_markup=contact_kb())


@router.message(ParcelOrder.contact, F.text)
async def parcel_contact_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await _cancel_fsm(message, state)
        return
    phone = _normalize_phone(message.text or "")
    if not phone:
        await message.answer(
            "Raqamni tugma orqali yuboring yoki +998XXXXXXXXX formatida yozing.",
            reply_markup=contact_kb(),
        )
        return
    await _finish_parcel(message, state, session, contact_phone=phone)


async def _finish_parcel(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    contact_phone: str,
) -> None:
    user = message.from_user
    if user is None:
        return
    if message.contact and message.contact.user_id and message.contact.user_id != user.id:
        await message.answer("Faqat o'z raqamingizni yuboring.", reply_markup=contact_kb())
        return

    data = await state.get_data()
    description = (data.get("cargo_description") or "").strip()
    if not description:
        await message.answer("Avval yuk tavsifini yozing yoki rasm yuboring.")
        await state.set_state(ParcelOrder.description)
        return

    phone = _normalize_phone(contact_phone) or contact_phone
    await upsert_user(session, user.id, user.full_name, user.username, phone=phone)
    order = await create_order(
        session,
        order_type=OrderType.PARCEL.value,
        passenger_id=user.id,
        direction=data["direction"],
        phone=phone,
        cargo_description=description,
        photo_file_id=data.get("photo_file_id"),
    )
    await session.commit()
    await state.clear()

    from bot.handlers.group import publish_order_to_group

    posted = await publish_order_to_group(message.bot, session, order)
    if posted:
        await message.answer(
            f"✅ Pochta buyurtmasi qabul qilindi! Raqam: <b>#{order.id}</b>\n\n"
            "Haydovchilar guruhiga yuborildi.",
            reply_markup=main_menu_kb(),
        )
    else:
        await message.answer(
            f"✅ Buyurtma saqlandi (#{order.id}), guruhga yuborishda xatolik. Admin ko'rib chiqadi.",
            reply_markup=main_menu_kb(),
        )
