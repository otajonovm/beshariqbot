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
    name = State()
    car_model = State()
    car_number = State()
    phone = State()


class AdminExtend(StatesGroup):
    driver_id = State()
    days = State()
