"""FSM holatlari: taksi, pochta va haydovchi ro'yxati."""

from aiogram.fsm.state import State, StatesGroup


class TaxiOrder(StatesGroup):
    seats = State()
    contact = State()


class ParcelOrder(StatesGroup):
    direction = State()
    description = State()
    contact = State()


class DriverReg(StatesGroup):
    """Faqat YANGI haydovchi — to'liq ariza."""

    name = State()
    phone = State()
    car_model = State()
    car_number = State()
    car_color = State()
    seats_count = State()
    photo = State()


class DriverMissing(StatesGroup):
    """Mavjud haydovchidan faqat yetishmayotgan maydonlar."""

    collecting = State()


class DriverAnnounce(StatesGroup):
    direction = State()
    free_seats = State()


class DriverRawPost(StatesGroup):
    """Lichkadagi xom e'lon — oldindan ko'rish / tasdiq."""

    confirm = State()


class AdminExtend(StatesGroup):
    driver_id = State()
    days = State()
