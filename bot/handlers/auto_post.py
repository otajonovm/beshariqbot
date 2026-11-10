"""Haydovchi paneli: ⏰ Avto-e'lon FSM."""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.db_requests import get_active_auto_post, get_driver, start_auto_post
from bot.database.models import Driver, async_session_maker
from bot.keyboards.default_kb import (
    BTN_ABORT,
    BTN_AUTO_ENABLE,
    BTN_AUTO_LAST,
    BTN_AUTO_NEW,
    BTN_AUTO_POST,
    BTN_AUTO_STOP,
    BTN_CANCEL,
    BTN_INT_30,
    BTN_INT_60,
    BTN_NO_SEATS,
    DURATION_MIN_BY_BTN,
    INTERVAL_SEC_BY_BTN,
    auto_post_confirm_kb,
    auto_post_duration_kb,
    auto_post_interval_kb,
    auto_post_source_kb,
    cancel_kb,
    main_menu_kb,
)
from bot.keyboards.inline_kb import departure_time_kb
from bot.services.auto_post import (
    format_duration_human,
    format_interval_human,
    hydrate_last_raw,
    is_auto_active,
    run_auto_post_cycle,
    set_auto_active,
    stop_and_notify,
)
from bot.services.departure import (
    MAX_CUSTOM_LEN,
    normalize_departure,
    resolve_template,
    strip_departure_line,
)
from bot.services.driver_raw_post import RawPayload, get_last_raw, post_targets
from bot.states.order_states import DriverAutoPost

logger = logging.getLogger(__name__)
router = Router(name="auto_post")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

_JOY_YOQ_RE = re.compile(r"joy\s*yo['ʻ’`]?q|0\s*joy|joy\s*qolma", re.IGNORECASE)


def looks_like_no_seats(text: str | None) -> bool:
    if not text:
        return False
    return bool(_JOY_YOQ_RE.search(text.strip()))


async def _panel_kb_for(session: AsyncSession, driver: Driver):
    from bot.handlers.driver import _panel_kb

    hydrate_last_raw(driver)
    active = await get_active_auto_post(session, driver.telegram_id)
    if active:
        set_auto_active(driver.telegram_id, True)
    else:
        set_auto_active(driver.telegram_id, False)
    return _panel_kb(driver, auto_active=bool(active))


async def _active_driver(message: Message, session: AsyncSession) -> Driver | None:
    user = message.from_user
    if user is None:
        return None
    driver = await get_driver(session, user.id)
    if driver is None:
        return None
    if not driver.is_legacy_or_approved() or not driver.is_access_valid():
        return None
    return driver


def _resolve_interval(text: str) -> int | None:
    if text in INTERVAL_SEC_BY_BTN:
        return INTERVAL_SEC_BY_BTN[text]
    # Dinamik yorliqlar
    m = re.match(r"⏱\s*(\d+)\s*soniya", text)
    if m:
        return int(m.group(1))
    m = re.match(r"⏱\s*(\d+)\s*daqiqa", text)
    if m:
        return int(m.group(1)) * 60
    if text == BTN_INT_30:
        return 30
    if text == BTN_INT_60:
        return 60
    return None


def _resolve_duration(text: str) -> int | None:
    if text in DURATION_MIN_BY_BTN:
        return DURATION_MIN_BY_BTN[text]
    m = re.match(r"🕐\s*(\d+)\s*soat", text)
    if m:
        return int(m.group(1)) * 60
    m = re.match(r"🕐\s*(\d+)\s*daqiqa", text)
    if m:
        return int(m.group(1))
    return None


async def _ask_interval(message: Message, state: FSMContext) -> None:
    await state.set_state(DriverAutoPost.interval)
    choices = settings.auto_post_interval_choices
    default = 60 if 60 in choices else (choices[0] if choices else 60)
    await message.answer(
        f"Intervalni tanlang (standart: <b>{format_interval_human(default)}</b>):",
        reply_markup=auto_post_interval_kb(choices),
    )


@router.message(F.text == BTN_AUTO_POST)
async def on_auto_post_start(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    from bot.handlers.driver import show_driver_panel
    from bot.services.feature_flags import driver_feature_enabled

    driver = await _active_driver(message, session)
    if driver is None:
        await message.answer(
            "Avval haydovchi sifatida ro'yxatdan o'ting.",
            reply_markup=main_menu_kb(),
        )
        return
    if not driver_feature_enabled("driver_auto_post", driver):
        await state.clear()
        await show_driver_panel(message, driver)
        return
    hydrate_last_raw(driver)
    existing = await get_active_auto_post(session, driver.telegram_id)
    if existing is not None:
        left = max(0, int((existing.ends_at - existing.started_at).total_seconds()))
        await message.answer(
            f"✅ Avto-e'lon yoqilgan.\n"
            f"Interval: <b>{format_interval_human(existing.interval_sec)}</b>\n"
            f"Tugash: <b>{existing.ends_at.astimezone().strftime('%H:%M')}</b>\n\n"
            f"To'xtatish: «{BTN_AUTO_STOP}»",
            reply_markup=await _panel_kb_for(session, driver),
        )
        _ = left
        return
    await state.set_state(DriverAutoPost.source)
    await message.answer(
        "Qaysi e'lon avtomatik tashlansin?\n"
        f"· <b>{BTN_AUTO_LAST}</b> — oxirgi e'lon (standart)\n"
        f"· <b>{BTN_AUTO_NEW}</b> — yangi matn tashlang",
        reply_markup=auto_post_source_kb(),
    )


@router.message(DriverAutoPost.source, F.text == BTN_CANCEL)
@router.message(DriverAutoPost.interval, F.text == BTN_CANCEL)
@router.message(DriverAutoPost.duration, F.text == BTN_CANCEL)
@router.message(DriverAutoPost.confirm, F.text.in_({BTN_CANCEL, BTN_ABORT}))
@router.message(DriverAutoPost.waiting_new, F.text == BTN_CANCEL)
@router.message(DriverAutoPost.new_departure, F.text == BTN_CANCEL)
@router.message(DriverAutoPost.new_custom_time, F.text == BTN_CANCEL)
async def auto_cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    driver = await _active_driver(message, session)
    if driver:
        await message.answer("Bekor qilindi.", reply_markup=await _panel_kb_for(session, driver))
    else:
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())


@router.message(DriverAutoPost.source, F.text == BTN_AUTO_LAST)
async def auto_source_last(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    driver = await _active_driver(message, session)
    if driver is None:
        await state.clear()
        return
    payload = hydrate_last_raw(driver) or get_last_raw(driver.telegram_id)
    if payload is None:
        await message.answer(
            "Oxirgi e'lon yo'q. «Yangi tashlang» ni tanlang yoki avval e'lon yuboring.",
            reply_markup=auto_post_source_kb(),
        )
        return
    await state.update_data(
        ap_text=payload.text,
        ap_photo=payload.photo_file_id,
        ap_departure=payload.departure_text or driver.last_departure_text,
        ap_seats=payload.seats_hint,
        ap_direction=payload.direction,
    )
    await _ask_interval(message, state)


@router.message(DriverAutoPost.source, F.text == BTN_AUTO_NEW)
async def auto_source_new(message: Message, state: FSMContext) -> None:
    await state.set_state(DriverAutoPost.waiting_new)
    await message.answer(
        "Yangi e'lon matnini yuboring (rasm + caption ham bo'ladi):",
        reply_markup=cancel_kb(),
    )


@router.message(DriverAutoPost.waiting_new, F.text | F.photo | F.caption)
async def auto_new_payload(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    if (message.text or "").strip() == BTN_CANCEL:
        return
    driver = await _active_driver(message, session)
    if driver is None:
        await state.clear()
        return
    text = (message.text or message.caption or "").strip()
    photo = message.photo[-1].file_id if message.photo else None
    if not text and not photo:
        await message.answer("Matn yoki rasm yuboring.")
        return
    if looks_like_no_seats(text):
        await message.answer(
            "Bu matn «joy yo'q»ga o'xshaydi. Avto-e'lon uchun boshqa e'lon yuboring.",
            reply_markup=cancel_kb(),
        )
        return
    clean = strip_departure_line(text)
    await state.update_data(ap_text=clean, ap_photo=photo)
    await state.set_state(DriverAutoPost.new_departure)
    await message.answer(
        "🕐 Ketish vaqtini tanlang:",
        reply_markup=departure_time_kb(driver.last_departure_text, prefix="apdep"),
    )


@router.callback_query(DriverAutoPost.new_departure, F.data.startswith("apdep:"))
async def auto_new_dep_cb(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    user = callback.from_user
    if user is None or not callback.data:
        await callback.answer()
        return
    code = callback.data.split(":", 1)[1]
    if code == "cancel":
        await state.clear()
        driver = await get_driver(session, user.id)
        kb = await _panel_kb_for(session, driver) if driver else main_menu_kb()
        await callback.message.answer("Bekor qilindi.", reply_markup=kb)
        await callback.answer()
        return
    driver = await get_driver(session, user.id)
    if driver is None:
        await state.clear()
        await callback.answer("Profil topilmadi.", show_alert=True)
        return
    if code == "custom":
        await state.set_state(DriverAutoPost.new_custom_time)
        await callback.message.answer(
            f"Ketish vaqtini yozing (max {MAX_CUSTOM_LEN} belgi):",
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
    await state.update_data(ap_departure=departure)
    await callback.answer(departure)
    await _ask_interval(callback.message, state)


@router.message(DriverAutoPost.new_custom_time, F.text)
async def auto_new_custom_time(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    if message.text == BTN_CANCEL:
        return
    departure = normalize_departure(message.text or "")
    if not departure or len(departure) > MAX_CUSTOM_LEN:
        await message.answer(f"Vaqtni qisqaroq yozing (max {MAX_CUSTOM_LEN}).")
        return
    await state.update_data(ap_departure=departure)
    await _ask_interval(message, state)


@router.message(DriverAutoPost.interval, F.text)
async def auto_pick_interval(message: Message, state: FSMContext) -> None:
    if message.text in {BTN_CANCEL}:
        return
    sec = _resolve_interval(message.text or "")
    allowed = set(settings.auto_post_interval_choices) | {30, 60}
    if sec is None or sec not in allowed:
        await message.answer(
            "Interval tugmasini bosing.",
            reply_markup=auto_post_interval_kb(settings.auto_post_interval_choices),
        )
        return
    await state.update_data(ap_interval=sec)
    await state.set_state(DriverAutoPost.duration)
    choices = settings.auto_post_duration_choices_min
    default = 120 if 120 in choices else (choices[-1] if choices else 120)
    await message.answer(
        f"Davomiylikni tanlang (standart: <b>{format_duration_human(default)}</b>):",
        reply_markup=auto_post_duration_kb(choices),
    )


@router.message(DriverAutoPost.duration, F.text)
async def auto_pick_duration(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        return
    mins = _resolve_duration(message.text or "")
    allowed = set(settings.auto_post_duration_choices_min) | {60, 120, 180}
    if mins is None or mins not in allowed:
        await message.answer(
            "Davomiylik tugmasini bosing.",
            reply_markup=auto_post_duration_kb(settings.auto_post_duration_choices_min),
        )
        return
    await state.update_data(ap_duration=mins)
    data = await state.get_data()
    interval = int(data.get("ap_interval") or 60)
    n = len(post_targets())
    await state.set_state(DriverAutoPost.confirm)
    await message.answer(
        f"Har <b>{format_interval_human(interval)}</b>da, "
        f"<b>{format_duration_human(mins)}</b> davomida "
        f"<b>{n}</b> ta guruhga yuboriladi.",
        reply_markup=auto_post_confirm_kb(),
    )


@router.message(DriverAutoPost.confirm, F.text == BTN_AUTO_ENABLE)
async def auto_confirm_on(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    driver = await _active_driver(message, session)
    if driver is None:
        await state.clear()
        return
    data = await state.get_data()
    interval = int(data.get("ap_interval") or 60)
    duration = int(data.get("ap_duration") or 120)
    text = str(data.get("ap_text") or "")
    photo = data.get("ap_photo")
    departure = data.get("ap_departure")
    if not text and not photo:
        await state.clear()
        await message.answer(
            "E'lon topilmadi. Qaytadan urinib ko'ring.",
            reply_markup=await _panel_kb_for(session, driver),
        )
        return

    sess = await start_auto_post(
        session,
        driver_id=driver.telegram_id,
        interval_sec=interval,
        duration_min=duration,
        text=text,
        photo_file_id=photo,
        departure_text=departure,
        seats_hint=data.get("ap_seats"),
        direction=data.get("ap_direction"),
    )
    await session.commit()
    set_auto_active(driver.telegram_id, True)
    await state.clear()

    await message.answer(
        "✅ Avto-e'lon yoqildi.",
        reply_markup=await _panel_kb_for(session, driver),
    )

    # Birinchi yuborish darhol
    try:
        await run_auto_post_cycle(message.bot, async_session_maker, sess)
    except Exception:
        logger.exception("auto post first cycle failed")


@router.message(F.text == BTN_AUTO_STOP)
async def on_auto_stop(message: Message, state: FSMContext, session: AsyncSession) -> None:
    from bot.handlers.driver import show_driver_panel
    from bot.services.feature_flags import driver_feature_enabled

    driver = await _active_driver(message, session)
    if driver is None:
        return
    await state.clear()
    if not driver_feature_enabled("driver_auto_post", driver):
        # Flag o'chiq bo'lsa ham to'xtatib panel yangilanadi (xato yo'q)
        await stop_and_notify(
            message.bot, async_session_maker, driver.telegram_id, reason="manual"
        )
        await show_driver_panel(message, driver)
        return
    stopped = await stop_and_notify(
        message.bot, async_session_maker, driver.telegram_id, reason="manual"
    )
    if not stopped:
        await message.answer(
            "Faol avto-e'lon yo'q.",
            reply_markup=await _panel_kb_for(session, driver),
        )
        return
    await message.answer(
        "Panel:",
        reply_markup=await _panel_kb_for(session, driver),
    )


@router.message(F.text == BTN_NO_SEATS)
async def on_no_seats(message: Message, state: FSMContext, session: AsyncSession) -> None:
    from bot.handlers.driver import show_driver_panel
    from bot.services.feature_flags import driver_feature_enabled

    driver = await _active_driver(message, session)
    if driver is None:
        return
    await state.clear()
    if not driver_feature_enabled("driver_auto_post", driver):
        await stop_and_notify(
            message.bot, async_session_maker, driver.telegram_id, reason="no_seats"
        )
        await show_driver_panel(message, driver)
        return
    if not is_auto_active(driver.telegram_id):
        active = await get_active_auto_post(session, driver.telegram_id)
        if active is None:
            await message.answer(
                "Faol avto-e'lon yo'q.",
                reply_markup=await _panel_kb_for(session, driver),
            )
            return
    await stop_and_notify(
        message.bot, async_session_maker, driver.telegram_id, reason="no_seats"
    )
    await message.answer(
        "Panel:",
        reply_markup=await _panel_kb_for(session, driver),
    )


async def maybe_stop_auto_for_no_seats(
    bot,
    session_maker,
    driver_id: int,
    seats_text: str | None,
) -> bool:
    """Joy yo'q matni bo'lsa avto-e'lonni to'xtatadi."""
    if not looks_like_no_seats(seats_text):
        return False
    return await stop_and_notify(bot, session_maker, driver_id, reason="no_seats")
