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
    departure = State()
    custom_time = State()


class DriverRawPost(StatesGroup):
    """Lichkadagi xom e'lon — vaqt, oldindan ko'rish / tasdiq."""

    departure = State()
    custom_time = State()
    confirm = State()


class DriverDepartureEdit(StatesGroup):
    """Profil: ketish vaqti shablonini o'zgartirish."""

    picking = State()
    custom_time = State()


class AdminExtend(StatesGroup):
    driver_id = State()
    days = State()
