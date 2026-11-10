"""Inline tugmalar: yo'nalish, hudud, qabul qilish, kelishuv."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import CANCEL_REASON_LABELS


def seats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="seats:1 kishi"),
                InlineKeyboardButton(text="2", callback_data="seats:2 kishi"),
                InlineKeyboardButton(text="3", callback_data="seats:3 kishi"),
                InlineKeyboardButton(text="4", callback_data="seats:4 kishi"),
            ],
            [InlineKeyboardButton(text="Butun salon", callback_data="seats:Butun salon")],
        ]
    )


def skip_photo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Rasm yo'q — davom etish", callback_data="parcel:nophoto")]
        ]
    )


def group_invite_kb(links: list[str] | None = None) -> InlineKeyboardMarkup:
    from bot.config import settings

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"⭐ Asosiy guruh ({settings.primary_group_title})",
                url=settings.primary_group_invite,
            )
        ]
    ]
    seen = {settings.primary_group_invite.rstrip("/")}
    extra = 0
    for url in links or []:
        key = url.rstrip("/")
        if not url or key in seen:
            continue
        seen.add(key)
        extra += 1
        rows.append([InlineKeyboardButton(text=f"👥 Guruh {extra}", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def claim_kb(order_id: int) -> InlineKeyboardMarkup:
    from bot.config import settings

    url = f"https://t.me/{settings.bot_username.lstrip('@')}?start=claim_{order_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Buyurtmani olish",
                    url=url,
                    style="success",
                )
            ]
        ]
    )


def deal_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha, oldim",
                    callback_data=f"deal_ok:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Yo'q, kelisha olmadik",
                    callback_data=f"deal_cancel:{order_id}",
                )
            ],
        ]
    )


def cancel_reason_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"creason:{order_id}:{code}",
                )
            ]
            for code, label in CANCEL_REASON_LABELS.items()
        ]
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    """Yangi sodda bosh menyu — ko'pi bilan 6 ta katta tugma."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚖 Haydovchilar", callback_data="adm:drv:list:0")],
            [InlineKeyboardButton(text="👥 Guruhlar", callback_data="adm:grp:list")],
            [InlineKeyboardButton(text="💳 To'lovlar", callback_data="adm:pay:menu")],
            [InlineKeyboardButton(text="⭐ Premium", callback_data="adm:prem:menu")],
            [InlineKeyboardButton(text="📣 Reklama", callback_data="adm:ad:menu")],
            [InlineKeyboardButton(text="📊 Statistika", callback_data="adm:stats")],
        ]
    )


def admin_nav_row(*, back: str = "adm:home", home: bool = True) -> list[InlineKeyboardButton]:
    row = [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back)]
    if home:
        row.append(InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="adm:home"))
    return row


def admin_drivers_list_kb(
    drivers: list,
    *,
    page: int,
    total: int,
    page_size: int = 10,
    query: str = "",
    pending_count: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for d in drivers:
        mark = "✅"
        st = getattr(d, "status", "")
        if st == "pending":
            mark = "⏳"
        elif st == "rejected":
            mark = "❌"
        elif st == "banned":
            mark = "⛔"
        elif st == "deleted" or getattr(d, "deleted_at", None):
            mark = "🗑"
        label = f"{mark} {d.full_name}"[:40]
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"adm:drv:card:{d.telegram_id}")]
        )
    pages = max(1, (total + page_size - 1) // page_size)
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️", callback_data=f"adm:drv:list:{page - 1}")
        )
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="adm:noop"))
    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(text="▶️", callback_data=f"adm:drv:list:{page + 1}")
        )
    if nav:
        rows.append(nav)
    pending_label = "⏳ Kutayotgan arizalar"
    if pending_count > 0:
        pending_label = f"⏳ Kutayotgan arizalar ({pending_count})"
    rows.append(
        [InlineKeyboardButton(text=pending_label, callback_data="adm:drv:pending")]
    )
    rows.append(
        [InlineKeyboardButton(text="🔎 Qidiruv", callback_data="adm:drv:search")]
    )
    rows.append(admin_nav_row(back="adm:home"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_driver_card_kb(driver) -> InlineKeyboardMarkup:
    banned = getattr(driver, "status", "") == "banned"
    deleted = getattr(driver, "status", "") == "deleted" or getattr(driver, "deleted_at", None)
    pending = getattr(driver, "status", "") == "pending"
    rejected = getattr(driver, "status", "") == "rejected"
    rows: list[list[InlineKeyboardButton]] = []
    if deleted:
        rows.append(
            [
                InlineKeyboardButton(
                    text="♻️ Tiklash",
                    callback_data=f"adm:drv:restore:{driver.telegram_id}",
                )
            ]
        )
        rows.append(admin_nav_row(back="adm:drv:list:0"))
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # Yangi / rad etilgan ariza — barcha adminlar tasdiqlay oladi
    if pending or rejected:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data=f"adm:drv:approve:{driver.telegram_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"adm:drv:reject:{driver.telegram_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✍️ Haydovchiga xabar",
                    callback_data=f"adm:drv:msg:{driver.telegram_id}",
                )
            ]
        )
        rows.append(admin_nav_row(back="adm:drv:pending"))
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if banned:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Blokdan chiqarish",
                    callback_data=f"adm:drv:unban:{driver.telegram_id}",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⛔ Bloklash",
                    callback_data=f"adm:drv:ban:{driver.telegram_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 O'chirish",
                callback_data=f"adm:drv:delask:{driver.telegram_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⭐ Premium funksiyalar",
                callback_data=f"adm:drv:prem:{driver.telegram_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="💳 To'lov qabul qilindi",
                callback_data=f"adm:drv:paid:{driver.telegram_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="📅 To'lov sanasini o'zgartirish",
                callback_data=f"adm:drv:duedate:{driver.telegram_id}",
            )
        ]
    )
    exempt = bool(getattr(driver, "payment_exempt", False))
    rows.append(
        [
            InlineKeyboardButton(
                text="🎁 Bepul" if not exempt else "💳 To'lovga qaytarish",
                callback_data=f"adm:drv:exempt:{driver.telegram_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="✍️ Haydovchiga xabar",
                callback_data=f"adm:drv:msg:{driver.telegram_id}",
            )
        ]
    )
    rows.append(admin_nav_row(back="adm:drv:list:0"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm_delete_kb(driver_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha, o'chirish",
                    callback_data=f"adm:drv:del:{driver_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Yo'q",
                    callback_data=f"adm:drv:card:{driver_id}",
                )
            ],
        ]
    )


def admin_driver_premium_kb(driver) -> InlineKeyboardMarkup:
    from bot.services.feature_flags import PREMIUM_KEYS, FLAG_BY_KEY, driver_feature_enabled

    rows: list[list[InlineKeyboardButton]] = []
    for key in PREMIUM_KEYS:
        meta = FLAG_BY_KEY[key]
        on = driver_feature_enabled(key, driver)
        mark = "✅" if on else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {meta.title}",
                    callback_data=f"adm:drv:ptog:{driver.telegram_id}:{key}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="⭐ Paket 7 kun", callback_data=f"adm:drv:ppack:{driver.telegram_id}:7"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="⭐ Paket 30 kun", callback_data=f"adm:drv:ppack:{driver.telegram_id}:30"),
            InlineKeyboardButton(text="⭐ Paket 90 kun", callback_data=f"adm:drv:ppack:{driver.telegram_id}:90"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Paketni o'chirish",
                callback_data=f"adm:drv:ppack:{driver.telegram_id}:0",
            )
        ]
    )
    rows.append(admin_nav_row(back=f"adm:drv:card:{driver.telegram_id}"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_pay_days_kb(driver_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="+7 kun", callback_data=f"adm:drv:payok:{driver_id}:7"),
                InlineKeyboardButton(text="+30 kun", callback_data=f"adm:drv:payok:{driver_id}:30"),
                InlineKeyboardButton(text="+90 kun", callback_data=f"adm:drv:payok:{driver_id}:90"),
            ],
            admin_nav_row(back=f"adm:drv:card:{driver_id}"),
        ]
    )


def admin_payments_menu_kb(*, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=("✅ To'lovlar yoqilgan" if enabled else "❌ To'lovlar o'chiq"),
                    callback_data="adm:pay:toggle",
                )
            ],
            [InlineKeyboardButton(text="📋 Kutilayotganlar", callback_data="adm:pay:due")],
            [InlineKeyboardButton(text="⚠️ Kechikkanlar", callback_data="adm:pay:late")],
            [InlineKeyboardButton(text="📜 Oxirgi to'lovlar", callback_data="adm:pay:hist")],
            admin_nav_row(back="adm:home"),
        ]
    )


def admin_premium_global_kb() -> InlineKeyboardMarkup:
    from bot.services.feature_flags import FLAG_DEFS, snapshot

    vals = snapshot()
    rows: list[list[InlineKeyboardButton]] = []
    for meta in FLAG_DEFS:
        if not meta.premium:
            continue
        mark = "✅" if vals.get(meta.key, True) else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {meta.title}",
                    callback_data=f"admin:flag:{meta.key}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🎛 Barcha funksiyalar", callback_data="admin:flags")]
    )
    rows.append(admin_nav_row(back="adm:home"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_groups_list_kb(groups: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for g in groups:
        star = "⭐ " if getattr(g, "is_main", False) else ""
        kind = "📢 " if getattr(g, "chat_type", "") == "channel" else ""
        ad = "" if getattr(g, "flag_bot_ads", True) else "🔇"
        title = (g.title or str(g.chat_id))[:32]
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{star}{kind}{ad}{title}",
                    callback_data=f"adm:grp:card:{g.chat_id}",
                )
            ]
        )
    rows.append(admin_nav_row(back="adm:home"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_group_card_kb(group) -> InlineKeyboardMarkup:
    def mark(v: bool) -> str:
        return "✅" if v else "❌"

    cid = group.chat_id
    rows = [
        [
            InlineKeyboardButton(
                text=f"{mark(group.flag_relay_source)} So'rovlarni 426 ga",
                callback_data=f"adm:grp:tog:{cid}:relay",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{mark(group.flag_driver_posts)} Haydovchi e'lonlari",
                callback_data=f"adm:grp:tog:{cid}:driver",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{mark(group.flag_auto_posts)} Avto-e'lon",
                callback_data=f"adm:grp:tog:{cid}:auto",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{mark(group.flag_bot_ads)} Bot reklamasi",
                callback_data=f"adm:grp:tog:{cid}:ads",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{mark(group.flag_delete_original)} Asl xabarni o'chirish",
                callback_data=f"adm:grp:tog:{cid}:delete",
            )
        ],
    ]
    if not group.is_main:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⭐ Asosiy qilish",
                    callback_data=f"adm:grp:main:{cid}",
                )
            ]
        )
    rows.append(admin_nav_row(back="adm:grp:list"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_group_approve_kb(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yoqish", callback_data=f"adm:grp:ok:{chat_id}"),
                InlineKeyboardButton(text="❌ Yo'q", callback_data=f"adm:grp:no:{chat_id}"),
            ]
        ]
    )


def admin_ads_menu_kb(
    *,
    enabled: bool,
    interval_label: str = "",
    duration_label: str = "",
) -> InlineKeyboardMarkup:
    toggle = "⏸ To'xtatish" if enabled else "▶️ Yoqish"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle, callback_data="adm:ad:toggle")],
            [
                InlineKeyboardButton(
                    text=f"⏱ Interval: {interval_label or '—'}",
                    callback_data="adm:ad:interval",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⌛ Davomiylik: {duration_label or '—'}",
                    callback_data="adm:ad:duration",
                )
            ],
            [InlineKeyboardButton(text="🕘 Faol soatlar", callback_data="adm:ad:window")],
            [
                InlineKeyboardButton(
                    text="🖼 Banner rasmini o'zgartirish",
                    callback_data="adm:ad:photo",
                )
            ],
            [InlineKeyboardButton(text="✏️ Matnni o'zgartirish", callback_data="adm:ad:text")],
            [InlineKeyboardButton(text="🔘 Tugma matni", callback_data="adm:ad:btn")],
            [
                InlineKeyboardButton(
                    text="📞 Dispetcher raqami",
                    callback_data="adm:ad:phone",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏢 426 Global reklama",
                    callback_data="adm:ad:main",
                )
            ],
            [InlineKeyboardButton(text="👁 Ko'rish", callback_data="adm:ad:preview")],
            [
                InlineKeyboardButton(
                    text="♻️ Default dizaynga qaytarish",
                    callback_data="adm:ad:reset",
                )
            ],
            [InlineKeyboardButton(text="🚀 Hozir yuborish", callback_data="adm:ad:now")],
            admin_nav_row(back="adm:home"),
        ]
    )


def admin_ad_interval_kb() -> InlineKeyboardMarkup:
    from bot.services.app_settings import AD_INTERVAL_CHOICES, format_interval_human

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for sec in AD_INTERVAL_CHOICES:
        row.append(
            InlineKeyboardButton(
                text=format_interval_human(sec),
                callback_data=f"adm:ad:intsec:{sec}",
            )
        )
        if len(row) >= 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(admin_nav_row(back="adm:ad:menu"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ad_duration_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Doimiy (faol soatlarda)",
                    callback_data="adm:ad:dur:0",
                )
            ],
            [
                InlineKeyboardButton(text="1 soat", callback_data="adm:ad:dur:3600"),
                InlineKeyboardButton(text="2 soat", callback_data="adm:ad:dur:7200"),
                InlineKeyboardButton(text="3 soat", callback_data="adm:ad:dur:10800"),
            ],
            admin_nav_row(back="adm:ad:menu"),
        ]
    )


def admin_stats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Bugun", callback_data="adm:stats:1"),
                InlineKeyboardButton(text="🗓 7 kun", callback_data="adm:stats:7"),
            ],
            [
                InlineKeyboardButton(text="🏆 Reyting", callback_data="admin:lb_menu"),
            ],
            admin_nav_row(back="adm:home"),
        ]
    )


def admin_flags_kb(values: dict[str, bool] | None = None) -> InlineKeyboardMarkup:
    from bot.services.feature_flags import FLAG_DEFS, snapshot

    vals = values if values is not None else snapshot()
    rows: list[list[InlineKeyboardButton]] = []
    for meta in FLAG_DEFS:
        on = bool(vals.get(meta.key, True))
        mark = "✅" if on else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {meta.title}",
                    callback_data=f"admin:flag:{meta.key}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin:back_main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def driver_approval_kb(driver_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data=f"drv_approve:{driver_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"drv_reject:{driver_id}",
                ),
            ]
        ]
    )


def raw_post_confirm_kb(*, show_change_time: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✅ Hamma guruhlarga yuborish",
                callback_data="rawpost:ok",
            )
        ],
    ]
    if show_change_time:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🕐 Vaqtni o'zgartirish",
                    callback_data="rawpost:changetime",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Bekor qilish",
                callback_data="rawpost:cancel",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def departure_time_kb(
    last_text: str | None = None,
    *,
    prefix: str = "dep",
) -> InlineKeyboardMarkup:
    """Ketish vaqti shablonlari. prefix: dep | adep | pdep (raw / announce / profile)."""
    rows: list[list[InlineKeyboardButton]] = []
    last = (last_text or "").strip()
    if last:
        label = last if len(last) <= 28 else last[:25] + "..."
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💾 Oxirgisi: {label}",
                    callback_data=f"{prefix}:last",
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Hozir yuryapman",
                    callback_data=f"{prefix}:now",
                )
            ],
            [
                InlineKeyboardButton(
                    text="30 daqiqadan keyin",
                    callback_data=f"{prefix}:30m",
                ),
                InlineKeyboardButton(
                    text="1 soatdan keyin",
                    callback_data=f"{prefix}:1h",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="2 soatdan keyin",
                    callback_data=f"{prefix}:2h",
                ),
                InlineKeyboardButton(
                    text="Bugun kechqurun",
                    callback_data=f"{prefix}:eve",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Ertaga ertalab",
                    callback_data=f"{prefix}:tmr",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Hozirgi soatni qo'yish",
                    callback_data=f"{prefix}:clock",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ O'zim yozaman",
                    callback_data=f"{prefix}:custom",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=f"{prefix}:cancel",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_departure_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🕐 Ketish vaqtini o'zgartirish",
                    callback_data="prof:time",
                )
            ]
        ]
    )


def profile_edit_kb(*, show_photo: bool = True) -> InlineKeyboardMarkup:
    """Har maydon uchun ✏️ O'zgartirish. show_photo=False → rasm qatori yo'q."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="✏️ Ism", callback_data="prof:name"),
            InlineKeyboardButton(text="✏️ Telefon", callback_data="prof:phone"),
        ],
        [
            InlineKeyboardButton(text="✏️ Mashina", callback_data="prof:car"),
            InlineKeyboardButton(text="✏️ Rang", callback_data="prof:color"),
        ],
        [
            InlineKeyboardButton(text="✏️ Raqam", callback_data="prof:plate"),
            InlineKeyboardButton(text="✏️ Joylar", callback_data="prof:seats"),
        ],
        [
            InlineKeyboardButton(text="✏️ Yo'nalish", callback_data="prof:route"),
            InlineKeyboardButton(text="✏️ Vaqt", callback_data="prof:time"),
        ],
    ]
    if show_photo:
        rows.append(
            [InlineKeyboardButton(text="✏️ Rasm", callback_data="prof:photo")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_ready_kb() -> InlineKeyboardMarkup:
    """Profil saqlangach: e'longa chiqishini tasdiqlash."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha, e'lonlarga shu profil",
                    callback_data="profok:yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Yana tahrirlash",
                    callback_data="profok:edit",
                )
            ],
        ]
    )


def profile_seats_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="profseats:1"),
                InlineKeyboardButton(text="2", callback_data="profseats:2"),
                InlineKeyboardButton(text="3", callback_data="profseats:3"),
                InlineKeyboardButton(text="4", callback_data="profseats:4"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Orqaga", callback_data="prof:back"),
                InlineKeyboardButton(text="❌ Bekor", callback_data="prof:abort"),
            ],
        ]
    )


def profile_route_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Beshariq ➡️ Toshkent",
                    callback_data="profroute:beshariq_tashkent",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Toshkent ➡️ Beshariq",
                    callback_data="profroute:tashkent_beshariq",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Default yo'q",
                    callback_data="profroute:clear",
                )
            ],
            [
                InlineKeyboardButton(text="⬅️ Orqaga", callback_data="prof:back"),
                InlineKeyboardButton(text="❌ Bekor", callback_data="prof:abort"),
            ],
        ]
    )


def skip_optional_kb(prefix: str = "drvskip") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data=f"{prefix}:skip")]
        ]
    )


def admin_leaderboard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Bugungi kunlik", callback_data="admin:lb:daily"),
                InlineKeyboardButton(text="🌟 Haftalik", callback_data="admin:lb:weekly"),
                InlineKeyboardButton(text="👑 Oylik", callback_data="admin:lb:monthly"),
            ],
            [
                InlineKeyboardButton(text="📢 Guruhga e'lon (Kunlik)", callback_data="admin:lb_post:daily"),
            ],
            [
                InlineKeyboardButton(text="📢 Guruhga e'lon (Haftalik)", callback_data="admin:lb_post:weekly"),
            ],
            [
                InlineKeyboardButton(text="📢 Guruhga e'lon (Oylik)", callback_data="admin:lb_post:monthly"),
            ],
            [
                InlineKeyboardButton(text="◀️ Orqaga", callback_data="admin:back_main"),
            ],
        ]
    )
