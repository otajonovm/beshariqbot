"""Reply klaviaturalar: bosh menyu, kontakt, bekor qilish."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_BT = "Beshariq ➡️ Toshkent"
BTN_TB = "Toshkent ➡️ Beshariq"
BTN_PARCEL = "📦 Pochta yuborish"
BTN_DRIVER = "🚘 Haydovchi sifatida ulanish"
BTN_CANCEL = "⬅️ Bekor qilish"
BTN_SHARE_CONTACT = "📲 Raqamni yuborish"

DIRECTION_BY_BTN = {
    BTN_BT: "beshariq_tashkent",
    BTN_TB: "tashkent_beshariq",
}


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BT)],
            [KeyboardButton(text=BTN_TB)],
            [KeyboardButton(text=BTN_PARCEL)],
            [KeyboardButton(text=BTN_DRIVER)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Yo'nalishni tanlang",
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


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
