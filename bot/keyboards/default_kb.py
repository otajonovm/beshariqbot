"""Reply klaviaturalar: bosh menyu, haydovchi paneli, kontakt, bekor qilish."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_BT = "Beshariq ➡️ Toshkent"
BTN_TB = "Toshkent ➡️ Beshariq"
BTN_PARCEL = "📦 Pochta yuborish"
BTN_DRIVER = "🚘 Haydovchi sifatida ulanish"
BTN_BECOME_DRIVER = "🚖 Haydovchi bo'lish"
BTN_ANNOUNCE = "📢 E'lon berish"
BTN_PROFILE = "👤 Profilim"
BTN_PASSENGER_MENU = "🏠 Yo'lovchi menyusi"
BTN_DRIVER_GROUPS = "⭐ Guruhlar"
BTN_CANCEL = "⬅️ Bekor qilish"
BTN_SKIP = "⏭ O'tkazib yuborish"
BTN_SHARE_CONTACT = "📲 Raqamni yuborish"

DIRECTION_BY_BTN = {
    BTN_BT: "beshariq_tashkent",
    BTN_TB: "tashkent_beshariq",
}

DRIVER_START_BUTTONS = {BTN_DRIVER, BTN_BECOME_DRIVER}


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BT)],
            [KeyboardButton(text=BTN_TB)],
            [KeyboardButton(text=BTN_PARCEL)],
            [KeyboardButton(text=BTN_BECOME_DRIVER)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Yo'nalishni tanlang",
    )


def driver_panel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ANNOUNCE)],
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_DRIVER_GROUPS)],
            [KeyboardButton(text=BTN_PASSENGER_MENU)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Haydovchi paneli",
    )


def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHARE_CONTACT, request_contact=True)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def skip_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SKIP)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
