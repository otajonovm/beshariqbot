"""Taksi va pochta buyurtma FSM jarayoni (faqat shaxsiy chat)."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.db_requests import create_order, get_driver, upsert_user
from bot.database.models import DIRECTION_LABELS, DriverStatus, OrderType
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
from bot.relay.card import contact_url
from bot.services.driver_card import normalize_uz_phone, phone_tel_url
from bot.states.order_states import DriverAnnounce, ParcelOrder, TaxiOrder

logger = logging.getLogger(__name__)
router = Router(name="passenger")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

PHONE_RE = re.compile(r"^\+?\d{9,15}$")


async def _notify_passenger_dm_drivers(bot, session: AsyncSession, order) -> None:
    """Premium: yo'lovchi so'rovi lichkaga — hot path emas (buyurtma yaratilgach)."""
    from sqlalchemy import select

    from bot.database.models import Driver
    from bot.services.feature_flags import driver_feature_enabled

    try:
        drivers = list((await session.execute(select(Driver))).scalars().all())
    except Exception:
        return
    text = (
        f"🚕 <b>Yangi yo'lovchi so'rovi</b> (#{order.id})\n"
        f"📍 {order.direction_display()}\n"
        f"🕐 {order.departure_time or '—'}\n"
        f"👥 {order.seats or '—'}\n"
        f"📞 <code>{order.phone}</code>"
    )
    for d in drivers:
        if not d.is_access_valid():
            continue
        if not driver_feature_enabled("passenger_dm", d):
            continue
        try:
            await bot.send_message(d.telegram_id, text)
        except TelegramForbiddenError:
            continue
        except Exception:
            continue


async def _handle_driver_book(
    message: Message,
    session,
    user: User,
    payload: str,
) -> None:
    """Guruhdagi «Zakas berish» — haydovchiga xabar + mijozga telefon."""
    try:
        driver_id = int(payload.split("_", 1)[1])
    except (ValueError, IndexError):
        await message.answer("Noto'g'ri havola.", reply_markup=main_menu_kb())
        return
    driver = await get_driver(session, driver_id)
    if driver is None or not driver.is_legacy_or_approved() or not driver.is_access_valid():
        await message.answer(
            "Haydovchi topilmadi yoki hozir band emas.",
            reply_markup=main_menu_kb(),
        )
        return

    uname = f"@{user.username}" if user.username else "—"
    try:
        await message.bot.send_message(
            driver_id,
            "📩 <b>Yangi zakas so'rovi</b>\n\n"
            f"Mijoz e'loningizga qiziqdi.\n"
            f"👤 {user.full_name}\n"
            f"🆔 <code>{user.id}</code>\n"
            f"🔗 {uname}\n\n"
            "Tez orada bog'laning yoki javob yozing.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✍️ Mijozga yozish",
                            url=contact_url(user.id, user.username),
                        )
                    ]
                ]
            ),
        )
    except TelegramForbiddenError:
        logger.info("Haydovchiga zakas xabari yetmadi driver=%s", driver_id)

    phone = normalize_uz_phone(driver.phone)
    lines = [
        f"✅ <b>{driver.full_name}</b> haydovchiga xabar yuborildi.",
        f"🚘 {driver.car_model} · {driver.car_number}",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if phone:
        lines.append(f"☎️ <code>{phone}</code>")
        rows.append(
            [InlineKeyboardButton(text=f"📞 {phone}", url=phone_tel_url(phone))]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✍️ Haydovchiga yozish",
                url=contact_url(driver.telegram_id, driver.username),
            )
        ]
    )
    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


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
async def cmd_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    command: CommandObject,
) -> None:
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

    # Guruhdagi "Buyurtmani olish" → t.me/bot?start=claim_123
    # Ro'yxatdan o'tish shart emas — guruh a'zosi to'g'ridan-to'g'ri olishi mumkin.
    payload = (command.args or "").strip()
    if payload.startswith("claim_"):
        try:
            order_id = int(payload.split("_", 1)[1])
        except (ValueError, IndexError):
            await message.answer("Noto'g'ri buyurtma havolasi.", reply_markup=main_menu_kb())
            return
        from bot.handlers.group import process_claim

        result_text = await process_claim(
            message.bot, session, order_id=order_id, user=user
        )
        await message.answer(result_text, reply_markup=main_menu_kb())
        return

    # Haydovchi e'lonidagi "Zakas berish" → t.me/bot?start=book_<driver_id>
    if payload.startswith("book_"):
        await _handle_driver_book(message, session, user, payload)
        return

    # Reklama deep link: t.me/bot?start=order
    if payload == "order" or payload.startswith("order"):
        from bot.services.bot_ads import record_ad_click

        await record_ad_click(session, user.id)
        await session.commit()
        await state.update_data(order_source="ad")
        await message.answer(
            "🚕 <b>Taksi chaqirish</b>\n\nYo'nalishni tanlang:",
            reply_markup=main_menu_kb(),
        )
        return

    # Mavjud tasdiqlangan haydovchi — qayta ro'yxat YO'Q, darhol panel.
    driver = await get_driver(session, user.id)
    if driver is not None and driver.is_legacy_or_approved() and driver.is_access_valid():
        from bot.handlers.driver import show_driver_panel

        await message.answer(
            "Assalomu alaykum!\n\n"
            "E'lon matnini shu yerga yozing yoki forward qiling — "
            "bot barcha guruhlarga yuboradi.\n"
            "Yoki «📢 E'lon berish» tugmasidan foydalaning."
        )
        await show_driver_panel(message, driver)
        return
    if driver is not None and (
        driver.is_soft_deleted() or driver.status == DriverStatus.BANNED.value
    ):
        await message.answer(
            "⛔ Haydovchi profilingiz yopiq.\n\nYo'lovchi sifatida buyurtma berishingiz mumkin:",
            reply_markup=main_menu_kb(),
        )
        return
    if driver is not None and driver.status == DriverStatus.PENDING.value:
        await message.answer(
            "Assalomu alaykum!\n\n"
            "⏳ Haydovchi arizangiz ko'rib chiqilmoqda.\n"
            "Tasdiqlangach panel avtomatik ochiladi.\n\n"
            "Hozircha yo'lovchi sifatida buyurtma berishingiz mumkin:",
            reply_markup=main_menu_kb(),
        )
        return

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


@router.message(F.text.in_({BTN_BT, BTN_TB}), ~StateFilter(DriverAnnounce.direction))
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

    data = await state.get_data()
    order_source = data.get("order_source")
    await state.clear()
    await state.update_data(direction=direction, order_source=order_source)
    await state.set_state(TaxiOrder.departure)
    await message.answer(
        f"🚖 Yo'nalish: <b>{label}</b>\n\n"
        "Qachon ketasiz? Qisqa yozing (masalan: <i>Bugun 16:00</i> yoki <i>Hozir</i>).",
        reply_markup=main_menu_kb(),
    )


@router.message(TaxiOrder.departure, F.text)
async def taxi_departure(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await _cancel_fsm(message, state)
        return
    text = (message.text or "").strip()
    if len(text) < 2 or len(text) > 80:
        await message.answer("Vaqtni 2–80 belgi orasida yozing.")
        return
    # Yo'nalish tugmalari bosilsa — qayta yo'nalish
    if text in {BTN_BT, BTN_TB, BTN_PARCEL}:
        return
    await state.update_data(departure_time=text)
    await state.set_state(TaxiOrder.seats)
    await message.answer(
        f"🕐 Vaqt: <b>{text}</b>\n\nNecha kishi ketadi?",
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
    source = data.get("order_source")
    order = await create_order(
        session,
        order_type=OrderType.TAXI.value,
        passenger_id=user.id,
        direction=data["direction"],
        phone=phone,
        seats=data.get("seats"),
        departure_time=data.get("departure_time"),
        source=source if isinstance(source, str) else None,
    )
    await session.commit()
    await state.clear()

    from bot.handlers.group import publish_order_to_group

    posted = await publish_order_to_group(message.bot, session, order)
    await _notify_passenger_dm_drivers(message.bot, session, order)
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
