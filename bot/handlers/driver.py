"""Haydovchi profili, panel, e'lon, kelishuv va bekor qilish."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Filter
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
    BTN_REPEAT_LAST,
    BTN_SKIP,
    BTN_TB,
    DIRECTION_BY_BTN,
    DRIVER_START_BUTTONS,
    RESERVED_PRIVATE_TEXTS,
    cancel_kb,
    contact_kb,
    driver_panel_kb,
    main_menu_kb,
    skip_kb,
)
from bot.keyboards.inline_kb import (
    cancel_reason_kb,
    claim_kb,
    departure_time_kb,
    driver_approval_kb,
    group_invite_kb,
    profile_departure_kb,
    raw_post_confirm_kb,
)
from bot.services.departure import (
    MAX_CUSTOM_LEN,
    normalize_departure,
    resolve_template,
    strip_departure_line,
)
from bot.services.driver_raw_post import (
    RawPayload,
    check_post_limit,
    compose_body,
    format_result,
    get_last_raw,
    post_to_main,
    post_to_rest,
    record_post,
    save_last_raw,
)
from bot.services.notify import notify_admins
from bot.states.order_states import (
    DriverAnnounce,
    DriverDepartureEdit,
    DriverMissing,
    DriverRawPost,
    DriverReg,
)

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


def _panel_kb(driver: Driver | int | None = None) -> "ReplyKeyboardMarkup":
    if isinstance(driver, Driver):
        uid = driver.telegram_id
    elif isinstance(driver, int):
        uid = driver
    else:
        uid = None
    has_last = bool(uid and get_last_raw(uid))
    return driver_panel_kb(has_last=has_last)


def driver_guide_text() -> str:
    return (
        "💡 <b>E'lon matnini shu yerga tashlang</b> — bot barcha guruhlarga yuboradi.\n"
        "Yoki «📢 E'lon berish» tugmasidan foydalaning."
    )


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
        f"{driver_guide_text()}\n\n"
        f"⭐ Asosiy guruh: <b>{settings.primary_group_title}</b>",
        reply_markup=_panel_kb(driver),
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
    pending_raw = data.get("pending_raw")
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
                reply_markup=_panel_kb(driver),
            )
            return
        await _start_announce(message, state)
        return
    if resume == "raw" and isinstance(pending_raw, dict):
        if driver.missing_required_fields():
            await message.answer(
                "Majburiy maydonlar hali to'liq emas.",
                reply_markup=_panel_kb(driver),
            )
            return
        payload = RawPayload(
            text=str(pending_raw.get("text") or ""),
            photo_file_id=pending_raw.get("photo_file_id"),
            departure_text=pending_raw.get("departure_text"),
        )
        prefer = bool(pending_raw.get("departure_text") or driver.last_departure_text)
        await _offer_or_send_raw(
            message,
            state,
            session,
            driver,
            payload,
            prefer_saved_time=prefer,
        )
        return
    await show_driver_panel(message, driver)


async def ensure_required_or_ask(
    message: Message,
    state: FSMContext,
    driver: Driver,
    *,
    resume_action: str,
    extra_data: dict | None = None,
) -> bool:
    """True = hammasi joyida. False = so'rov boshlandi."""
    missing = driver.missing_required_fields()
    if not missing:
        return True
    keep = dict(extra_data or {})
    await state.clear()
    await state.set_state(DriverMissing.collecting)
    await state.update_data(
        missing_queue=missing,
        missing_optional=False,
        resume_action=resume_action,
        **keep,
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
    uid = message.from_user.id if message.from_user else None
    await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))


@router.message(DriverAnnounce.free_seats, F.text)
async def announce_seats(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        uid = message.from_user.id if message.from_user else None
        await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))
        return
    seats = (message.text or "").strip()
    if len(seats) < 1 or len(seats) > 40:
        await message.answer("Bo'sh joy sonini yozing (masalan: 2).")
        return
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await message.answer("Profil topilmadi.", reply_markup=main_menu_kb())
        return
    await state.update_data(free_seats=seats)
    await state.set_state(DriverAnnounce.departure)
    await message.answer(
        "🕐 Ketish vaqtini tanlang:",
        reply_markup=departure_time_kb(driver.last_departure_text, prefix="adep"),
    )


async def _publish_wizard_announce(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    departure: str,
) -> None:
    data = await state.get_data()
    direction = data.get("direction")
    seats = data.get("free_seats") or ""
    if not direction:
        await state.clear()
        await message.answer("Xatolik. Qaytadan urinib ko'ring.", reply_markup=_panel_kb(driver))
        return

    await update_driver_fields(session, driver.telegram_id, last_departure_text=departure)
    await session.commit()
    driver.last_departure_text = departure

    origin, dest = DIRECTION_LABELS.get(direction, ("Beshariq", "Toshkent"))
    # Stil parser uchun qisqa matn (vaqt alohida ketadi)
    seed = f"{origin}dan {dest}ga {seats} joy bor"
    payload = RawPayload(
        text=seed,
        photo_file_id=driver.photo_file_id,
        departure_text=departure,
        seats_hint=str(seats),
        direction=direction,
    )

    bot = message.bot
    chat_id = message.chat.id
    await state.clear()

    ok: list[int] = []
    failed: list[tuple[int, str]] = []
    main_id, main_err = await post_to_main(bot, driver, payload)
    if main_id is not None and main_err is None:
        ok.append(main_id)
    elif main_id is not None:
        failed.append((main_id, main_err or "xato"))

    async def _rest() -> None:
        skip = {main_id} if main_id is not None else set()
        rest_ok, rest_fail = await post_to_rest(bot, driver, payload, skip_chat_ids=skip)
        all_ok = ok + rest_ok
        all_fail = failed + rest_fail
        if all_ok:
            save_last_raw(driver.telegram_id, payload)
            record_post(driver.telegram_id)
        await bot.send_message(
            chat_id,
            format_result(all_ok, all_fail),
            reply_markup=_panel_kb(driver),
        )

    asyncio.create_task(_rest(), name=f"wizard-post-{driver.telegram_id}")


@router.callback_query(DriverAnnounce.departure, F.data.startswith("adep:"))
async def announce_departure_cb(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    code = callback.data.split(":", 1)[1]
    if code == "cancel":
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer("Bekor qilindi.", reply_markup=_panel_kb(user.id))
        await callback.answer()
        return

    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await callback.answer("Profil topilmadi.", show_alert=True)
        return

    if code == "custom":
        await state.set_state(DriverAnnounce.custom_time)
        await callback.message.answer(
            f"Ketish vaqtini yozing (max {MAX_CUSTOM_LEN} belgi):\n"
            "<i>Masalan: Bugun soat 18:00 da Beshariqdan</i>",
            reply_markup=cancel_kb(),
        )
        await callback.answer()
        return

    if code == "last":
        departure = normalize_departure(driver.last_departure_text or "")
        if not departure:
            await callback.answer("Oxirgi vaqt yo'q.", show_alert=True)
            return
    else:
        departure = resolve_template(code)
        if not departure:
            await callback.answer("Noto'g'ri tanlov.", show_alert=True)
            return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer(departure)
    await _publish_wizard_announce(callback.message, state, session, driver, departure)


@router.message(DriverAnnounce.custom_time, F.text)
async def announce_custom_time(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        uid = message.from_user.id if message.from_user else None
        await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))
        return
    departure = normalize_departure(message.text or "")
    if not departure:
        await message.answer(
            f"1–{MAX_CUSTOM_LEN} belgi orasida yozing.",
            reply_markup=cancel_kb(),
        )
        return
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await message.answer("Profil topilmadi.", reply_markup=main_menu_kb())
        return
    await _publish_wizard_announce(message, state, session, driver, departure)


@router.message(DriverAnnounce.departure, F.text == BTN_CANCEL)
async def announce_dep_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id if message.from_user else None
    await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))


# ----- Xom e'lon: lichkaga matn / forward / rasm -----


def _extract_raw_payload(message: Message) -> RawPayload | None:
    """Forward header'siz matn/caption (+ ixtiyoriy rasm)."""
    text = (message.text or message.caption or "").strip()
    photo_id = message.photo[-1].file_id if message.photo else None
    if not text and not photo_id:
        return None
    return RawPayload(text=text, photo_file_id=photo_id)


async def _active_driver_or_none(
    message: Message,
    session: AsyncSession,
) -> Driver | None:
    user = message.from_user
    if user is None:
        return None
    driver = await get_driver(session, user.id)
    if driver is None:
        return None
    if not driver.is_legacy_or_approved() or not driver.is_access_valid():
        return None
    return driver


async def _ask_raw_departure(
    message: Message,
    state: FSMContext,
    driver: Driver,
    payload: RawPayload,
    *,
    prefer_saved: bool = False,
) -> None:
    """Vaqt so'rash. prefer_saved=True (takrorlash) — saqlangan bilan preview."""
    await state.update_data(
        raw_text=payload.text,
        raw_photo=payload.photo_file_id,
    )
    saved = normalize_departure(
        payload.departure_text or driver.last_departure_text or ""
    )
    if prefer_saved and saved:
        await state.update_data(departure_text=saved)
        await _show_raw_preview(message, state, driver, payload.text, payload.photo_file_id, saved)
        return
    await state.set_state(DriverRawPost.departure)
    await message.answer(
        "🕐 Ketish vaqtini tanlang:",
        reply_markup=departure_time_kb(driver.last_departure_text, prefix="dep"),
    )


async def _show_raw_preview(
    message: Message,
    state: FSMContext,
    driver: Driver,
    raw_text: str,
    photo_file_id: str | None,
    departure: str,
) -> None:
    await state.set_state(DriverRawPost.confirm)
    await state.update_data(
        raw_text=raw_text,
        raw_photo=photo_file_id,
        departure_text=departure,
    )
    from bot.services.driver_card import resolve_announce_photo

    preview = compose_body(
        raw_text,
        driver,
        for_caption=True,
        departure_text=departure,
    )
    photo = resolve_announce_photo(driver, photo_file_id)
    await message.answer("👀 <b>Oldindan ko'rish</b> — guruhda shunday chiqadi:")
    if photo:
        await message.answer_photo(
            photo=photo,
            caption=(preview or "📷")[:1024],
            reply_markup=raw_post_confirm_kb(),
        )
    else:
        await message.answer(preview or "—", reply_markup=raw_post_confirm_kb())
    await message.answer(
        "Tasdiqlang, vaqtni o'zgartiring yoki bekor qiling.",
        reply_markup=_panel_kb(driver),
    )


async def _offer_or_send_raw(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    payload: RawPayload,
    *,
    prefer_saved_time: bool = False,
) -> None:
    allowed, wait_sec = check_post_limit(driver.telegram_id)
    if not allowed:
        mins = max(1, (wait_sec + 59) // 60)
        await message.answer(
            f"⏳ Soatlik limit ({settings.driver_post_limit_per_hour} ta) to'ldi.\n"
            f"Taxminan <b>{mins}</b> daqiqadan keyin qayta urinib ko'ring.",
            reply_markup=_panel_kb(driver),
        )
        return

    # Asl matndan eski 🕐 qatorini ajratib olish
    clean_text = strip_departure_line(payload.text)
    payload = RawPayload(
        text=clean_text,
        photo_file_id=payload.photo_file_id,
        departure_text=payload.departure_text,
    )
    await _ask_raw_departure(
        message,
        state,
        driver,
        payload,
        prefer_saved=prefer_saved_time,
    )


async def _dispatch_raw(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    payload: RawPayload,
) -> None:
    if payload.departure_text:
        await update_driver_fields(
            session,
            driver.telegram_id,
            last_departure_text=payload.departure_text,
        )
        await session.commit()
        driver.last_departure_text = payload.departure_text

    await state.clear()
    bot = message.bot
    chat_id = message.chat.id

    ok: list[int] = []
    failed: list[tuple[int, str]] = []
    main_id, main_err = await post_to_main(bot, driver, payload)
    if main_id is not None and main_err is None:
        ok.append(main_id)
    elif main_id is not None:
        failed.append((main_id, main_err or "xato"))

    async def _rest() -> None:
        skip = {main_id} if main_id is not None else set()
        rest_ok, rest_fail = await post_to_rest(
            bot, driver, payload, skip_chat_ids=skip
        )
        all_ok = ok + rest_ok
        all_fail = failed + rest_fail
        if all_ok:
            save_last_raw(driver.telegram_id, payload)
            record_post(driver.telegram_id)
        await bot.send_message(
            chat_id,
            format_result(all_ok, all_fail),
            reply_markup=_panel_kb(driver),
        )

    asyncio.create_task(_rest(), name=f"raw-post-{driver.telegram_id}")


@router.message(F.text == BTN_REPEAT_LAST)
async def on_repeat_last(message: Message, state: FSMContext, session: AsyncSession) -> None:
    driver = await _active_driver_or_none(message, session)
    if driver is None:
        await message.answer(
            "Avval haydovchi sifatida ro'yxatdan o'ting.",
            reply_markup=main_menu_kb(),
        )
        return
    last = get_last_raw(driver.telegram_id)
    if last is None:
        await message.answer(
            "Hali xom e'lon yuborilmagan. Matnni shu yerga tashlang.",
            reply_markup=_panel_kb(driver),
        )
        return
    # Saqlangan vaqtni birga olib kelish
    dep = last.departure_text or driver.last_departure_text
    payload = RawPayload(
        text=last.text,
        photo_file_id=last.photo_file_id,
        departure_text=dep,
    )
    if not await ensure_required_or_ask(
        message,
        state,
        driver,
        resume_action="raw",
        extra_data={
            "pending_raw": {
                "text": payload.text,
                "photo_file_id": payload.photo_file_id,
                "departure_text": payload.departure_text,
            }
        },
    ):
        return
    await _offer_or_send_raw(
        message, state, session, driver, payload, prefer_saved_time=True
    )


@router.callback_query(DriverRawPost.departure, F.data.startswith("dep:"))
async def raw_departure_cb(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    raw_text = str(data.get("raw_text") or "")
    raw_photo = data.get("raw_photo")

    if code == "cancel":
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer("Bekor qilindi.", reply_markup=_panel_kb(user.id))
        await callback.answer()
        return

    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await callback.answer("Profil topilmadi.", show_alert=True)
        return

    if code == "custom":
        await state.set_state(DriverRawPost.custom_time)
        await callback.message.answer(
            f"Ketish vaqtini yozing (max {MAX_CUSTOM_LEN} belgi):\n"
            "<i>Masalan: Bugun soat 18:00 da Beshariqdan</i>",
            reply_markup=cancel_kb(),
        )
        await callback.answer()
        return

    if code == "last":
        departure = normalize_departure(driver.last_departure_text or "")
        if not departure:
            await callback.answer("Oxirgi vaqt yo'q — shablon tanlang.", show_alert=True)
            return
    else:
        departure = resolve_template(code)
        if not departure:
            await callback.answer("Noto'g'ri tanlov.", show_alert=True)
            return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer(departure)

    if settings.driver_raw_confirm:
        await _show_raw_preview(
            callback.message, state, driver, raw_text, raw_photo, departure
        )
        return

    payload = RawPayload(text=raw_text, photo_file_id=raw_photo, departure_text=departure)
    await _dispatch_raw(callback.message, state, session, driver, payload)


@router.message(DriverRawPost.custom_time, F.text)
async def raw_custom_time(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        uid = message.from_user.id if message.from_user else None
        await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))
        return
    departure = normalize_departure(message.text or "")
    if not departure:
        await message.answer(
            f"1–{MAX_CUSTOM_LEN} belgi orasida yozing.",
            reply_markup=cancel_kb(),
        )
        return
    user = message.from_user
    if user is None:
        return
    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await message.answer("Profil topilmadi.", reply_markup=main_menu_kb())
        return
    data = await state.get_data()
    raw_text = str(data.get("raw_text") or "")
    raw_photo = data.get("raw_photo")
    if settings.driver_raw_confirm:
        await _show_raw_preview(message, state, driver, raw_text, raw_photo, departure)
        return
    payload = RawPayload(text=raw_text, photo_file_id=raw_photo, departure_text=departure)
    await _dispatch_raw(message, state, session, driver, payload)


@router.message(DriverRawPost.departure, F.text == BTN_CANCEL)
async def raw_dep_cancel_btn(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id if message.from_user else None
    await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))


@router.message(DriverRawPost.confirm, F.text == BTN_CANCEL)
async def raw_confirm_cancel_btn(message: Message, state: FSMContext) -> None:
    await state.clear()
    uid = message.from_user.id if message.from_user else None
    await message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))


@router.message(DriverRawPost.confirm, F.text | F.photo)
async def raw_confirm_waiting(message: Message) -> None:
    await message.answer("Pastdagi ✅ / 🕐 / ❌ tugmalarini bosing.")


@router.callback_query(DriverRawPost.confirm, F.data == "rawpost:cancel")
async def raw_post_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = callback.from_user.id if callback.from_user else None
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer("Bekor qilindi.", reply_markup=_panel_kb(uid))
    await callback.answer("Bekor qilindi")


@router.callback_query(DriverRawPost.confirm, F.data == "rawpost:changetime")
async def raw_post_change_time(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    driver = await get_driver(session, user.id)
    last = driver.last_departure_text if driver else None
    await state.set_state(DriverRawPost.departure)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(
        "🕐 Yangi ketish vaqtini tanlang:",
        reply_markup=departure_time_kb(last, prefix="dep"),
    )
    await callback.answer()


@router.callback_query(DriverRawPost.confirm, F.data == "rawpost:ok")
async def raw_post_ok(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    data = await state.get_data()
    departure = normalize_departure(str(data.get("departure_text") or ""))
    if not departure:
        await callback.answer("Avval ketish vaqtini tanlang.", show_alert=True)
        return
    payload = RawPayload(
        text=str(data.get("raw_text") or ""),
        photo_file_id=data.get("raw_photo"),
        departure_text=departure,
    )
    if not payload.text and not payload.photo_file_id:
        await state.clear()
        await callback.answer("E'lon topilmadi.", show_alert=True)
        return

    driver = await get_driver(session, user.id)
    if driver is None or not driver.is_legacy_or_approved() or not driver.is_access_valid():
        await state.clear()
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    allowed, wait_sec = check_post_limit(driver.telegram_id)
    if not allowed:
        mins = max(1, (wait_sec + 59) // 60)
        await state.clear()
        await callback.answer(f"Limit. ~{mins} daqiqa kuting.", show_alert=True)
        return

    await update_driver_fields(
        session, driver.telegram_id, last_departure_text=departure
    )
    await session.commit()
    driver.last_departure_text = departure

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    bot = callback.bot
    chat_id = callback.message.chat.id if callback.message else user.id
    await state.clear()

    ok: list[int] = []
    failed: list[tuple[int, str]] = []
    main_id, main_err = await post_to_main(bot, driver, payload)
    if main_id is not None and main_err is None:
        ok.append(main_id)
        await callback.answer("Yuborildi")
    elif main_id is not None:
        failed.append((main_id, main_err or "xato"))
        await callback.answer("Xato", show_alert=True)
    else:
        await callback.answer("Guruhlar yo'q", show_alert=True)

    async def _rest() -> None:
        skip = {main_id} if main_id is not None else set()
        rest_ok, rest_fail = await post_to_rest(
            bot, driver, payload, skip_chat_ids=skip
        )
        all_ok = ok + rest_ok
        all_fail = failed + rest_fail
        if all_ok:
            save_last_raw(driver.telegram_id, payload)
            record_post(driver.telegram_id)
        await bot.send_message(
            chat_id,
            format_result(all_ok, all_fail),
            reply_markup=_panel_kb(driver),
        )

    asyncio.create_task(_rest(), name=f"raw-post-{driver.telegram_id}")


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
    dep = (driver.last_departure_text or "").strip() or "—"
    lines = [
        "👤 <b>Profilingiz</b>\n",
        f"Ism: <b>{driver.full_name}</b>",
        f"Telefon: <code>{driver.phone or '—'}</code>",
        f"Mashina: {driver.car_model or '—'} · {driver.car_number or '—'}",
        f"Rang: {driver.car_color or '—'}",
        f"O'rinlar: {driver.seats_count if driver.seats_count else '—'}",
        f"Rasm: {'bor' if driver.photo_file_id else 'yoq'}",
        f"🕐 Ketish vaqti: <b>{dep}</b>",
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
    await message.answer("\n".join(lines), reply_markup=_panel_kb(driver))
    await message.answer(
        "Ketish vaqti shablonini o'zgartirish mumkin:",
        reply_markup=profile_departure_kb(),
    )
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


@router.callback_query(F.data == "pdep:edit")
async def profile_dep_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    driver = await get_driver(session, user.id)
    if driver is None or not driver.is_legacy_or_approved():
        await callback.answer("Profil topilmadi.", show_alert=True)
        return
    await state.set_state(DriverDepartureEdit.picking)
    await callback.message.answer(
        "🕐 Yangi ketish vaqti shablonini tanlang:",
        reply_markup=departure_time_kb(driver.last_departure_text, prefix="pdep"),
    )
    await callback.answer()


@router.callback_query(DriverDepartureEdit.picking, F.data.startswith("pdep:"))
async def profile_dep_pick(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    code = callback.data.split(":", 1)[1]
    if code == "edit":
        await callback.answer()
        return
    if code == "cancel":
        await state.clear()
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer("Bekor qilindi.", reply_markup=_panel_kb(user.id))
        await callback.answer()
        return

    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await callback.answer("Profil topilmadi.", show_alert=True)
        return

    if code == "custom":
        await state.set_state(DriverDepartureEdit.custom_time)
        await callback.message.answer(
            f"Ketish vaqtini yozing (max {MAX_CUSTOM_LEN} belgi):",
            reply_markup=cancel_kb(),
        )
        await callback.answer()
        return

    if code == "last":
        departure = normalize_departure(driver.last_departure_text or "")
        if not departure:
            await callback.answer("Hali saqlangan vaqt yo'q.", show_alert=True)
            return
    else:
        departure = resolve_template(code)
        if not departure:
            await callback.answer("Noto'g'ri tanlov.", show_alert=True)
            return

    await update_driver_fields(session, user.id, last_departure_text=departure)
    await session.commit()
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(
        f"✅ Saqlandi: <b>{departure}</b>",
        reply_markup=_panel_kb(user.id),
    )
    await callback.answer("Saqlandi")


@router.message(DriverDepartureEdit.custom_time, F.text)
async def profile_dep_custom(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = message.from_user
    if user is None:
        return
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=_panel_kb(user.id))
        return
    departure = normalize_departure(message.text or "")
    if not departure:
        await message.answer(
            f"1–{MAX_CUSTOM_LEN} belgi orasida yozing.",
            reply_markup=cancel_kb(),
        )
        return
    await update_driver_fields(session, user.id, last_departure_text=departure)
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Saqlandi: <b>{departure}</b>",
        reply_markup=_panel_kb(user.id),
    )


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
        await message.answer("Bekor qilindi.", reply_markup=_panel_kb(user.id))
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


# ----- Xom e'lon catch-all (barcha panel/FSM handlerlardan keyin) -----


class _ActiveRawPostFilter(Filter):
    """Faqat active haydovchi + bo'sh FSM + tugma/buyruq emas — aks holda event o'tadi."""

    async def __call__(
        self,
        message: Message,
        session: AsyncSession,
        state: FSMContext,
    ) -> bool:
        if await state.get_state() is not None:
            return False
        if message.from_user is None:
            return False
        text = (message.text or "").strip()
        if text.startswith("/"):
            return False
        if text in RESERVED_PRIVATE_TEXTS:
            return False
        has_photo = bool(message.photo)
        body = (message.text or message.caption or "").strip()
        if not has_photo and not body:
            return False
        if not has_photo and len(body) < 3:
            return False
        driver = await get_driver(session, message.from_user.id)
        if driver is None:
            return False
        if not driver.is_legacy_or_approved() or not driver.is_access_valid():
            return False
        return True


@router.message(_ActiveRawPostFilter(), F.text | F.photo | F.caption)
async def on_raw_driver_post(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Lichkadagi oddiy matn / forward / rasm+caption → e'lon."""
    driver = await _active_driver_or_none(message, session)
    if driver is None:
        return
    payload = _extract_raw_payload(message)
    if payload is None:
        return
    if not await ensure_required_or_ask(
        message,
        state,
        driver,
        resume_action="raw",
        extra_data={
            "pending_raw": {
                "text": payload.text,
                "photo_file_id": payload.photo_file_id,
            }
        },
    ):
        return
    await _offer_or_send_raw(message, state, session, driver, payload)


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
