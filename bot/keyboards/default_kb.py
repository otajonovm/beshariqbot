"""Reply klaviaturalar: bosh menyu, haydovchi paneli, kontakt, bekor qilish."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_BT = "Beshariq ➡️ Toshkent"
BTN_TB = "Toshkent ➡️ Beshariq"
BTN_PARCEL = "📦 Pochta yuborish"
BTN_DRIVER = "🚘 Haydovchi sifatida ulanish"
BTN_BECOME_DRIVER = "🚖 Haydovchi bo'lish"
BTN_ANNOUNCE = "📢 E'lon berish"
BTN_REPEAT_LAST = "🔁 Oxirgi e'lonni takrorlash"
BTN_PROFILE = "👤 Profilim"
BTN_PASSENGER_MENU = "🏠 Yo'lovchi menyusi"
BTN_DRIVER_GROUPS = "⭐ Guruhlar"
BTN_CANCEL = "⬅️ Bekor qilish"
BTN_BACK = "⬅️ Orqaga"
BTN_ABORT = "❌ Bekor qilish"
BTN_SKIP = "⏭ O'tkazib yuborish"
BTN_SHARE_CONTACT = "📲 Raqamni yuborish"
BTN_DELETE_PHOTO = "🗑 Rasmni o'chirish"

# Panel / menyu tugmalari — xom e'lon deb olinmasin
DRIVER_PANEL_TEXTS = {
    BTN_ANNOUNCE,
    BTN_REPEAT_LAST,
    BTN_PROFILE,
    BTN_PASSENGER_MENU,
    BTN_DRIVER_GROUPS,
}
RESERVED_PRIVATE_TEXTS = DRIVER_PANEL_TEXTS | {
    BTN_BT,
    BTN_TB,
    BTN_PARCEL,
    BTN_DRIVER,
    BTN_BECOME_DRIVER,
    BTN_CANCEL,
    BTN_BACK,
    BTN_ABORT,
    BTN_SKIP,
    BTN_SHARE_CONTACT,
    BTN_DELETE_PHOTO,
}

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


def driver_panel_kb(*, has_last: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BTN_ANNOUNCE)],
    ]
    if has_last:
        rows.append([KeyboardButton(text=BTN_REPEAT_LAST)])
    rows.extend(
        [
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_DRIVER_GROUPS)],
            [KeyboardButton(text=BTN_PASSENGER_MENU)],
        ]
    )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="E'lon matnini yozing yoki tashlang",
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


def profile_edit_nav_kb(*, with_contact: bool = False) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    if with_contact:
        rows.append([KeyboardButton(text=BTN_SHARE_CONTACT, request_contact=True)])
    rows.append([KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_ABORT)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def profile_photo_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_DELETE_PHOTO)],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_ABORT)],
        ],
        resize_keyboard=True,
    )


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
