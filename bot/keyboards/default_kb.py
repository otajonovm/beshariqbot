"""Reply klaviaturalar: bosh menyu, haydovchi paneli, kontakt, bekor qilish."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_BT = "Beshariq ➡️ Toshkent"
BTN_TB = "Toshkent ➡️ Beshariq"
BTN_PARCEL = "📦 Pochta yuborish"
BTN_DRIVER = "🚘 Haydovchi sifatida ulanish"
BTN_BECOME_DRIVER = "🚖 Haydovchi bo'lish"
BTN_ANNOUNCE = "📢 E'lon berish"
BTN_REPEAT_LAST = "🔁 Oxirgi e'lonni takrorlash"
BTN_AUTO_POST = "⏰ Avto-e'lon"
BTN_AUTO_STOP = "⏹ To'xtatish"
BTN_NO_SEATS = "🚫 Joy yo'q"
BTN_AUTO_LAST = "📋 Oxirgisi"
BTN_AUTO_NEW = "🆕 Yangi tashlang"
BTN_AUTO_ENABLE = "✅ Yoqish"
BTN_INT_30 = "⏱ 30 soniya"
BTN_INT_60 = "⏱ 1 daqiqa"
BTN_DUR_60 = "🕐 1 soat"
BTN_DUR_120 = "🕐 2 soat"
BTN_DUR_180 = "🕐 3 soat"
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
    BTN_AUTO_POST,
    BTN_AUTO_STOP,
    BTN_NO_SEATS,
    BTN_PROFILE,
    BTN_PASSENGER_MENU,
    BTN_DRIVER_GROUPS,
}
AUTO_POST_WIZARD_TEXTS = {
    BTN_AUTO_LAST,
    BTN_AUTO_NEW,
    BTN_AUTO_ENABLE,
    BTN_INT_30,
    BTN_INT_60,
    BTN_DUR_60,
    BTN_DUR_120,
    BTN_DUR_180,
}
RESERVED_PRIVATE_TEXTS = DRIVER_PANEL_TEXTS | AUTO_POST_WIZARD_TEXTS | {
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

INTERVAL_BTN_BY_SEC = {30: BTN_INT_30, 60: BTN_INT_60}
DURATION_BTN_BY_MIN = {60: BTN_DUR_60, 120: BTN_DUR_120, 180: BTN_DUR_180}
INTERVAL_SEC_BY_BTN = {v: k for k, v in INTERVAL_BTN_BY_SEC.items()}
DURATION_MIN_BY_BTN = {v: k for k, v in DURATION_BTN_BY_MIN.items()}

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


def driver_panel_kb(
    *,
    has_last: bool = False,
    auto_active: bool = False,
) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BTN_ANNOUNCE)],
    ]
    if has_last:
        rows.append([KeyboardButton(text=BTN_REPEAT_LAST)])
    rows.append([KeyboardButton(text=BTN_AUTO_POST)])
    if auto_active:
        rows.append(
            [
                KeyboardButton(text=BTN_AUTO_STOP),
                KeyboardButton(text=BTN_NO_SEATS),
            ]
        )
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


def auto_post_source_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_AUTO_LAST)],
            [KeyboardButton(text=BTN_AUTO_NEW)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def auto_post_interval_kb(choices_sec: list[int] | None = None) -> ReplyKeyboardMarkup:
    secs = choices_sec or [30, 60]
    row: list[KeyboardButton] = []
    for sec in secs:
        label = INTERVAL_BTN_BY_SEC.get(sec)
        if label is None:
            if sec < 60:
                label = f"⏱ {sec} soniya"
            else:
                label = f"⏱ {sec // 60} daqiqa"
        row.append(KeyboardButton(text=label))
    return ReplyKeyboardMarkup(
        keyboard=[row, [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def auto_post_duration_kb(choices_min: list[int] | None = None) -> ReplyKeyboardMarkup:
    mins = choices_min or [60, 120, 180]
    row: list[KeyboardButton] = []
    for m in mins:
        label = DURATION_BTN_BY_MIN.get(m)
        if label is None:
            label = f"🕐 {m // 60} soat" if m % 60 == 0 else f"🕐 {m} daqiqa"
        row.append(KeyboardButton(text=label))
    return ReplyKeyboardMarkup(
        keyboard=[row, [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def auto_post_confirm_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_AUTO_ENABLE)],
            [KeyboardButton(text=BTN_ABORT)],
        ],
        resize_keyboard=True,
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
