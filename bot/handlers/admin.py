"""Admin: statistika va obunani uzaytirish."""

from __future__ import annotations

import logging
import re
from datetime import timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.db_requests import extend_subscription, get_driver, get_stats, list_drivers
from bot.database.models import DriverStatus
from bot.keyboards.default_kb import BTN_CANCEL, main_menu_kb
from bot.keyboards.inline_kb import admin_menu_kb
from bot.states.order_states import AdminExtend

logger = logging.getLogger(__name__)
router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and user.id == settings.admin_id)


router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(F.message.chat.type == "private", IsAdmin())


def _fmt_stats(stats) -> str:
    return (
        "📊 <b>Karvon Taxi statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{stats.users}</b>\n"
        f"🚘 Haydovchilar: <b>{stats.drivers_total}</b>\n"
        f"   • Aktiv: {stats.drivers_active}\n"
        f"   • Obunali: {stats.drivers_paid}\n"
        f"   • Tugagan: {stats.drivers_expired}\n\n"
        f"📋 Buyurtmalar: <b>{stats.orders_total}</b>\n"
        f"   • Kutilmoqda: {stats.orders_pending}\n"
        f"   • Qabul qilingan: {stats.orders_accepted}\n"
        f"   • Tasdiqlangan: {stats.orders_confirmed}\n"
        f"   🚖 Taksi: {stats.orders_taxi}\n"
        f"   📦 Pochta: {stats.orders_parcel}"
    )


def _driver_line(driver) -> str:
    paid = "💳" if driver.has_paid_subscription() else "📌"
    status = driver.status
    until = ""
    if driver.subscription_until:
        dt = driver.subscription_until
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        until = f" · {dt.astimezone().strftime('%d.%m.%Y')}"
    return (
        f"{paid} <code>{driver.telegram_id}</code> {driver.full_name}\n"
        f"   {driver.car_model} {driver.car_number} · {status}{until}"
    )


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🔐 Admin panel:", reply_markup=admin_menu_kb())


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession) -> None:
    stats = await get_stats(session)
    await message.answer(_fmt_stats(stats))


@router.message(Command("drivers"))
async def cmd_drivers(message: Message, session: AsyncSession) -> None:
    drivers = await list_drivers(session)
    if not drivers:
        await message.answer("Haydovchilar yo'q.")
        return
    text = "🚘 <b>Haydovchilar</b>\n\n" + "\n".join(_driver_line(d) for d in drivers)
    await message.answer(text[:4000])


@router.message(Command("extend"))
async def cmd_extend(message: Message, session: AsyncSession) -> None:
    """
    /extend <telegram_id> <kun>
    Masalan: /extend 123456789 30
    """
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Format: <code>/extend 123456789 30</code>")
        return
    await _do_extend(message, session, int(parts[1]), int(parts[2]))


async def _groups_status_text(bot) -> str:
    me = await bot.get_me()
    lines = ["👥 <b>Guruhlar holati</b>\n"]
    for chat_id in settings.group_ids:
        try:
            chat = await bot.get_chat(chat_id)
            member = await bot.get_chat_member(chat_id, me.id)
            status = member.status
            star = "⭐ Asosiy · " if chat_id == settings.primary_group_id else ""
            if status == "administrator":
                can_post = getattr(member, "can_post_messages", True)
                extra = "xabar yozishi mumkin" if can_post is not False else "xabar yozish huquqi yo'q"
                lines.append(
                    f"✅ {star}<b>{chat.title}</b>\n"
                    f"   ID: <code>{chat_id}</code>\n"
                    f"   Holat: admin · {extra}"
                )
            elif status == "member":
                lines.append(
                    f"⚠️ {star}<b>{chat.title}</b>\n"
                    f"   ID: <code>{chat_id}</code>\n"
                    f"   Holat: a'zo, lekin admin emas"
                )
            elif status in {"left", "kicked"}:
                lines.append(
                    f"❌ ID: <code>{chat_id}</code>\n"
                    f"   Holat: guruhda yo'q ({status})"
                )
            else:
                lines.append(
                    f"⚠️ <b>{chat.title}</b>\n"
                    f"   ID: <code>{chat_id}</code>\n"
                    f"   Holat: {status}"
                )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            lines.append(
                f"❌ ID: <code>{chat_id}</code>\n"
                f"   Ulanmagan yoki bot admin emas.\n"
                f"   <code>{exc}</code>"
            )
    lines.append(
        "\nTekshirish: guruhda botni admin qiling, keyin yana /groups yuboring."
    )
    return "\n\n".join(lines)


@router.message(Command("groups"))
async def cmd_groups(message: Message) -> None:
    await message.answer(await _groups_status_text(message.bot))


@router.callback_query(F.data == "admin:groups")
async def cb_groups(callback: CallbackQuery) -> None:
    await callback.message.answer(await _groups_status_text(callback.bot))
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def cb_stats(callback: CallbackQuery, session: AsyncSession) -> None:
    stats = await get_stats(session)
    await callback.message.answer(_fmt_stats(stats))
    await callback.answer()


@router.callback_query(F.data == "admin:drivers")
async def cb_drivers(callback: CallbackQuery, session: AsyncSession) -> None:
    drivers = await list_drivers(session)
    if not drivers:
        await callback.message.answer("Haydovchilar yo'q.")
        await callback.answer()
        return
    await callback.message.answer(
        "🚘 <b>Haydovchilar</b>\n\n" + "\n".join(_driver_line(d) for d in drivers[:40])
    )
    await callback.answer()


@router.callback_query(F.data == "admin:extend")
async def cb_extend_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminExtend.driver_id)
    await callback.message.answer(
        "Haydovchining Telegram ID sini yuboring.\n"
        "Yoki: <code>/extend 123456789 30</code>"
    )
    await callback.answer()


@router.message(AdminExtend.driver_id, F.text)
async def admin_extend_id(message: Message, state: FSMContext) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    text = (message.text or "").strip()
    if not re.fullmatch(r"\d{5,15}", text):
        await message.answer("Faqat raqamli Telegram ID yuboring.")
        return
    await state.update_data(driver_id=int(text))
    await state.set_state(AdminExtend.days)
    await message.answer("Necha kunga uzaytirilsin? (masalan: 30)")


@router.message(AdminExtend.days, F.text)
async def admin_extend_days(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == BTN_CANCEL:
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb())
        return
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 366):
        await message.answer("1–366 orasidagi kunni yozing.")
        return
    data = await state.get_data()
    await state.clear()
    await _do_extend(message, session, int(data["driver_id"]), int(text))


async def _do_extend(message: Message, session: AsyncSession, driver_id: int, days: int) -> None:
    if days < 1 or days > 366:
        await message.answer("Kun 1–366 oralig'ida bo'lsin.")
        return
    was_expired = False
    existing = await get_driver(session, driver_id)
    if existing is None:
        await message.answer("Bunday haydovchi topilmadi.")
        return
    was_expired = existing.status == DriverStatus.EXPIRED.value or existing.kicked_at is not None

    driver = await extend_subscription(session, driver_id, days)
    if driver is None:
        await message.answer("Bunday haydovchi topilmadi.")
        return

    invite_links: list[str] = []
    if was_expired:
        from bot.handlers.group import create_driver_invite_links

        for chat_id in settings.group_ids:
            try:
                await message.bot.unban_chat_member(
                    chat_id=chat_id,
                    user_id=driver_id,
                    only_if_banned=True,
                )
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                logger.warning("Unban ishlamadi %s / %s: %s", driver_id, chat_id, exc)
        invite_links = await create_driver_invite_links(message.bot)

    until = driver.subscription_until
    if until and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    until_s = until.astimezone().strftime("%d.%m.%Y %H:%M") if until else "—"

    await message.answer(
        f"✅ Obuna uzaytirildi.\n"
        f"Haydovchi: {driver.full_name} (<code>{driver_id}</code>)\n"
        f"Muddat: <b>{until_s}</b> ({days} kun)"
    )
    try:
        text = (
            f"✅ Oylik obunangiz {days} kunga uzaytirildi.\n"
            f"Amal qilish muddati: <b>{until_s}</b>"
        )
        if invite_links:
            text += (
                f"\n\n⭐ Asosiy guruh ({settings.primary_group_title}):\n"
                f"{settings.primary_group_invite}"
            )
        await message.bot.send_message(driver_id, text)
    except TelegramForbiddenError:
        await message.answer("Haydovchiga lichka yozib bo'lmadi (botni bloklagan).")
