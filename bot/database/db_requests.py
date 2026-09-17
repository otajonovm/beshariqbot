"""Atomar async CRUD va race-condition himoyasi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
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

ClaimResult = Literal["ok", "taken", "cooldown", "not_found", "inactive", "not_driver"]


@dataclass(slots=True)
class Stats:
    users: int
    drivers_total: int
    drivers_active: int
    drivers_trial: int
    drivers_expired: int
    orders_total: int
    orders_pending: int
    orders_accepted: int
    orders_confirmed: int
    orders_taxi: int
    orders_parcel: int


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
    existing = await session.get(Driver, telegram_id)
    if existing is not None:
        existing.full_name = full_name
        existing.username = username
        existing.car_model = car_model
        existing.car_number = car_number
        existing.phone = phone
        if existing.status == DriverStatus.BANNED.value:
            await session.flush()
            return existing
        if not existing.is_access_valid() and not existing.has_paid_subscription():
            await session.flush()
            return existing
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
        notified_day5=False,
        notified_day7=False,
    )
    session.add(driver)
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


async def claim_order(
    session: AsyncSession,
    order_id: int,
    driver_id: int,
) -> tuple[ClaimResult, Order | None]:
    """
    Birinchi bosgan haydovchi yutadi.

    SQLite WAL + bitta tranzaksiyadagi SELECT + shartli UPDATE
    ikki haydovchi bir vaqtda bosganda ikkinchisini rad etadi.
    """
    driver = await session.get(Driver, driver_id)
    if driver is None:
        return "not_driver", None
    if not driver.is_access_valid():
        return "inactive", None

    async with session.begin_nested():
        order = await session.get(Order, order_id, with_for_update=True)
        if order is None:
            return "not_found", None
        if order.status != OrderStatus.PENDING.value:
            return "taken", order
        if driver_id in order.rejected_id_list():
            return "cooldown", order

        result = await session.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.status == OrderStatus.PENDING.value,
            )
            .values(
                status=OrderStatus.ACCEPTED.value,
                driver_id=driver_id,
                updated_at=utcnow(),
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
        order.updated_at = utcnow()

    await session.commit()
    await session.refresh(order)
    return order


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
    await session.commit()
    return driver


async def mark_driver_notified(session: AsyncSession, driver_id: int, day: int) -> None:
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


async def list_trial_drivers(session: AsyncSession) -> list[Driver]:
    stmt = select(Driver).where(Driver.status != DriverStatus.BANNED.value)
    return list((await session.execute(stmt)).scalars().all())


async def list_drivers(session: AsyncSession, limit: int = 30) -> list[Driver]:
    stmt = select(Driver).order_by(Driver.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_stats(session: AsyncSession) -> Stats:
    now = utcnow()
    users = (await session.execute(select(func.count(User.telegram_id)))).scalar_one()
    drivers = list((await session.execute(select(Driver))).scalars().all())
    active = sum(1 for d in drivers if d.is_access_valid(now))
    expired = sum(1 for d in drivers if d.status == DriverStatus.EXPIRED.value)
    trial = sum(
        1
        for d in drivers
        if d.status == DriverStatus.ACTIVE.value and not d.has_paid_subscription(now)
    )

    async def _count(*where: Any) -> int:
        stmt = select(func.count(Order.id))
        for clause in where:
            stmt = stmt.where(clause)
        return int((await session.execute(stmt)).scalar_one())

    return Stats(
        users=int(users),
        drivers_total=len(drivers),
        drivers_active=active,
        drivers_trial=trial,
        drivers_expired=expired,
        orders_total=await _count(),
        orders_pending=await _count(Order.status == OrderStatus.PENDING.value),
        orders_accepted=await _count(Order.status == OrderStatus.ACCEPTED.value),
        orders_confirmed=await _count(Order.status == OrderStatus.CONFIRMED.value),
        orders_taxi=await _count(Order.order_type == OrderType.TAXI.value),
        orders_parcel=await _count(Order.order_type == OrderType.PARCEL.value),
    )
