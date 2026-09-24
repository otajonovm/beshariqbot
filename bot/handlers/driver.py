"""Haydovchi profili, guruh havolasi, kelishuv va bekor qilish."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.db_requests import (
    cancel_deal_and_reopen,
    confirm_deal,
    create_driver,
    get_driver,
    get_order,
)
from bot.database.models import CANCEL_REASON_LABELS, DriverStatus, OrderStatus
from bot.handlers.group import create_driver_invite_links, edit_group_card
from bot.keyboards.default_kb import (
    BTN_CANCEL,
    BTN_DRIVER,
    cancel_kb,
    contact_kb,
    main_menu_kb,
)
from bot.keyboards.inline_kb import cancel_reason_kb, claim_kb, group_invite_kb
from bot.services.notify import notify_admins
from bot.states.order_states import DriverReg

logger = logging.getLogger(__name__)
router = Router(name="driver")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

CAR_NUMBER_RE = re.compile(r"^[A-Za-z0-9\-\s]{5,15}$")


def _fmt_until(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%d.%m.%Y %H:%M")


async def _start_profile_fsm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(DriverReg.name)
    await message.answer(
        "🚘 <b>Haydovchi profilini to'ldirish</b>\n\n"
        "Birinchi marta taxi sifatida ulanish uchun profilingizni to'ldiring.\n\n"
        "To'liq ismingizni yozing:",
        reply_markup=cancel_kb(),
    )


@router.message(F.text == BTN_DRIVER)
async def start_driver_reg(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is not None:
        if driver.status == DriverStatus.BANNED.value:
            await message.answer("Sizning profilingiz bloklangan. Admin bilan bog'laning.")
            return
        if not driver.is_profile_complete():
            await _start_profile_fsm(message, state)
            return
        if driver.is_access_valid():
            extra = (
                f"📅 Obuna: {_fmt_until(driver.subscription_until)}"
                if driver.has_paid_subscription()
                else "📌 Profil to'ldirilgan"
            )
            links = await create_driver_invite_links(message.bot)
            kb = group_invite_kb(links)
            text = (
                "Siz allaqachon haydovchisiz.\n"
                f"🚘 {driver.car_model} · {driver.car_number}\n"
                f"{extra}\n\n"
                f"⭐ Asosiy guruh: <b>{settings.primary_group_title}</b>\n"
                "Zakaslar avval shu yerga tushadi. Kirish:\n"
                f"{settings.primary_group_invite}"
            )
            await message.answer(text, reply_markup=kb)
            await message.answer("Asosiy menyu:", reply_markup=main_menu_kb())
            return
        await message.answer(
            "Obuna muddati tugagan. Qayta ulanish uchun admin oylik to'lovni tasdiqlashi kerak.\n"
            "Admin bilan bog'laning.",
            reply_markup=main_menu_kb(),
        )
        await notify_admins(
            message.bot,
            f"💳 Haydovchi qayta ulanmoqchi:\n"
            f"ID: <code>{user.id}</code>\n"
            f"Ism: {driver.full_name}\n"
            f"Mashina: {driver.car_model} {driver.car_number}",
        )
        return

    await _start_profile_fsm(message, state)


@router.message(DriverReg.name, F.text)
async def driver_name(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    name = (message.text or "").strip()
    if len(name) < 3 or len(name) > 80:
        await message.answer("Ism 3–80 belgi orasida bo'lsin.")
        return
    await state.update_data(full_name=name)
    await state.set_state(DriverReg.car_model)
    await message.answer("Mashina rusumini yozing (masalan: <i>Nexia 3</i>, <i>Cobalt</i>):")


@router.message(DriverReg.car_model, F.text)
async def driver_car_model(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    model = (message.text or "").strip()
    if len(model) < 2 or len(model) > 80:
        await message.answer("Rusum 2–80 belgi orasida bo'lsin.")
        return
    await state.update_data(car_model=model)
    await state.set_state(DriverReg.car_number)
    await message.answer("Davlat raqamini yozing (masalan: <i>40 A 123 BA</i>):")


@router.message(DriverReg.car_number, F.text)
async def driver_car_number(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    number = re.sub(r"\s+", " ", (message.text or "").strip()).upper()
    if not CAR_NUMBER_RE.match(number):
        await message.answer("Raqam formati noto'g'ri. Qayta yozing.")
        return
    await state.update_data(car_number=number)
    await state.set_state(DriverReg.phone)
    await message.answer("Telefon raqamingizni yuboring:", reply_markup=contact_kb())


@router.message(DriverReg.phone, F.contact)
async def driver_phone_contact(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _finish_driver_reg(message, state, session, message.contact.phone_number)


@router.message(DriverReg.phone, F.text)
async def driver_phone_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    phone = re.sub(r"[\s\-()]", "", message.text or "")
    if not re.match(r"^\+?\d{9,15}$", phone):
        await message.answer("Raqamni tugma orqali yuboring.", reply_markup=contact_kb())
        return
    await _finish_driver_reg(message, state, session, phone)


async def _finish_driver_reg(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    phone: str,
) -> None:
    user = message.from_user
    if user is None:
        return
    if message.contact and message.contact.user_id and message.contact.user_id != user.id:
        await message.answer("Faqat o'z raqamingizni yuboring.", reply_markup=contact_kb())
        return

    data = await state.get_data()
    driver = await create_driver(
        session,
        telegram_id=user.id,
        full_name=data["full_name"],
        username=user.username,
        car_model=data["car_model"],
        car_number=data["car_number"],
        phone=phone if phone.startswith("+") else f"+{phone.lstrip('+')}",
    )
    await session.commit()
    await state.clear()

    links = await create_driver_invite_links(message.bot)
    kb = group_invite_kb(links)
    registered_at = _fmt_until(driver.created_at or driver.trial_start)
    text = (
        "🎉 <b>Tabriklaymiz!</b>\n\n"
        "Siz <b>Karvon Taxi</b> da haydovchi sifatida muvaffaqiyatli "
        "ro'yxatdan o'tdingiz.\n\n"
        f"🚘 {driver.car_model} · {driver.car_number}\n"
        f"📅 Ro'yxatdan o'tgan vaqt: <b>{registered_at}</b>\n\n"
        f"⭐ Asosiy guruh: <b>{settings.primary_group_title}</b>\n"
        "Taksichilar shu yerga qo'shiladi, zakaslar avval shu guruhga tushadi:\n"
        f"{settings.primary_group_invite}"
    )
    await message.answer(text, reply_markup=kb)
    await message.answer("Asosiy menyu:", reply_markup=main_menu_kb())

    uname = f"@{driver.username}" if driver.username else "—"
    await notify_admins(
        message.bot,
        "🆕 <b>Yangi haydovchi (taksi) ro'yxatdan o'tdi</b>\n\n"
        f"👤 Ism: <b>{driver.full_name}</b>\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n"
        f"🔗 Username: {uname}\n"
        f"🚘 Mashina: {driver.car_model} · {driver.car_number}\n"
        f"📞 Telefon: <code>{driver.phone}</code>\n"
        f"📅 Vaqt: <b>{registered_at}</b>",
    )


@router.callback_query(F.data.startswith("deal_ok:"))
async def deal_ok(callback: CallbackQuery, session: AsyncSession) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    order = await confirm_deal(session, order_id, user.id)
    if order is None:
        await callback.answer("Bu buyurtmani tasdiqlay olmaysiz.", show_alert=True)
        return

    driver = await get_driver(session, user.id)
    name = driver.full_name if driver else user.full_name
    await edit_group_card(callback.bot, order, order.to_confirmed_group_text(name), reply_markup=None)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(f"✅ #{order.id} tasdiqlandi. Oq yo'l!")

    try:
        await callback.bot.send_message(
            order.passenger_id,
            f"✅ #{order.id} buyurtmangiz tasdiqlandi. Haydovchi siz bilan kelishdi. Oq yo'l!",
        )
    except TelegramForbiddenError:
        pass
    await callback.answer("Tasdiqlandi.")


@router.callback_query(F.data.startswith("deal_cancel:"))
async def deal_cancel(callback: CallbackQuery, session: AsyncSession) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return

    order = await get_order(session, order_id)
    if order is None or order.driver_id != user.id or order.status != OrderStatus.ACCEPTED.value:
        await callback.answer("Bekor qilish mumkin emas.", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=cancel_reason_kb(order_id))
    except TelegramBadRequest:
        await callback.message.answer("Sababni tanlang:", reply_markup=cancel_reason_kb(order_id))
    await callback.answer()


@router.callback_query(F.data.startswith("creason:"))
async def cancel_reason(callback: CallbackQuery, session: AsyncSession) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return
    try:
        order_id = int(parts[1])
    except ValueError:
        await callback.answer("Noto'g'ri so'rov.", show_alert=True)
        return
    reason = parts[2]
    if reason not in CANCEL_REASON_LABELS:
        await callback.answer("Noto'g'ri sabab.", show_alert=True)
        return

    order = await cancel_deal_and_reopen(session, order_id, user.id, reason)
    if order is None:
        await callback.answer("Bu buyurtmani bekor qila olmaysiz.", show_alert=True)
        return

    await edit_group_card(
        callback.bot,
        order,
        order.to_group_text(reactivated=True),
        reply_markup=claim_kb(order.id),
    )
    from bot.database.db_requests import set_group_posts

    await set_group_posts(session, order.id, order.posts_map())
    await session.commit()

    try:
        await callback.message.edit_text(
            f"❌ #{order.id} bekor qilindi ({CANCEL_REASON_LABELS[reason]}).\n"
            "Bu buyurtmani qayta ololmaysiz. Guruhga qayta e'lon qilindi."
        )
    except TelegramBadRequest:
        await callback.message.answer(
            f"❌ #{order.id} bekor qilindi. Qayta e'lon guruhda."
        )

    try:
        await callback.bot.send_message(
            order.passenger_id,
            "Buyurtmangiz qayta shofyorlar guruhiga chiqarildi. "
            "Tez orada boshqa haydovchi chiqadi.",
        )
    except TelegramForbiddenError:
        pass
    await callback.answer("Buyurtma qayta faollashtirildi.")
