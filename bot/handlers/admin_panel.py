"""Yangi sodda /admin paneli: haydovchi, guruh, to'lov, premium, reklama, statistika."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.db_requests import (
    get_admin_period_stats,
    get_driver,
    list_payment_history,
    log_admin_action,
    search_drivers,
)
from bot.database.models import Driver, DriverStatus, utcnow
from bot.keyboards.default_kb import BTN_CANCEL, driver_panel_kb, main_menu_kb
from bot.keyboards.inline_kb import (
    admin_ad_interval_kb,
    admin_ads_menu_kb,
    admin_confirm_delete_kb,
    admin_driver_card_kb,
    admin_driver_premium_kb,
    admin_drivers_list_kb,
    admin_group_card_kb,
    admin_groups_list_kb,
    admin_menu_kb,
    admin_pay_days_kb,
    admin_payments_menu_kb,
    admin_premium_global_kb,
    admin_stats_kb,
)
from bot.services import app_settings
from bot.services.bot_ads import preview_text, send_ad_to_groups
from bot.services.bot_groups import (
    list_groups,
    refresh_group_meta,
    set_group_flag,
    set_main_group,
    upsert_group,
)
from bot.services.driver_moderation import (
    ban_driver,
    notify_driver_blocked,
    notify_driver_unblocked,
    restore_driver,
    soft_delete_driver,
    unban_driver,
)
from bot.services.feature_flags import (
    FLAG_BY_KEY,
    driver_feature_enabled,
    premium_status_lines,
    set_driver_override,
    set_premium_package,
)
from bot.services.payments import (
    accept_payment,
    admin_contact_text,
    driver_fee,
    fmt_date,
    fmt_money,
    set_payment_due,
)
from bot.states.order_states import (
    AdminAdEdit,
    AdminDriverMessage,
    AdminDriverSearch,
    AdminPaymentDate,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_panel")
TZ = ZoneInfo("Asia/Tashkent")
PAGE = 10


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and settings.is_admin(user.id))


router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(F.message.chat.type == "private", IsAdmin())


def _status_line(driver: Driver) -> str:
    if driver.is_soft_deleted():
        return "🗑 O'chirilgan"
    if driver.status == DriverStatus.PENDING.value:
        return "⏳ Tasdiq kutilmoqda"
    if driver.status == DriverStatus.REJECTED.value:
        return "❌ Rad etilgan"
    if driver.status == DriverStatus.BANNED.value:
        return "⛔ Bloklangan"
    if driver.is_access_valid():
        return "✅ Faol"
    return f"ℹ️ {driver.status}"


async def _pending_drivers(session: AsyncSession) -> list[Driver]:
    rows, _ = await search_drivers(session, None, offset=0, limit=200, include_deleted=False)
    return [d for d in rows if d.status == DriverStatus.PENDING.value]


async def _pending_count(session: AsyncSession) -> int:
    return len(await _pending_drivers(session))


def _payment_line(driver: Driver) -> str:
    if driver.payment_exempt:
        return "💳 To'lov: <b>Bepul</b>"
    st = driver.payment_status()
    labels = {
        "to'langan": "to'langan",
        "kutilmoqda": "kutilmoqda",
        "kechikkan": "kechikkan",
        "o'chiq": "—",
        "bepul": "bepul",
    }
    return (
        f"💳 To'lov: {fmt_date(driver.payment_due_at)} · "
        f"<b>{labels.get(st, st)}</b> · {fmt_money(driver_fee(driver))} so'm"
    )


def _premium_line(driver: Driver) -> str:
    if driver.has_active_premium():
        return f"⭐ Premium: <b>bor</b> (gacha {fmt_date(driver.premium_until)})"
    return "⭐ Premium: <b>yo'q</b>"


def format_driver_card(driver: Driver) -> str:
    return (
        f"👤 <b>{driver.full_name}</b>\n"
        f"📞 {driver.phone or '—'}\n"
        f"🚗 {driver.car_model or '—'}, {driver.car_number or '—'}\n"
        f"🆔 <code>{driver.telegram_id}</code>\n\n"
        f"Holat: {_status_line(driver)}\n"
        f"{_premium_line(driver)}\n"
        f"{_payment_line(driver)}"
    )


async def _edit_or_answer(callback: CallbackQuery, text: str, kb) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "adm:home")
async def adm_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit_or_answer(callback, "🔐 Admin panel:", admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:noop")
async def adm_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ----- Haydovchilar -----


@router.callback_query(F.data.startswith("adm:drv:list:"))
async def adm_drv_list(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    page = max(0, page)
    rows, total = await search_drivers(session, None, offset=page * PAGE, limit=PAGE)
    pending_n = await _pending_count(session)
    text = f"🚖 <b>Haydovchilar</b> ({total} ta)\nSahifa {page + 1}."
    if pending_n:
        text += f"\n⏳ Kutayotgan ariza: <b>{pending_n}</b>"
    if not rows:
        text = "Haydovchilar topilmadi."
    await _edit_or_answer(
        callback,
        text,
        admin_drivers_list_kb(
            rows, page=page, total=total, page_size=PAGE, pending_count=pending_n
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:drv:pending")
async def adm_drv_pending(callback: CallbackQuery, session: AsyncSession) -> None:
    pending = await _pending_drivers(session)
    if not pending:
        await _edit_or_answer(
            callback,
            "⏳ Kutayotgan ariza yo'q.",
            admin_drivers_list_kb([], page=0, total=0, pending_count=0),
        )
        await callback.answer()
        return
    text = f"⏳ <b>Kutayotgan arizalar</b> ({len(pending)} ta)\nTanlang:"
    await _edit_or_answer(
        callback,
        text,
        admin_drivers_list_kb(
            pending[:PAGE],
            page=0,
            total=len(pending),
            page_size=PAGE,
            pending_count=len(pending),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:drv:approve:"))
async def adm_drv_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    from bot.database.db_requests import approve_driver
    from bot.services.notify import notify_admins

    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await approve_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    if driver.status == DriverStatus.BANNED.value:
        await callback.answer("Bloklangan — avval blokdan chiqaring.", show_alert=True)
        return
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="approve",
        target_id=driver_id,
    )
    await session.commit()
    try:
        await callback.bot.send_message(
            driver_id,
            "🎉 Arizangiz tasdiqlandi! Haydovchi paneli ochildi.",
            reply_markup=driver_panel_kb(),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    admin_name = callback.from_user.full_name if callback.from_user else "Admin"
    await notify_admins(
        callback.bot,
        f"✅ {admin_name} tasdiqladi: <b>{driver.full_name}</b> "
        f"(<code>{driver_id}</code>)",
    )
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Tasdiqlandi.")


@router.callback_query(F.data.startswith("adm:drv:reject:"))
async def adm_drv_reject(callback: CallbackQuery, session: AsyncSession) -> None:
    from bot.database.db_requests import reject_driver
    from bot.services.notify import notify_admins

    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await reject_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="reject",
        target_id=driver_id,
    )
    await session.commit()
    try:
        await callback.bot.send_message(
            driver_id,
            "Arizangiz rad etildi. Savol bo'lsa admin bilan bog'laning.",
            reply_markup=main_menu_kb(),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    admin_name = callback.from_user.full_name if callback.from_user else "Admin"
    await notify_admins(
        callback.bot,
        f"❌ {admin_name} rad etdi: <b>{driver.full_name}</b> "
        f"(<code>{driver_id}</code>)",
    )
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Rad etildi.")


@router.callback_query(F.data == "adm:drv:search")
async def adm_drv_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminDriverSearch.waiting)
    await callback.message.answer(
        "Qidiruv: ism, telefon yoki davlat raqamini yozing.\nBekor: /admin"
    )
    await callback.answer()


@router.message(AdminDriverSearch.waiting, F.text)
async def adm_drv_search_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if (message.text or "").strip() in {BTN_CANCEL, "/admin"}:
        await state.clear()
        await message.answer("🔐 Admin panel:", reply_markup=admin_menu_kb())
        return
    q = (message.text or "").strip()
    await state.clear()
    rows, total = await search_drivers(session, q, offset=0, limit=PAGE)
    pending_n = await _pending_count(session)
    await message.answer(
        f"🔎 Natija: <b>{total}</b> ta\nSo'rov: <code>{q}</code>",
        reply_markup=admin_drivers_list_kb(
            rows, page=0, total=total, page_size=PAGE, query=q, pending_count=pending_n
        ),
    )


@router.callback_query(F.data.startswith("adm:drv:card:"))
async def adm_drv_card(callback: CallbackQuery, session: AsyncSession) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:drv:ban:"))
async def adm_drv_ban(callback: CallbackQuery, session: AsyncSession) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await ban_driver(session, driver, reason="admin")
    await log_admin_action(
        session, admin_id=callback.from_user.id, action="ban", target_id=driver_id
    )
    await session.commit()
    await notify_driver_blocked(
        callback.bot,
        driver_id,
        reason_text="⛔ Profilingiz bloklandi. Savol bo'lsa admin bilan bog'laning.",
    )
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Bloklandi.")


@router.callback_query(F.data.startswith("adm:drv:unban:"))
async def adm_drv_unban(callback: CallbackQuery, session: AsyncSession) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await unban_driver(session, driver)
    await log_admin_action(
        session, admin_id=callback.from_user.id, action="unban", target_id=driver_id
    )
    await session.commit()
    await notify_driver_unblocked(callback.bot, driver_id)
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Ochildi.")


@router.callback_query(F.data.startswith("adm:drv:delask:"))
async def adm_drv_delask(callback: CallbackQuery) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _edit_or_answer(
        callback,
        "Rostdan o'chirasizmi?\nMa'lumot saqlanadi, keyin tiklash mumkin.",
        admin_confirm_delete_kb(driver_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:drv:del:"))
async def adm_drv_del(callback: CallbackQuery, session: AsyncSession) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await soft_delete_driver(session, driver)
    await log_admin_action(
        session, admin_id=callback.from_user.id, action="soft_delete", target_id=driver_id
    )
    await session.commit()
    await notify_driver_blocked(
        callback.bot,
        driver_id,
        reason_text="🗑 Profilingiz o'chirildi. Savol bo'lsa admin bilan bog'laning.",
    )
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("O'chirildi.")


@router.callback_query(F.data.startswith("adm:drv:restore:"))
async def adm_drv_restore(callback: CallbackQuery, session: AsyncSession) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await restore_driver(session, driver)
    await log_admin_action(
        session, admin_id=callback.from_user.id, action="restore", target_id=driver_id
    )
    await session.commit()
    try:
        await callback.bot.send_message(
            driver_id,
            "✅ Profilingiz tiklandi.",
            reply_markup=driver_panel_kb(),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Tiklandi.")


@router.callback_query(F.data.startswith("adm:drv:prem:"))
async def adm_drv_prem(callback: CallbackQuery, session: AsyncSession) -> None:
    # adm:drv:prem:{id} — not ptog/ppack
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    driver_id = int(parts[3])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    text = f"⭐ <b>{driver.full_name}</b>\n\n{premium_status_lines(driver)}"
    await _edit_or_answer(callback, text, admin_driver_premium_kb(driver))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:drv:ptog:"))
async def adm_drv_ptog(callback: CallbackQuery, session: AsyncSession) -> None:
    # adm:drv:ptog:{id}:{key}
    parts = (callback.data or "").split(":")
    if len(parts) < 5:
        await callback.answer()
        return
    driver_id = int(parts[3])
    key = parts[4]
    if key not in FLAG_BY_KEY:
        await callback.answer("Noma'lum.", show_alert=True)
        return
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    new_val = not driver_feature_enabled(key, driver)
    await set_driver_override(session, driver, key, new_val)
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="premium_toggle",
        target_id=driver_id,
        detail=f"{key}={new_val}",
    )
    await session.commit()
    text = f"⭐ <b>{driver.full_name}</b>\n\n{premium_status_lines(driver)}"
    await _edit_or_answer(callback, text, admin_driver_premium_kb(driver))
    await callback.answer("Saqlandi.")


@router.callback_query(F.data.startswith("adm:drv:ppack:"))
async def adm_drv_ppack(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    driver_id = int(parts[3])
    days = int(parts[4])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    if days <= 0:
        await set_premium_package(session, driver, days=None, enable=False)
        msg = "Premium o'chirildi."
    else:
        await set_premium_package(session, driver, days=days, enable=True)
        msg = f"Premium {days} kun."
        try:
            await callback.bot.send_message(
                driver_id,
                f"⭐ Premium paket yoqildi: <b>{days} kun</b>.",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="premium_pack",
        target_id=driver_id,
        detail=str(days),
    )
    await session.commit()
    text = f"⭐ <b>{driver.full_name}</b>\n\n{premium_status_lines(driver)}"
    await _edit_or_answer(callback, text, admin_driver_premium_kb(driver))
    await callback.answer(msg)


@router.callback_query(F.data.startswith("adm:drv:paid:"))
async def adm_drv_paid_menu(callback: CallbackQuery) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    await _edit_or_answer(
        callback,
        "To'lov muddatini tanlang:",
        admin_pay_days_kb(driver_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:drv:payok:"))
async def adm_drv_payok(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    driver_id = int(parts[3])
    days = int(parts[4])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await accept_payment(session, driver, admin_id=callback.from_user.id, days=days)
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="payment_accept",
        target_id=driver_id,
        detail=f"+{days}",
    )
    await session.commit()
    due_s = fmt_date(driver.payment_due_at)
    try:
        await callback.bot.send_message(
            driver_id,
            f"✅ To'lov qabul qilindi, keyingi to'lov: <b>{due_s}</b>",
            reply_markup=driver_panel_kb(),
        )
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Qabul qilindi.")


@router.callback_query(F.data.startswith("adm:drv:duedate:"))
async def adm_drv_duedate(callback: CallbackQuery, state: FSMContext) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    await state.set_state(AdminPaymentDate.waiting)
    await state.update_data(driver_id=driver_id)
    await callback.message.answer(
        "Yangi to'lov sanasini yozing: <code>KK.OO.YYYY</code>\nMasalan: 25.04.2026"
    )
    await callback.answer()


@router.message(AdminPaymentDate.waiting, F.text)
async def adm_drv_duedate_text(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    driver_id = int(data.get("driver_id") or 0)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw)
    if not m:
        await message.answer("Format: 25.04.2026")
        return
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        due = datetime(year, month, day, 12, 0, tzinfo=TZ)
    except ValueError:
        await message.answer("Sana noto'g'ri.")
        return
    driver = await get_driver(session, driver_id)
    if driver is None:
        await state.clear()
        await message.answer("Haydovchi topilmadi.")
        return
    await set_payment_due(session, driver, due)
    await log_admin_action(
        session,
        admin_id=message.from_user.id if message.from_user else 0,
        action="payment_due_set",
        target_id=driver_id,
        detail=raw,
    )
    await session.commit()
    await state.clear()
    await message.answer(
        format_driver_card(driver),
        reply_markup=admin_driver_card_kb(driver),
    )


@router.callback_query(F.data.startswith("adm:drv:exempt:"))
async def adm_drv_exempt(callback: CallbackQuery, session: AsyncSession) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    driver = await get_driver(session, driver_id)
    if driver is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    driver.payment_exempt = not bool(driver.payment_exempt)
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="payment_exempt",
        target_id=driver_id,
        detail=str(driver.payment_exempt),
    )
    await session.commit()
    await _edit_or_answer(callback, format_driver_card(driver), admin_driver_card_kb(driver))
    await callback.answer("Saqlandi.")


@router.callback_query(F.data.startswith("adm:drv:msg:"))
async def adm_drv_msg(callback: CallbackQuery, state: FSMContext) -> None:
    driver_id = int((callback.data or "").rsplit(":", 1)[-1])
    await state.set_state(AdminDriverMessage.waiting)
    await state.update_data(driver_id=driver_id)
    await callback.message.answer("Haydovchiga yuboriladigan xabarni yozing:")
    await callback.answer()


@router.message(AdminDriverMessage.waiting, F.text)
async def adm_drv_msg_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    driver_id = int(data.get("driver_id") or 0)
    text = (message.text or "").strip()
    await state.clear()
    if not text:
        await message.answer("Bo'sh xabar.")
        return
    try:
        await message.bot.send_message(driver_id, f"📨 Admin:\n{text}")
        await log_admin_action(
            session,
            admin_id=message.from_user.id if message.from_user else 0,
            action="dm_driver",
            target_id=driver_id,
        )
        await session.commit()
        await message.answer("✅ Yuborildi.")
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer("Yuborib bo'lmadi (botni bloklagan).")


# ----- To'lovlar -----


@router.callback_query(F.data == "adm:pay:menu")
async def adm_pay_menu(callback: CallbackQuery) -> None:
    enabled = app_settings.payments_enabled()
    fee = fmt_money(app_settings.monthly_fee_default())
    text = (
        f"💳 <b>To'lovlar</b>\n\n"
        f"Oylik summa (standart): <b>{fee}</b> so'm\n"
        f"Imtiyozli kun: <b>{app_settings.grace_days()}</b>\n"
        f"Admin: {admin_contact_text()}"
    )
    await _edit_or_answer(callback, text, admin_payments_menu_kb(enabled=enabled))
    await callback.answer()


@router.callback_query(F.data == "adm:pay:toggle")
async def adm_pay_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    new_val = not app_settings.payments_enabled()
    await app_settings.set_setting(session, "payments_enabled", new_val)
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="payments_toggle",
        detail=str(new_val),
    )
    await session.commit()
    await adm_pay_menu(callback)


@router.callback_query(F.data.in_({"adm:pay:due", "adm:pay:late"}))
async def adm_pay_lists(callback: CallbackQuery, session: AsyncSession) -> None:
    late = callback.data == "adm:pay:late"
    rows, _ = await search_drivers(session, None, offset=0, limit=200, include_deleted=False)
    today = utcnow().astimezone(TZ).date()
    picked: list[Driver] = []
    for d in rows:
        if d.payment_exempt or d.status == DriverStatus.BANNED.value and d.block_reason != "payment":
            if d.payment_exempt:
                continue
        due = d._aware(d.payment_due_at)
        if due is None:
            continue
        dd = due.astimezone(TZ).date()
        if late and dd < today:
            picked.append(d)
        elif not late and dd <= today:
            picked.append(d)
    title = "Kechikkanlar" if late else "Kutilayotganlar"
    if not picked:
        await callback.message.answer(f"💳 {title}: bo'sh.")
        await callback.answer()
        return
    lines = [f"💳 <b>{title}</b>\n"]
    for d in picked[:30]:
        lines.append(
            f"• {d.full_name} · {fmt_date(d.payment_due_at)} · "
            f"{fmt_money(driver_fee(d))} · <code>{d.telegram_id}</code>"
        )
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "adm:pay:hist")
async def adm_pay_hist(callback: CallbackQuery, session: AsyncSession) -> None:
    rows = await list_payment_history(session, limit=20)
    if not rows:
        await callback.message.answer("To'lov tarixi bo'sh.")
        await callback.answer()
        return
    lines = ["📜 <b>Oxirgi to'lovlar</b>\n"]
    for r in rows:
        lines.append(
            f"• {fmt_date(r.created_at)} · #{r.driver_id} · "
            f"{fmt_money(r.amount)} · +{r.days} kun · admin {r.accepted_by}"
        )
    await callback.message.answer("\n".join(lines))
    await callback.answer()


# ----- Premium global -----


@router.callback_query(F.data == "adm:prem:menu")
async def adm_prem_menu(callback: CallbackQuery) -> None:
    text = (
        "⭐ <b>Premium</b>\n\n"
        "Global o'chiq bo'lsa — hech kimda ishlamaydi.\n"
        "Yoqiq bo'lsa — har haydovchining shaxsiy sozlamasi.\n"
        "Bitta haydovchi: 🚖 Haydovchilar → kartochka → Premium."
    )
    await _edit_or_answer(callback, text, admin_premium_global_kb())
    await callback.answer()


# ----- Guruhlar -----


@router.callback_query(F.data == "adm:grp:list")
async def adm_grp_list(callback: CallbackQuery, session: AsyncSession) -> None:
    groups = await list_groups(session)
    for g in groups[:15]:
        try:
            await refresh_group_meta(callback.bot, session, g.chat_id)
        except Exception:
            pass
    await session.commit()
    groups = await list_groups(session)
    await _edit_or_answer(
        callback,
        "👥 <b>Guruhlar</b>\nGuruhni tanlang:",
        admin_groups_list_kb(groups),
    )
    await callback.answer()


def _group_card_text(g) -> str:
    post = "✅" if g.can_post else "❌"
    delete = "✅" if g.can_delete else "❌"
    warn = ""
    if g.can_post is False:
        warn += "\n🔴 Botga <b>xabar yozish</b> huquqini bering."
    if g.can_delete is False and g.flag_delete_original:
        warn += "\n🔴 Botga <b>o'chirish</b> huquqini bering."
    members = g.member_count if g.member_count is not None else "—"
    star = "⭐ Asosiy\n" if g.is_main else ""
    return (
        f"👥 <b>{g.title or g.chat_id}</b>\n"
        f"{star}"
        f"ID: <code>{g.chat_id}</code>\n"
        f"A'zolar: {members}\n"
        f"Huquq: yozish {post} · o'chirish {delete}"
        f"{warn}"
    )


@router.callback_query(F.data.startswith("adm:grp:card:"))
async def adm_grp_card(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = int((callback.data or "").rsplit(":", 1)[-1])
    await refresh_group_meta(callback.bot, session, chat_id)
    await session.commit()
    groups = await list_groups(session)
    g = next((x for x in groups if x.chat_id == chat_id), None)
    if g is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await _edit_or_answer(callback, _group_card_text(g), admin_group_card_kb(g))
    await callback.answer()


@router.callback_query(F.data.startswith("adm:grp:tog:"))
async def adm_grp_tog(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = (callback.data or "").split(":")
    chat_id = int(parts[3])
    flag = parts[4]
    groups = await list_groups(session)
    g = next((x for x in groups if x.chat_id == chat_id), None)
    if g is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    cur = {
        "relay": g.flag_relay_source,
        "driver": g.flag_driver_posts,
        "auto": g.flag_auto_posts,
        "ads": g.flag_bot_ads,
        "delete": g.flag_delete_original,
    }.get(flag)
    if cur is None:
        await callback.answer()
        return
    await set_group_flag(session, chat_id, flag, not cur)
    await log_admin_action(
        session,
        admin_id=callback.from_user.id,
        action="group_flag",
        target_id=chat_id,
        detail=f"{flag}={not cur}",
    )
    await session.commit()
    groups = await list_groups(session)
    g = next((x for x in groups if x.chat_id == chat_id), None)
    await _edit_or_answer(callback, _group_card_text(g), admin_group_card_kb(g))
    await callback.answer("Saqlandi.")


@router.callback_query(F.data.startswith("adm:grp:main:"))
async def adm_grp_main(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = int((callback.data or "").rsplit(":", 1)[-1])
    g = await set_main_group(session, chat_id)
    await log_admin_action(
        session, admin_id=callback.from_user.id, action="set_main_group", target_id=chat_id
    )
    await session.commit()
    if g is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    await _edit_or_answer(callback, _group_card_text(g), admin_group_card_kb(g))
    await callback.answer("Asosiy guruh yangilandi.")


@router.callback_query(F.data.startswith("adm:grp:ok:"))
async def adm_grp_ok(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = int((callback.data or "").rsplit(":", 1)[-1])
    await upsert_group(session, chat_id, approved=True)
    await session.commit()
    await callback.answer("Yoqildi.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("adm:grp:no:"))
async def adm_grp_no(callback: CallbackQuery, session: AsyncSession) -> None:
    chat_id = int((callback.data or "").rsplit(":", 1)[-1])
    await upsert_group(session, chat_id, approved=False)
    await session.commit()
    await callback.answer("O'tkazib yuborildi.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


# ----- Reklama -----


@router.callback_query(F.data == "adm:ad:menu")
async def adm_ad_menu(callback: CallbackQuery) -> None:
    cfg = app_settings.ad_config()
    text = (
        f"📣 <b>Reklama</b>\n\n"
        f"{'✅ Yoqilgan' if cfg['enabled'] else '❌ O‘chiq'}\n"
        f"Interval: har {cfg['interval_hours']} soat\n"
        f"Vaqt: {cfg['active_from']}–{cfg['active_to']}\n\n"
        f"<i>{cfg['text'][:200]}</i>"
    )
    await _edit_or_answer(
        callback,
        text,
        admin_ads_menu_kb(enabled=cfg["enabled"], interval=cfg["interval_hours"]),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:ad:toggle")
async def adm_ad_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    cfg = app_settings.ad_config()
    await app_settings.set_setting(session, "ad_enabled", not cfg["enabled"])
    await session.commit()
    await adm_ad_menu(callback)


@router.callback_query(F.data == "adm:ad:interval")
async def adm_ad_interval(callback: CallbackQuery) -> None:
    await _edit_or_answer(callback, "Intervalni tanlang:", admin_ad_interval_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("adm:ad:inth:"))
async def adm_ad_inth(callback: CallbackQuery, session: AsyncSession) -> None:
    hours = int((callback.data or "").rsplit(":", 1)[-1])
    await app_settings.set_setting(session, "ad_interval_hours", hours)
    await session.commit()
    await adm_ad_menu(callback)


@router.callback_query(F.data == "adm:ad:text")
async def adm_ad_text(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminAdEdit.text)
    await callback.message.answer("Yangi reklama matnini yuboring:")
    await callback.answer()


@router.message(AdminAdEdit.text, F.text)
async def adm_ad_text_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Matn juda qisqa.")
        return
    await app_settings.set_setting(session, "ad_text", text[:1000])
    await session.commit()
    await state.clear()
    await message.answer("✅ Matn saqlandi.", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "adm:ad:photo")
async def adm_ad_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminAdEdit.photo)
    await callback.message.answer("Reklama rasmini yuboring yoki «o'chirish» deb yozing.")
    await callback.answer()


@router.message(AdminAdEdit.photo, F.photo)
async def adm_ad_photo_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    file_id = message.photo[-1].file_id
    await app_settings.set_setting(session, "ad_photo_file_id", file_id)
    await session.commit()
    await state.clear()
    await message.answer("✅ Rasm saqlandi.")


@router.message(AdminAdEdit.photo, F.text)
async def adm_ad_photo_clear(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if "ochir" in (message.text or "").lower().replace("'", ""):
        await app_settings.set_setting(session, "ad_photo_file_id", "")
        await session.commit()
        await state.clear()
        await message.answer("Rasm olib tashlandi.")
        return
    await message.answer("Rasm yuboring yoki «o'chirish» yozing.")


@router.callback_query(F.data == "adm:ad:window")
async def adm_ad_window(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminAdEdit.window)
    await callback.message.answer(
        "Faol vaqt oralig'ini yozing: <code>07:00-23:00</code>"
    )
    await callback.answer()


@router.message(AdminAdEdit.window, F.text)
async def adm_ad_window_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    raw = (message.text or "").strip().replace("–", "-").replace("—", "-")
    m = re.fullmatch(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", raw)
    if not m:
        await message.answer("Format: 07:00-23:00")
        return
    await app_settings.set_setting(session, "ad_active_from", m.group(1))
    await app_settings.set_setting(session, "ad_active_to", m.group(2))
    await session.commit()
    await state.clear()
    await message.answer("✅ Vaqt saqlandi.")


@router.callback_query(F.data == "adm:ad:preview")
async def adm_ad_preview(callback: CallbackQuery) -> None:
    from bot.services.bot_ads import ad_keyboard

    cfg = app_settings.ad_config()
    text = await preview_text()
    try:
        if cfg["photo_file_id"]:
            await callback.message.answer_photo(
                cfg["photo_file_id"],
                caption=cfg["text"][:1024],
                reply_markup=ad_keyboard(),
            )
        else:
            await callback.message.answer(cfg["text"], reply_markup=ad_keyboard())
        await callback.message.answer(text)
    except TelegramBadRequest:
        await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "adm:ad:now")
async def adm_ad_now(callback: CallbackQuery, session: AsyncSession) -> None:
    ok, fail = await send_ad_to_groups(callback.bot, session, force=True)
    await app_settings.set_setting(
        session, "ad_last_sent_at", datetime.now(TZ).isoformat()
    )
    await log_admin_action(
        session, admin_id=callback.from_user.id, action="ad_send_now", detail=f"{ok}/{fail}"
    )
    await session.commit()
    await callback.answer(f"Yuborildi: {ok}, xato: {fail}", show_alert=True)


# ----- Statistika -----


@router.callback_query(F.data == "adm:stats")
async def adm_stats_menu(callback: CallbackQuery) -> None:
    await _edit_or_answer(
        callback,
        "📊 <b>Statistika</b>\nDavrni tanlang:",
        admin_stats_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:stats:"))
async def adm_stats_period(callback: CallbackQuery, session: AsyncSession) -> None:
    days = int((callback.data or "").rsplit(":", 1)[-1])
    st = await get_admin_period_stats(session, days=days)
    label = "Bugun" if days <= 1 else f"{days} kun"
    text = (
        f"📊 <b>Statistika — {label}</b>\n\n"
        f"🆕 Yangi haydovchilar: <b>{st.new_drivers}</b>\n"
        f"✅ Faol: <b>{st.active_drivers}</b>\n"
        f"⛔ Bloklangan: <b>{st.banned_drivers}</b>\n"
        f"⭐ Premium: <b>{st.premium_drivers}</b>\n"
        f"💳 To'lov kutilmoqda: <b>{st.payment_due}</b>\n"
        f"🚕 Yo'lovchi so'rovlari: <b>{st.passenger_orders}</b>\n"
        f"🚖 Tasdiqlangan safarlar: <b>{st.driver_announces}</b>\n"
        f"📣 Reklama orqali: <b>{st.ad_customers}</b>"
    )
    await _edit_or_answer(callback, text, admin_stats_kb())
    await callback.answer()
