"""Haydovchi profili, panel, e'lon, kelishuv va bekor qilish."""

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
    get_driver,
    get_order,
    submit_driver_application,
    update_driver_fields,
)
from bot.database.models import (
    CANCEL_REASON_LABELS,
    DIRECTION_LABELS,
    DRIVER_FIELD_LABELS,
    Driver,
    DriverStatus,
    OrderStatus,
)
from bot.handlers.group import create_driver_invite_links, edit_group_card
from bot.keyboards.default_kb import (
    BTN_ANNOUNCE,
    BTN_BT,
    BTN_CANCEL,
    BTN_DRIVER_GROUPS,
    BTN_PASSENGER_MENU,
    BTN_PROFILE,
    BTN_SKIP,
    BTN_TB,
    DIRECTION_BY_BTN,
    DRIVER_START_BUTTONS,
    cancel_kb,
    contact_kb,
    driver_panel_kb,
    main_menu_kb,
    skip_kb,
)
from bot.keyboards.inline_kb import (
    cancel_reason_kb,
    claim_kb,
    driver_approval_kb,
    group_invite_kb,
)
from bot.services.notify import notify_admins
from bot.states.order_states import DriverAnnounce, DriverMissing, DriverReg

logger = logging.getLogger(__name__)
router = Router(name="driver")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

CAR_NUMBER_RE = re.compile(r"^[A-Za-z0-9\-\s]{5,15}$")
PHONE_RE = re.compile(r"^\+?\d{9,15}$")

MISSING_PROMPTS: dict[str, str] = {
    "phone": "📞 Telefon raqamingizni yuboring:",
    "car_model": "Mashina rusumingizni yozing (masalan: <i>Cobalt</i>):",
    "car_number": "Mashina raqamingizni yozing (masalan: <i>40 A 123 BA</i>):",
    "car_color": "Mashina rangini yozing yoki o'tkazib yuboring:",
    "seats_count": "O'rinlar sonini yozing (1–8) yoki o'tkazib yuboring:",
    "photo_file_id": "Mashina rasmini yuboring yoki o'tkazib yuboring:",
}


def _fmt_until(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%d.%m.%Y %H:%M")


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


def _normalize_plate(raw: str) -> str | None:
    number = re.sub(r"\s+", " ", raw.strip()).upper()
    if not CAR_NUMBER_RE.match(number):
        return None
    return number


async def show_driver_panel(message: Message, driver: Driver) -> None:
    extra = (
        f"📅 Obuna: {_fmt_until(driver.subscription_until)}"
        if driver.has_paid_subscription()
        else "📌 Profil tasdiqlangan"
    )
    missing = driver.missing_required_fields()
    miss_line = ""
    if missing:
        labels = ", ".join(DRIVER_FIELD_LABELS[k] for k in missing)
        miss_line = f"\n⚠️ Yetishmayotgan: <b>{labels}</b> (e'lon berishda so'raladi)"
    await message.answer(
        f"🚘 <b>Haydovchi paneli</b>\n\n"
        f"👤 {driver.full_name}\n"
        f"🚘 {driver.car_model or '—'} · {driver.car_number or '—'}\n"
        f"📞 {driver.phone or '—'}\n"
        f"{extra}{miss_line}\n\n"
        f"⭐ Asosiy guruh: <b>{settings.primary_group_title}</b>",
        reply_markup=driver_panel_kb(),
    )


async def _ask_next_missing(message: Message, state: FSMContext) -> bool:
    """Keyingi yetishmagan maydonni so'raydi. False = navbat tugadi."""
    data = await state.get_data()
    queue: list[str] = list(data.get("missing_queue") or [])
    optional = bool(data.get("missing_optional"))
    if not queue:
        return False
    field = queue[0]
    await state.update_data(missing_queue=queue, current_missing=field)
    prompt = MISSING_PROMPTS.get(field, f"{field} ni yozing:")
    if field == "phone":
        await message.answer(prompt, reply_markup=contact_kb())
    elif optional or field in {"car_color", "seats_count", "photo_file_id"}:
        await message.answer(prompt, reply_markup=skip_kb())
    else:
        await message.answer(prompt, reply_markup=cancel_kb())
    return True


async def _finish_missing_flow(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    resume = data.get("resume_action")
    await state.clear()
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None:
        await message.answer("Profil topilmadi.", reply_markup=main_menu_kb())
        return
    if resume == "announce":
        if driver.missing_required_fields():
            await message.answer(
                "Majburiy maydonlar hali to'liq emas.",
                reply_markup=driver_panel_kb(),
            )
            return
        await _start_announce(message, state)
        return
    await show_driver_panel(message, driver)


async def ensure_required_or_ask(
    message: Message,
    state: FSMContext,
    driver: Driver,
    *,
    resume_action: str,
) -> bool:
    """True = hammasi joyida. False = so'rov boshlandi."""
    missing = driver.missing_required_fields()
    if not missing:
        return True
    await state.clear()
    await state.set_state(DriverMissing.collecting)
    await state.update_data(
        missing_queue=missing,
        missing_optional=False,
        resume_action=resume_action,
    )
    labels = ", ".join(DRIVER_FIELD_LABELS[k] for k in missing)
    await message.answer(
        f"E'lon uchun quyidagilar kerak: <b>{labels}</b>.\n"
        "Faqat yetishmayotganlari so'raladi.",
        reply_markup=cancel_kb(),
    )
    await _ask_next_missing(message, state)
    return False


# ----- / panel tugmalari -----


@router.message(F.text.in_(DRIVER_START_BUTTONS))
async def start_driver_reg(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is not None:
        if driver.status == DriverStatus.BANNED.value:
            await message.answer("Sizning profilingiz bloklangan. Admin bilan bog'laning.")
            return
        if driver.status == DriverStatus.PENDING.value:
            await message.answer(
                "⏳ Arizangiz ko'rib chiqilmoqda. Tasdiqlangach panel ochiladi.",
                reply_markup=main_menu_kb(),
            )
            return
        if driver.status == DriverStatus.REJECTED.value:
            await message.answer(
                "Arizangiz rad etilgan. Qayta topshirish uchun ma'lumotlarni yuboring.",
                reply_markup=cancel_kb(),
            )
            await _start_full_registration(message, state, prefill_name=user.full_name)
            return
        if driver.is_legacy_or_approved():
            if driver.is_access_valid():
                await show_driver_panel(message, driver)
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

    await _start_full_registration(message, state, prefill_name=user.full_name)


async def _start_full_registration(
    message: Message,
    state: FSMContext,
    *,
    prefill_name: str | None = None,
) -> None:
    await state.clear()
    await state.set_state(DriverReg.name)
    if prefill_name and len(prefill_name.strip()) >= 3:
        await state.update_data(suggested_name=prefill_name.strip())
    await message.answer(
        "🚖 <b>Haydovchi bo'lish</b>\n\n"
        "Ariza adminlarga yuboriladi. Tasdiqlangach panel ochiladi.\n\n"
        "To'liq ismingizni yozing:",
        reply_markup=cancel_kb(),
    )


@router.message(F.text == BTN_ANNOUNCE)
async def on_announce(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None or not driver.is_legacy_or_approved():
        await message.answer(
            "Avval haydovchi sifatida ro'yxatdan o'ting.",
            reply_markup=main_menu_kb(),
        )
        return
    if driver.status == DriverStatus.BANNED.value:
        await message.answer("Profilingiz bloklangan.")
        return
    if not await ensure_required_or_ask(message, state, driver, resume_action="announce"):
        return
    await _start_announce(message, state)


def _announce_direction_kb() -> "ReplyKeyboardMarkup":
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BT)],
            [KeyboardButton(text=BTN_TB)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


async def _start_announce(message: Message, state: FSMContext) -> None:
    await state.set_state(DriverAnnounce.direction)
    await message.answer(
        "Yo'nalishni tanlang:",
        reply_markup=_announce_direction_kb(),
    )


@router.message(DriverAnnounce.direction, F.text.in_({BTN_BT, BTN_TB}))
async def announce_direction(message: Message, state: FSMContext) -> None:
    direction = DIRECTION_BY_BTN[message.text or ""]
    await state.update_data(direction=direction)
    await state.set_state(DriverAnnounce.free_seats)
    await message.answer(
        "Nechta bo'sh joy bor? (1–4 yoki «Butun salon»)",
        reply_markup=cancel_kb(),
    )


@router.message(DriverAnnounce.direction, F.text == BTN_CANCEL)
async def announce_dir_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=driver_panel_kb())


@router.message(DriverAnnounce.free_seats, F.text)
async def announce_seats(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=driver_panel_kb())
        return
    seats = (message.text or "").strip()
    if len(seats) < 1 or len(seats) > 40:
        await message.answer("Bo'sh joy sonini yozing (masalan: 2).")
        return
    data = await state.get_data()
    direction = data.get("direction")
    user = message.from_user
    if user is None or not direction:
        await state.clear()
        await message.answer("Xatolik. Qaytadan urinib ko'ring.", reply_markup=driver_panel_kb())
        return
    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await message.answer("Profil topilmadi.", reply_markup=main_menu_kb())
        return

    origin, dest = DIRECTION_LABELS.get(direction, ("Beshariq", "Toshkent"))
    color = f" · {driver.car_color}" if driver.car_color else ""
    text = (
        f"📢 <b>Haydovchi e'loni</b>\n\n"
        f"👤 <b>{driver.full_name}</b>\n"
        f"🚘 {driver.car_model} · <code>{driver.car_number}</code>{color}\n"
        f"📍 {origin} ➡️ {dest}\n"
        f"👥 Bo'sh joy: <b>{seats}</b>\n"
        f"📞 <code>{driver.phone}</code>"
    )
    posted = 0
    for chat_id in settings.group_ids:
        try:
            if driver.photo_file_id:
                await message.bot.send_photo(
                    chat_id, photo=driver.photo_file_id, caption=text
                )
            else:
                await message.bot.send_message(chat_id, text)
            posted += 1
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning("E'lon yuborilmadi %s: %s", chat_id, exc)

    await state.clear()
    if posted:
        await message.answer(
            f"✅ E'lon {posted} ta guruhga yuborildi.",
            reply_markup=driver_panel_kb(),
        )
    else:
        await message.answer(
            "E'lon yuborilmadi. Bot guruhlarda admin ekanini tekshiring.",
            reply_markup=driver_panel_kb(),
        )


@router.message(F.text == BTN_PROFILE)
async def on_profile(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None or not driver.is_legacy_or_approved():
        await message.answer("Haydovchi profili yo'q.", reply_markup=main_menu_kb())
        return
    opt = driver.missing_optional_fields()
    req = driver.missing_required_fields()
    lines = [
        "👤 <b>Profilingiz</b>\n",
        f"Ism: <b>{driver.full_name}</b>",
        f"Telefon: <code>{driver.phone or '—'}</code>",
        f"Mashina: {driver.car_model or '—'} · {driver.car_number or '—'}",
        f"Rang: {driver.car_color or '—'}",
        f"O'rinlar: {driver.seats_count if driver.seats_count else '—'}",
        f"Rasm: {'bor' if driver.photo_file_id else 'yoq'}",
        f"Status: <b>{driver.status}</b>",
    ]
    if req:
        lines.append(
            "\n⚠️ Majburiy yetishmayapti: "
            + ", ".join(DRIVER_FIELD_LABELS[k] for k in req)
        )
    if opt:
        lines.append(
            "\nIxtiyoriy to'ldirilmagan: "
            + ", ".join(DRIVER_FIELD_LABELS[k] for k in opt)
        )
    await message.answer("\n".join(lines), reply_markup=driver_panel_kb())
    fill_queue = req + opt
    if fill_queue:
        await state.set_state(DriverMissing.collecting)
        await state.update_data(
            missing_queue=fill_queue,
            missing_optional=True,  # ixtiyoriylarni o'tkazish mumkin; majburiy skip tekshiruvi alohida
            resume_action="profile",
        )
        await message.answer("Yetishmayotgan maydonlarni birma-bir to'ldiramiz:")
        await _ask_next_missing(message, state)


@router.message(F.text == BTN_DRIVER_GROUPS)
async def on_driver_groups(message: Message, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None or not driver.is_legacy_or_approved():
        await message.answer("Avval haydovchi bo'ling.", reply_markup=main_menu_kb())
        return
    links = await create_driver_invite_links(message.bot)
    await message.answer(
        f"⭐ Asosiy: <b>{settings.primary_group_title}</b>\n"
        f"{settings.primary_group_invite}",
        reply_markup=group_invite_kb(links),
    )


@router.message(F.text == BTN_PASSENGER_MENU)
async def on_passenger_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Yo'lovchi menyusi:", reply_markup=main_menu_kb())


# ----- Yetishmagan maydonlar -----


@router.message(DriverMissing.collecting, F.contact)
async def missing_contact(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    if data.get("current_missing") != "phone":
        await message.answer("Hozir telefon kutilmayapti.")
        return
    if message.contact and message.contact.user_id and message.contact.user_id != user.id:
        await message.answer("Faqat o'z raqamingizni yuboring.", reply_markup=contact_kb())
        return
    phone = _normalize_phone(message.contact.phone_number if message.contact else "")
    if not phone:
        await message.answer("Raqam o'qilmadi. Qayta yuboring.", reply_markup=contact_kb())
        return
    await update_driver_fields(session, user.id, phone=phone)
    await session.commit()
    queue = list(data.get("missing_queue") or [])
    if queue and queue[0] == "phone":
        queue.pop(0)
    await state.update_data(missing_queue=queue, current_missing=None)
    if not await _ask_next_missing(message, state):
        await _finish_missing_flow(message, state, session)


@router.message(DriverMissing.collecting, F.photo)
async def missing_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    if data.get("current_missing") != "photo_file_id":
        await message.answer("Hozir rasm kutilmayapti. Matn yuboring.")
        return
    photo = message.photo[-1]
    await update_driver_fields(session, user.id, photo_file_id=photo.file_id)
    await session.commit()
    queue = list(data.get("missing_queue") or [])
    if queue and queue[0] == "photo_file_id":
        queue.pop(0)
    await state.update_data(missing_queue=queue, current_missing=None)
    if not await _ask_next_missing(message, state):
        await _finish_missing_flow(message, state, session)


@router.message(DriverMissing.collecting, F.text)
async def missing_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = message.from_user
    if user is None:
        return
    text = (message.text or "").strip()
    if text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=driver_panel_kb())
        return

    data = await state.get_data()
    field = data.get("current_missing")
    queue = list(data.get("missing_queue") or [])
    optional = bool(data.get("missing_optional"))

    if text == BTN_SKIP:
        if field in {"phone", "car_model", "car_number"}:
            await message.answer("Bu maydon majburiy — o'tkazib bo'lmaydi.")
            return
        if field not in {"car_color", "seats_count", "photo_file_id"} and not optional:
            await message.answer("Bu maydonni o'tkazib bo'lmaydi.")
            return
        if queue:
            queue.pop(0)
        await state.update_data(missing_queue=queue, current_missing=None)
        if not await _ask_next_missing(message, state):
            await _finish_missing_flow(message, state, session)
        return

    if not field:
        if not await _ask_next_missing(message, state):
            await _finish_missing_flow(message, state, session)
        return

    kwargs: dict = {}
    if field == "phone":
        phone = _normalize_phone(text)
        if not phone:
            await message.answer(
                "Raqamni tugma orqali yuboring yoki +998... yozing.",
                reply_markup=contact_kb(),
            )
            return
        kwargs["phone"] = phone
    elif field == "car_model":
        if len(text) < 2 or len(text) > 80:
            await message.answer("Rusum 2–80 belgi orasida bo'lsin.")
            return
        kwargs["car_model"] = text
    elif field == "car_number":
        number = _normalize_plate(text)
        if not number:
            await message.answer("Raqam formati noto'g'ri. Qayta yozing.")
            return
        kwargs["car_number"] = number
    elif field == "car_color":
        if len(text) < 2 or len(text) > 40:
            await message.answer("Rang 2–40 belgi bo'lsin yoki o'tkazib yuboring.")
            return
        kwargs["car_color"] = text
    elif field == "seats_count":
        if not text.isdigit() or not (1 <= int(text) <= 8):
            await message.answer("1–8 orasidagi son yozing yoki o'tkazib yuboring.")
            return
        kwargs["seats_count"] = int(text)
    elif field == "photo_file_id":
        await message.answer("Rasm yuboring yoki «O'tkazib yuborish»ni bosing.", reply_markup=skip_kb())
        return
    else:
        await message.answer("Noma'lum maydon.")
        return

    await update_driver_fields(session, user.id, **kwargs)
    await session.commit()
    if queue and queue[0] == field:
        queue.pop(0)
    await state.update_data(missing_queue=queue, current_missing=None)
    if not await _ask_next_missing(message, state):
        await _finish_missing_flow(message, state, session)


# ----- Yangi haydovchi to'liq ariza -----


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
    await state.set_state(DriverReg.phone)
    await message.answer("Telefon raqamingizni yuboring:", reply_markup=contact_kb())


@router.message(DriverReg.phone, F.contact)
async def driver_phone_contact(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    if message.contact and message.contact.user_id and message.contact.user_id != user.id:
        await message.answer("Faqat o'z raqamingizni yuboring.", reply_markup=contact_kb())
        return
    phone = _normalize_phone(message.contact.phone_number if message.contact else "")
    if not phone:
        await message.answer("Raqam o'qilmadi.", reply_markup=contact_kb())
        return
    await state.update_data(phone=phone)
    await state.set_state(DriverReg.car_model)
    await message.answer(
        "Mashina rusumini yozing (masalan: <i>Nexia 3</i>, <i>Cobalt</i>):",
        reply_markup=cancel_kb(),
    )


@router.message(DriverReg.phone, F.text)
async def driver_phone_text(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    phone = _normalize_phone(message.text or "")
    if not phone:
        await message.answer("Raqamni tugma orqali yuboring.", reply_markup=contact_kb())
        return
    await state.update_data(phone=phone)
    await state.set_state(DriverReg.car_model)
    await message.answer(
        "Mashina rusumini yozing (masalan: <i>Nexia 3</i>, <i>Cobalt</i>):",
        reply_markup=cancel_kb(),
    )


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
    number = _normalize_plate(message.text or "")
    if not number:
        await message.answer("Raqam formati noto'g'ri. Qayta yozing.")
        return
    await state.update_data(car_number=number)
    await state.set_state(DriverReg.car_color)
    await message.answer(
        "Mashina rangini yozing (ixtiyoriy):",
        reply_markup=skip_kb(),
    )


@router.message(DriverReg.car_color, F.text)
async def driver_car_color(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    if message.text != BTN_SKIP:
        color = (message.text or "").strip()
        if len(color) < 2 or len(color) > 40:
            await message.answer("Rang 2–40 belgi yoki o'tkazib yuboring.")
            return
        await state.update_data(car_color=color)
    await state.set_state(DriverReg.seats_count)
    await message.answer("O'rinlar sonini yozing (1–8, ixtiyoriy):", reply_markup=skip_kb())


@router.message(DriverReg.seats_count, F.text)
async def driver_seats(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    if message.text != BTN_SKIP:
        text = (message.text or "").strip()
        if not text.isdigit() or not (1 <= int(text) <= 8):
            await message.answer("1–8 yoki o'tkazib yuboring.")
            return
        await state.update_data(seats_count=int(text))
    await state.set_state(DriverReg.photo)
    await message.answer("Mashina rasmini yuboring (ixtiyoriy):", reply_markup=skip_kb())


@router.message(DriverReg.photo, F.photo)
async def driver_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    photo = message.photo[-1]
    await state.update_data(photo_file_id=photo.file_id)
    await _finish_new_application(message, state, session)


@router.message(DriverReg.photo, F.text)
async def driver_photo_skip(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    if message.text != BTN_SKIP:
        await message.answer("Rasm yuboring yoki o'tkazib yuboring.", reply_markup=skip_kb())
        return
    await _finish_new_application(message, state, session)


async def _finish_new_application(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    driver, is_new_app = await submit_driver_application(
        session,
        telegram_id=user.id,
        full_name=data["full_name"],
        username=user.username,
        car_model=data["car_model"],
        car_number=data["car_number"],
        phone=data["phone"],
        car_color=data.get("car_color"),
        seats_count=data.get("seats_count"),
        photo_file_id=data.get("photo_file_id"),
    )
    await session.commit()
    await state.clear()

    if driver.status == DriverStatus.BANNED.value:
        await message.answer(
            "Profilingiz bloklangan. Admin bilan bog'laning.",
            reply_markup=main_menu_kb(),
        )
        return

    if driver.is_legacy_or_approved():
        await message.answer("Ma'lumotlar yangilandi.")
        await show_driver_panel(message, driver)
        return

    await message.answer(
        "⏳ Arizangiz yuborildi. Admin tasdiqlagach haydovchi paneli ochiladi.",
        reply_markup=main_menu_kb(),
    )
    if is_new_app or driver.status == DriverStatus.PENDING.value:
        uname = f"@{driver.username}" if driver.username else "—"
        color = driver.car_color or "—"
        seats = driver.seats_count if driver.seats_count else "—"
        await notify_admins(
            message.bot,
            "🆕 <b>Yangi haydovchi arizasi</b>\n\n"
            f"👤 Ism: <b>{driver.full_name}</b>\n"
            f"🆔 Telegram ID: <code>{user.id}</code>\n"
            f"🔗 Username: {uname}\n"
            f"🚘 Mashina: {driver.car_model} · {driver.car_number}\n"
            f"🎨 Rang: {color} · O'rin: {seats}\n"
            f"📞 Telefon: <code>{driver.phone}</code>",
            reply_markup=driver_approval_kb(user.id),
        )


# ----- Deal callbacks (eski oqim) -----


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
        await callback.answer(
            "Bu buyurtmani tasdiqlay olmaysiz (3 daqiqlik vaqt tugagan bo'lishi mumkin).",
            show_alert=True,
        )
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
        await callback.answer(
            "Bekor qilish mumkin emas (3 daqiqlik vaqt tugagan bo'lishi mumkin).",
            show_alert=True,
        )
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
