"""Atomar async CRUD va race-condition himoyasi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from bot.database.models import (
    Driver,
    DriverStatus,
    Order,
    OrderStatus,
    OrderType,
    User,
    utcnow,
)

ClaimResult = Literal["ok", "taken", "cooldown", "not_found"]


@dataclass(slots=True)
class Stats:
    users: int
    drivers_total: int
    drivers_active: int
    drivers_paid: int
    drivers_expired: int
    orders_total: int
    orders_pending: int
    orders_accepted: int
    orders_confirmed: int
    orders_taxi: int
    orders_parcel: int


@dataclass(slots=True)
class LeaderboardEntry:
    driver_id: int
    orders_count: int
    taxi_count: int
    parcel_count: int
    full_name: str
    username: str | None
    car_model: str
    car_number: str


@dataclass(slots=True)
class LeaderboardData:
    start_dt: datetime
    end_dt: datetime
    total_orders: int
    total_taxi: int
    total_parcel: int
    top_drivers: list[LeaderboardEntry]


async def upsert_user(
    session: AsyncSession,
    telegram_id: int,
    full_name: str,
    username: str | None,
    phone: str | None = None,
) -> User:
    user = await session.get(User, telegram_id)
    if user is None:
        user = User(
            telegram_id=telegram_id,
            full_name=full_name,
            username=username,
            phone=phone,
        )
        session.add(user)
    else:
        user.full_name = full_name
        user.username = username
        if phone:
            user.phone = phone
    await session.flush()
    return user


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.get(User, telegram_id)


async def get_driver(session: AsyncSession, telegram_id: int) -> Driver | None:
    return await session.get(Driver, telegram_id)


def _nonempty(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


async def update_driver_fields(
    session: AsyncSession,
    telegram_id: int,
    *,
    full_name: str | None = None,
    username: str | None = None,
    car_model: str | None = None,
    car_number: str | None = None,
    phone: str | None = None,
    car_color: str | None = None,
    seats_count: int | None = None,
    photo_file_id: str | None = None,
    last_departure_text: str | None = None,
    clear_photo: bool = False,
) -> Driver | None:
    """Mavjud haydovchi maydonlarini to'ldirish. Bo'sh qiymat ustiga yozilmaydi."""
    driver = await session.get(Driver, telegram_id)
    if driver is None:
        return None
    if full_name is not None and len(full_name.strip()) >= 3:
        driver.full_name = full_name.strip()
    if username is not None:
        driver.username = username
    if (model := _nonempty(car_model)) is not None:
        driver.car_model = model
    if (number := _nonempty(car_number)) is not None:
        driver.car_number = number
    if (phone_v := _nonempty(phone)) is not None:
        driver.phone = phone_v
    if (color := _nonempty(car_color)) is not None:
        driver.car_color = color
    if seats_count is not None and seats_count > 0:
        driver.seats_count = seats_count
    if clear_photo:
        driver.photo_file_id = None
    elif (photo := _nonempty(photo_file_id)) is not None:
        driver.photo_file_id = photo
    if last_departure_text is not None:
        cleaned = last_departure_text.strip()
        if cleaned:
            driver.last_departure_text = cleaned[:120]
    await session.flush()
    return driver


async def submit_driver_application(
    session: AsyncSession,
    *,
    telegram_id: int,
    full_name: str,
    username: str | None,
    car_model: str,
    car_number: str,
    phone: str,
    car_color: str | None = None,
    seats_count: int | None = None,
    photo_file_id: str | None = None,
) -> tuple[Driver, bool]:
    """
    Yangi haydovchi arizasi (pending).

    Qaytaradi: (driver, created_new_application).
    Mavjud active/expired haydovchini pendingga tushirmaydi — faqat maydonlarni to'ldiradi.
    """
    existing = await session.get(Driver, telegram_id)
    if existing is not None and existing.is_legacy_or_approved():
        await update_driver_fields(
            session,
            telegram_id,
            full_name=full_name,
            username=username,
            car_model=car_model,
            car_number=car_number,
            phone=phone,
            car_color=car_color,
            seats_count=seats_count,
            photo_file_id=photo_file_id,
        )
        await session.refresh(existing)
        return existing, False

    if existing is not None:
        # pending / rejected / banned — ariza maydonlarini yangilash (banned status saqlanadi)
        existing.full_name = full_name.strip()
        existing.username = username
        existing.car_model = car_model.strip()
        existing.car_number = car_number.strip()
        existing.phone = phone.strip()
        if (color := _nonempty(car_color)) is not None:
            existing.car_color = color
        if seats_count is not None and seats_count > 0:
            existing.seats_count = seats_count
        if (photo := _nonempty(photo_file_id)) is not None:
            existing.photo_file_id = photo
        if existing.status != DriverStatus.BANNED.value:
            existing.status = DriverStatus.PENDING.value
            existing.kicked_at = None
        await session.flush()
        return existing, existing.status == DriverStatus.PENDING.value

    driver = Driver(
        telegram_id=telegram_id,
        full_name=full_name.strip(),
        username=username,
        car_model=car_model.strip(),
        car_number=car_number.strip(),
        phone=phone.strip(),
        car_color=_nonempty(car_color),
        seats_count=seats_count if seats_count and seats_count > 0 else None,
        photo_file_id=_nonempty(photo_file_id),
        trial_start=utcnow(),
        status=DriverStatus.PENDING.value,
        notified_day5=True,
        notified_day7=True,
        notified_sub_10=False,
        notified_sub_5=False,
        notified_sub_1=False,
    )
    session.add(driver)
    await session.flush()
    return driver, True


async def create_driver(
    session: AsyncSession,
    *,
    telegram_id: int,
    full_name: str,
    username: str | None,
    car_model: str,
    car_number: str,
    phone: str,
) -> Driver:
    """Eski API: mavjud haydovchini yangilaydi yoki active yaratadi (legacy)."""
    existing = await session.get(Driver, telegram_id)
    if existing is not None:
        await update_driver_fields(
            session,
            telegram_id,
            full_name=full_name,
            username=username,
            car_model=car_model,
            car_number=car_number,
            phone=phone,
        )
        if existing.status not in {
            DriverStatus.BANNED.value,
            DriverStatus.PENDING.value,
            DriverStatus.REJECTED.value,
        }:
            existing.status = DriverStatus.ACTIVE.value
            existing.kicked_at = None
        await session.flush()
        return existing

    driver = Driver(
        telegram_id=telegram_id,
        full_name=full_name,
        username=username,
        car_model=car_model,
        car_number=car_number,
        phone=phone,
        trial_start=utcnow(),
        status=DriverStatus.ACTIVE.value,
        notified_day5=True,
        notified_day7=True,
        notified_sub_10=False,
        notified_sub_5=False,
        notified_sub_1=False,
    )
    session.add(driver)
    await session.flush()
    return driver


async def approve_driver(session: AsyncSession, telegram_id: int) -> Driver | None:
    driver = await session.get(Driver, telegram_id)
    if driver is None:
        return None
    if driver.status == DriverStatus.BANNED.value:
        return driver
    driver.status = DriverStatus.ACTIVE.value
    driver.kicked_at = None
    await session.flush()
    return driver


async def reject_driver(session: AsyncSession, telegram_id: int) -> Driver | None:
    driver = await session.get(Driver, telegram_id)
    if driver is None:
        return None
    if driver.status == DriverStatus.BANNED.value:
        return driver
    driver.status = DriverStatus.REJECTED.value
    await session.flush()
    return driver


async def block_driver(session: AsyncSession, telegram_id: int) -> Driver | None:
    driver = await session.get(Driver, telegram_id)
    if driver is None:
        return None
    driver.status = DriverStatus.BANNED.value
    driver.kicked_at = utcnow()
    await session.flush()
    return driver


async def unblock_driver(session: AsyncSession, telegram_id: int) -> Driver | None:
    driver = await session.get(Driver, telegram_id)
    if driver is None:
        return None
    if driver.status != DriverStatus.BANNED.value:
        return driver
    driver.status = DriverStatus.ACTIVE.value
    driver.kicked_at = None
    await session.flush()
    return driver


async def create_order(
    session: AsyncSession,
    *,
    order_type: str,
    passenger_id: int,
    direction: str,
    phone: str,
    area: str | None = None,
    area_custom: str | None = None,
    seats: str | None = None,
    departure_time: str | None = None,
    cargo_description: str | None = None,
    photo_file_id: str | None = None,
) -> Order:
    order = Order(
        order_type=order_type,
        status=OrderStatus.PENDING.value,
        passenger_id=passenger_id,
        direction=direction,
        phone=phone,
        area=area,
        area_custom=area_custom,
        seats=seats,
        departure_time=departure_time,
        cargo_description=cargo_description,
        photo_file_id=photo_file_id,
        rejected_drivers=[],
    )
    session.add(order)
    await session.flush()
    return order


async def get_order(
    session: AsyncSession,
    order_id: int,
    *,
    with_relations: bool = False,
) -> Order | None:
    if with_relations:
        stmt = (
            select(Order)
            .options(selectinload(Order.passenger), selectinload(Order.driver))
            .where(Order.id == order_id)
        )
        return (await session.execute(stmt)).scalar_one_or_none()
    return await session.get(Order, order_id)


async def set_group_posts(
    session: AsyncSession, order_id: int, posts: dict[int, int]
) -> None:
    payload = {str(chat_id): message_id for chat_id, message_id in posts.items()}
    await session.execute(
        update(Order).where(Order.id == order_id).values(group_posts=payload)
    )


async def set_order_claim_message(
    session: AsyncSession, order_id: int, message_id: int
) -> None:
    """Haydovchiga yuborilgan tasdiqlash xabari ID sini saqlaydi."""
    await session.execute(
        update(Order).where(Order.id == order_id).values(claim_message_id=message_id)
    )
    await session.commit()


async def claim_order(
    session: AsyncSession,
    order_id: int,
    driver_id: int,
    *,
    full_name: str = "",
    username: str | None = None,
) -> tuple[ClaimResult, Order | None]:
    """
    Birinchi bosgan haydovchi yutadi.

    Ro'yxatdan o'tish shart emas — drivers jadvaliga yozilmaydi.
    SELECT FOR UPDATE ikki haydovchini ajratadi.
    """
    del full_name, username  # API mosligi uchun qoldirilgan

    async with session.begin_nested():
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return "not_found", None
        if order.status != OrderStatus.PENDING.value:
            return "taken", order
        if driver_id in order.rejected_id_list():
            return "cooldown", order

        now = utcnow()
        result = await session.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.status == OrderStatus.PENDING.value,
            )
            .values(
                status=OrderStatus.ACCEPTED.value,
                driver_id=driver_id,
                claimed_at=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await session.refresh(order)
            return "taken", order

        await session.refresh(order)

    await session.commit()
    order = await get_order(session, order_id, with_relations=True)
    return "ok", order


async def release_claim(
    session: AsyncSession,
    order_id: int,
    driver_id: int,
) -> Order | None:
    """Lichkaga yozib bo'lmasa qabulni rollback qilish (cooldown yozilmaydi)."""
    order = await session.get(Order, order_id)
    if (
        order is None
        or order.driver_id != driver_id
        or order.status != OrderStatus.ACCEPTED.value
    ):
        return None
    order.driver_id = None
    order.status = OrderStatus.PENDING.value
    order.claimed_at = None
    order.claim_message_id = None
    order.updated_at = utcnow()
    await session.commit()
    return order


async def confirm_deal(session: AsyncSession, order_id: int, driver_id: int) -> Order | None:
    order = await session.get(Order, order_id)
    if (
        order is None
        or order.driver_id != driver_id
        or order.status != OrderStatus.ACCEPTED.value
    ):
        return None
    order.status = OrderStatus.CONFIRMED.value
    order.claim_message_id = None
    order.updated_at = utcnow()
    await session.commit()
    return order


async def cancel_deal_and_reopen(
    session: AsyncSession,
    order_id: int,
    driver_id: int,
    reason: str,
) -> Order | None:
    """Kelisha olmadi: haydovchi cooldown ro'yxatiga, buyurtma qayta PENDING."""
    async with session.begin_nested():
        order = await session.get(Order, order_id, with_for_update=True)
        if (
            order is None
            or order.driver_id != driver_id
            or order.status != OrderStatus.ACCEPTED.value
        ):
            return None
        order.add_rejected_driver(driver_id)
        flag_modified(order, "rejected_drivers")
        order.driver_id = None
        order.status = OrderStatus.PENDING.value
        order.last_cancel_reason = reason
        order.claimed_at = None
        order.claim_message_id = None
        order.updated_at = utcnow()

    await session.commit()
    await session.refresh(order)
    return order


async def list_expired_claimed_orders(
    session: AsyncSession, timeout_seconds: int = 180
) -> list[Order]:
    """3 daqiqa ichida 'Ha, oldim' bosilmagan qabul qilingan buyurtmalarni topadi."""
    threshold = utcnow() - timedelta(seconds=timeout_seconds)
    stmt = (
        select(Order)
        .options(selectinload(Order.passenger))
        .where(
            Order.status == OrderStatus.ACCEPTED.value,
            or_(
                and_(Order.claimed_at.is_not(None), Order.claimed_at <= threshold),
                and_(Order.claimed_at.is_(None), Order.updated_at <= threshold),
            ),
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def expire_claimed_order(
    session: AsyncSession, order_id: int
) -> Order | None:
    """3 daqiqalik vaqt tugagan buyurtmani guruhga qayta ochish va cooldown belgilash."""
    async with session.begin_nested():
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None or order.status != OrderStatus.ACCEPTED.value:
            return None

        if order.driver_id:
            order.add_rejected_driver(order.driver_id)
            flag_modified(order, "rejected_drivers")

        order.driver_id = None
        order.status = OrderStatus.PENDING.value
        order.last_cancel_reason = "timeout_3min"
        order.claimed_at = None
        order.updated_at = utcnow()

    await session.commit()
    return await get_order(session, order_id, with_relations=True)


async def extend_subscription(
    session: AsyncSession,
    driver_id: int,
    days: int,
) -> Driver | None:
    driver = await session.get(Driver, driver_id)
    if driver is None:
        return None
    now = utcnow()
    base = driver.subscription_until if driver.has_paid_subscription(now) else now
    if base is not None and base.tzinfo is None:
        from bot.database.models import TZ_UTC

        base = base.replace(tzinfo=TZ_UTC)
    driver.subscription_until = (base or now) + timedelta(days=days)
    driver.status = DriverStatus.ACTIVE.value
    driver.kicked_at = None
    driver.notified_day5 = True
    driver.notified_day7 = True
    # Yangi muddat — eslatmalar qayta yuboriladi
    driver.notified_sub_10 = False
    driver.notified_sub_5 = False
    driver.notified_sub_1 = False
    await session.commit()
    return driver


async def mark_sub_notified(session: AsyncSession, driver_id: int, days: int) -> None:
    values: dict[str, Any] = {}
    if days == 10:
        values["notified_sub_10"] = True
    elif days == 5:
        values["notified_sub_5"] = True
    elif days == 1:
        values["notified_sub_1"] = True
    if values:
        await session.execute(update(Driver).where(Driver.telegram_id == driver_id).values(**values))
        await session.commit()


async def mark_driver_notified(session: AsyncSession, driver_id: int, day: int) -> None:
    """Eski trial flaglari (moslik)."""
    values: dict[str, Any] = {}
    if day == 5:
        values["notified_day5"] = True
    elif day == 7:
        values["notified_day7"] = True
    if values:
        await session.execute(update(Driver).where(Driver.telegram_id == driver_id).values(**values))
        await session.commit()


async def mark_driver_expired(session: AsyncSession, driver_id: int) -> None:
    await session.execute(
        update(Driver)
        .where(Driver.telegram_id == driver_id)
        .values(status=DriverStatus.EXPIRED.value, kicked_at=utcnow())
    )
    await session.commit()


async def list_subscription_drivers(session: AsyncSession) -> list[Driver]:
    """Obuna muddati tekshiriladigan haydovchilar (bloklanmaganlar)."""
    stmt = select(Driver).where(Driver.status != DriverStatus.BANNED.value)
    return list((await session.execute(stmt)).scalars().all())


async def list_drivers(session: AsyncSession, limit: int = 50) -> list[Driver]:
    """Barcha haydovchilar (stub emas: ism yoki majburiy maydon bor)."""
    stmt = select(Driver).order_by(Driver.created_at.desc()).limit(max(limit * 2, 50))
    all_rows = list((await session.execute(stmt)).scalars().all())
    real: list[Driver] = []
    for d in all_rows:
        if d.is_profile_complete() or d.is_legacy_or_approved() or d.status in {
            DriverStatus.PENDING.value,
            DriverStatus.REJECTED.value,
            DriverStatus.BANNED.value,
        }:
            real.append(d)
        elif len((d.full_name or "").strip()) >= 3:
            real.append(d)
    return real[:limit]


async def get_stats(session: AsyncSession) -> Stats:
    now = utcnow()
    users = (await session.execute(select(func.count(User.telegram_id)))).scalar_one()
    drivers = [
        d
        for d in (await session.execute(select(Driver))).scalars().all()
        if d.is_profile_complete()
    ]
    active = sum(1 for d in drivers if d.is_access_valid(now))
    expired = sum(1 for d in drivers if d.status == DriverStatus.EXPIRED.value)
    paid = sum(1 for d in drivers if d.has_paid_subscription(now))

    async def _count(*where: Any) -> int:
        stmt = select(func.count(Order.id))
        for clause in where:
            stmt = stmt.where(clause)
        return int((await session.execute(stmt)).scalar_one())

    return Stats(
        users=int(users),
        drivers_total=len(drivers),
        drivers_active=active,
        drivers_paid=paid,
        drivers_expired=expired,
        orders_total=await _count(),
        orders_pending=await _count(Order.status == OrderStatus.PENDING.value),
        orders_accepted=await _count(Order.status == OrderStatus.ACCEPTED.value),
        orders_confirmed=await _count(Order.status == OrderStatus.CONFIRMED.value),
        orders_taxi=await _count(Order.order_type == OrderType.TAXI.value),
        orders_parcel=await _count(Order.order_type == OrderType.PARCEL.value),
    )


async def get_leaderboard_data(
    session: AsyncSession,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = 10,
) -> LeaderboardData:
    """Belgilangan vaqt oralig'idagi tasdiqlangan buyurtmalar va top haydovchilar reytingi."""
    totals_stmt = select(
        func.count(Order.id),
        func.coalesce(func.sum(case((Order.order_type == OrderType.TAXI.value, 1), else_=0)), 0),
        func.coalesce(func.sum(case((Order.order_type == OrderType.PARCEL.value, 1), else_=0)), 0),
    ).where(
        Order.status == OrderStatus.CONFIRMED.value,
        Order.updated_at >= start_dt,
        Order.updated_at <= end_dt,
    )
    totals_row = (await session.execute(totals_stmt)).one()
    total_orders = int(totals_row[0] or 0)
    total_taxi = int(totals_row[1] or 0)
    total_parcel = int(totals_row[2] or 0)

    drivers_stmt = (
        select(
            Order.driver_id,
            func.count(Order.id).label("total_orders"),
            func.coalesce(func.sum(case((Order.order_type == OrderType.TAXI.value, 1), else_=0)), 0).label("taxi_orders"),
            func.coalesce(func.sum(case((Order.order_type == OrderType.PARCEL.value, 1), else_=0)), 0).label("parcel_orders"),
            Driver.full_name,
            Driver.username,
            Driver.car_model,
            Driver.car_number,
        )
        .outerjoin(Driver, Order.driver_id == Driver.telegram_id)
        .where(
            Order.status == OrderStatus.CONFIRMED.value,
            Order.driver_id.is_not(None),
            Order.updated_at >= start_dt,
            Order.updated_at <= end_dt,
        )
        .group_by(
            Order.driver_id,
            Driver.full_name,
            Driver.username,
            Driver.car_model,
            Driver.car_number,
        )
        .order_by(func.count(Order.id).desc())
        .limit(limit)
    )
    rows = (await session.execute(drivers_stmt)).all()

    top_drivers: list[LeaderboardEntry] = []
    for row in rows:
        top_drivers.append(
            LeaderboardEntry(
                driver_id=int(row[0]),
                orders_count=int(row[1]),
                taxi_count=int(row[2]),
                parcel_count=int(row[3]),
                full_name=row[4] or "Haydovchi",
                username=row[5],
                car_model=row[6] or "Avtomobil",
                car_number=row[7] or "—",
            )
        )

    return LeaderboardData(
        start_dt=start_dt,
        end_dt=end_dt,
        total_orders=total_orders,
        total_taxi=total_taxi,
        total_parcel=total_parcel,
        top_drivers=top_drivers,
    )
