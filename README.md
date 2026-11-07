# Beshariq–Toshkent taksi boti

Aiogram 3 dagi bot. Hosting: **Railway**. Buyurtma, haydovchi va admin oqimlari o'z holicha qoladi. Quyidagi bo'lim guruhlardagi yo'lovchi xabarini **426. Global** asosiy guruhga zudlik bilan olib o'tadi.

## Deploy oldidan: DB zaxira nusxasi (majburiy)

Yangi haydovchi profil/migratsiya o'zgarishlarini chiqarishdan **oldin** bazani zaxiralang. Mavjud haydovchilar o'chirilmaydi va qayta to'liq ro'yxatdan o'tkazilmaydi; shunga qaramay zaxira shart.

**Postgres (Railway):**

```bash
# Lokalga dump
railway connect Postgres
# yoki
pg_dump "$DATABASE_URL" -Fc -f backup_$(date +%Y%m%d).dump

# Tiklash (kerak bo'lsa)
pg_restore -d "$DATABASE_URL" --clean --if-exists backup_YYYYMMDD.dump
```

**SQLite (faqat lokal):**

```bash
copy data\bot.db data\bot.db.bak
# yoki
cp data/bot.db "data/bot.db.bak.$(date +%Y%m%d)"
```

Migratsiya idempotent: qayta ishga tushganda dublikat yaratmaydi va mavjud telefon/mashina maydonlarini ustidan yozmaydi. Mavjud haydovchilar `active`/`expired` holatida qoladi; faqat **yangi** arizalar admin tasdig'ini kutadi (`/drivers`, `/block`, `/unblock`).

## Guruhdan asosiy guruhga tezkor yo'naltirish

Bot admin bo'lgan guruhda odam taksi so'rasa (`taksi kerak`, `Toshkentga ketaman`, `такси нужно`, `1 kishi` va shu kabilar), bot shu xabarni asosiy guruhga karta qilib yuboradi. Haydovchi e'lonlari (`joy bor`, `Cobalt bo'sh`, `свободно`) va reklama havolalari o'tkazilmaydi.

Yuborishdan oldin baza va tashqi API chaqirilmaydi. Statistika yuborilgandan keyin fonda yoziladi. Logda qator:

```text
relay delay_ms=180 since_msg_ms=240 chat_id=... message_id=...
```

`delay_ms` — bot xabarni qo'liga olgandan asosiy guruhga `sendMessage` qaytgunicha. `since_msg_ms` — Telegramdagi xabar vaqtidan hisoblangan to'liq vaqt.

### Muhit o'zgaruvchilari

| O'zgaruvchi | Ma'nosi |
| --- | --- |
| `BOT_TOKEN` | BotFather tokeni |
| `ADMIN_ID` | Asosiy admin |
| `ADMIN_IDS` | Qo'shimcha adminlar, vergul bilan. `/stats`, `/groups`, `/addword`, `/delword` faqat shu odamlarga, shaxsiy chatda |
| `PRIMARY_GROUP_ID` | Mavjud 426. Global guruh. `MAIN_GROUP_ID` berilmasa shu ishlatiladi |
| `MAIN_GROUP_ID` | Yo'naltirish tushadigan guruh. Bo'sh bo'lsa `PRIMARY_GROUP_ID` |
| `SOURCE_GROUP_IDS` | Qaysi guruhlardan olinadi. Bo'sh bo'lsa — bot turgan barcha guruhlar, asosiydan tashqari |
| `SWEEP_GROUP_IDS` | Admin emas yozsa xabar **darhol o'chirilib** asosiy guruhga o'tkaziladi. Standart: `-1003189462638` |
| `SUPERGROUP_IDS` | Eski buyurtma/admin guruhlari ro'yxati (yo'naltirishdan alohida) |
| `DEDUP_MINUTES` | Bir odam bir xil matnni shu daqiqa ichida faqat bir marta yuboradi. Standart: `10` |
| `RELAY_MIN_SCORE` | Yo'lovchi balli chegarasi. Standart: `3` |
| `RELAY_MAX_CHARS` | Bundan uzun xabar spam hisoblanadi. Standart: `400` |
| `RELAY_RATE_PER_MINUTE` | Asosiy guruhga daqiqalik limit (Telegram ~20). Standart: `18`. Limit ichida kutish yo'q |
| `USE_WEBHOOK` | `auto` (Railway'da webhook, lokalda polling), `0` (faqat polling), `1` (webhook) |
| `WEBHOOK_URL` | Masalan `https://your-service.up.railway.app`. Bo'sh bo'lsa Railway `RAILWAY_PUBLIC_DOMAIN` ishlatiladi |
| `WEBHOOK_SECRET` | Ixtiyoriy. Bo'sh bo'lsa token dan barqaror sirf hosil qilinadi |
| `DATABASE_URL` | Railway Postgres. Statistika va `/addword` shu yerda saqlanadi |

Kalit so'zlarning asosiy ro'yxati: `bot/relay/keywords.py`. Ishlayotgan botda qo'shish:

```text
/addword yolovchi taksi shart
/addword haydovchi bosh joy
/delword joy bor
```

`/stats` — bugun nechta xabar yo'naltirilgani, guruhlar bo'yicha va o'rtacha `delay_ms`.
`/groups` — sozlangan guruhlar, asosiy guruh va bot xabar ko'rgan guruhlar.

### BotFather va guruh huquqlari

1. BotFather → `/setprivacy` → **Disable**. Aks holda bot guruhdagi oddiy xabarlarni ko'rmaydi.
2. Privacy o'zgargach botni har bir manba guruhdan chiqarib, qayta qo'shing. Eski a'zolik yangi rejimni olmaydi.
3. Botni har bir manba guruhga **admin** qiling (xabarlarni o'qishi uchun).
4. Asosiy guruhda (426. Global) bot admin bo'lsin va **xabar yozish** huquqi yoqilgan bo'lsin. Aks holda karta chiqmaydi.
5. Railway'da bitta service yetarli. Public Domain yoqing, Postgres ulang (`DATABASE_URL`).

### Guruh ID sini topish

- Botni guruhga qo'shing. Admin lichkasiga `ID: -100...` keladi (bot guruhga qo'shilganda o'zi yozadi).
- Shaxsiy chatda `/groups` ham ID larni ko'rsatadi.
- Yoki `@getidsbot` / `@userinfobot` ga guruhdan xabar yuboring.

Superguruh ID si odatda `-100` bilan boshlanadi. `t.me/c/...` havoladagi raqam oldiga `-100` qo'yiladi.

### Tezlik

Aniqlash (lotin/kirill, ball) import paytida yig'ilgan regexda bajariladi. 90 ta misolni o'lchaganda har bir matn taxminan **0.01 ms**. Asosiy vaqt — Telegram `sendMessage` javobi. Railway'dan bu odatda **100–400 ms** (`delay_ms`).

Webhook rejimida Telegram xabarni darhol beradi, qo'shimcha poll kutishi yo'q. Polling qolsa, long poll **50 soniya** — bu kechikish emas: ulanish ochiq turadi va xabar kelishi bilan darhol olinadi. `handle_as_tasks` yoqilgan, shuning uchun bitta sekin ish keyingisini to'smaydi.

Oddiy holatda navbat **0 ms**. Daqiqasiga 18 tadan oshsa yoki Telegram `429 Retry-After` bersa, shu xabar navbatda kutadi, qolganlari shu limitga rioya qiladi.

Kutilayotgan yo'l, webhook va iliq ulanishda: foydalanuvchi yuborganidan asosiy guruhda ko'ringuncha ko'pincha **200–700 ms** (Telegram yetkazishi + bitta `sendMessage`). Bundan tezroq bo'lish uchun qo'shimcha baza so'rovi qo'yilmagan.

Kartada ism (`tg://user`), telefon (matnda bo'lsa), «Yo'lovchiga yozish» va «Asl xabar» tugmalari bor. Forward ishlatilmaydi: odam forwardni yopgan bo'lsa haydovchi ismni ko'rmas edi.

## Haydovchi: lichkaga e'lon tashlash (sodda yo'l)

Eski panel («📢 E'lon berish» wizard) o'z holicha ishlayveradi. Qo'shimcha sodda yo'l:

1. Tasdiqlangan (`active`) haydovchi bot **lichkasiga** o'z e'lon matnini yozadi yoki boshqa chatdan **forward** / copy-paste qiladi (rasm + caption ham bo'ladi).
2. Bot matnni buzmaydi: asl matn saqlanadi, pastiga ixtiyoriy telefon/`@username` va «Bot orqali» qo'shilishi mumkin.
3. Standart holatda oldindan ko'rish chiqadi: **✅ Hamma guruhlarga yuborish** / **❌ Bekor qilish**. Tasdiqlanmasa guruhga chiqmaydi.
4. Tasdiqdan keyin **ulangan barcha guruhlarga** (426. Global dan tashqari) yuboriladi. **426. Global** — faqat yo'lovchi/taksi hub; haydovchi e'loni u yerga tushmaydi.
5. Oxirida: `Yuborildi: N ta guruh` (+ xato bo'lgan guruhlar).
6. Soatlik spam limati: `DRIVER_POST_LIMIT_PER_HOUR` (standart 20).
7. Tez takrorlash: **🔁 Oxirgi e'lonni takrorlash**.

**Nima e'lon emas:** `/start` va boshqa buyruqlar, panel tugmalari, FSM javoblari (anketa, narx, bo'sh joy).

| O'zgaruvchi | Ma'nosi |
| --- | --- |
| `DRIVER_RAW_CONFIRM` | `true` (standart) — preview; `false` — darhol yuborish |
| `DRIVER_POST_LIMIT_PER_HOUR` | Soatiga nechta xom e'lon. Standart: `20` |
| `DRIVER_POST_GROUP_IDS` | Bo'sh = barcha `SUPERGROUP_IDS` minus **426. Global** |

Mavjud haydovchilar qayta ro'yxatdan o'tkazilmaydi; yetishmasa faqat bo'sh maydon so'raladi.

### Ketish vaqti

E'lon (raw yoki «📢 E'lon berish») yuborishdan oldin bot ketish vaqtini so'raydi:

- Shablonlar: Hozir yuryapman, 30 daqiqa / 1–2 soat, Bugun kechqurun, Ertaga ertalab
- **💾 Oxirgisi** — saqlangan matn bir bosishda
- **⏰ Hozirgi soat** — Toshkent vaqti (`Bugun soat 16:30 da`)
- **✍️ O'zim yozaman** — erkin matn (max 80 belgi)

Tanlangan matn `last_departure_text` da saqlanadi. «🔁 Oxirgi e'lonni takrorlash» da saqlangan vaqt ishlatiladi; **Vaqtni o'zgartirish** tugmasi bor. Profilida ham ko'rish/o'zgartirish mumkin.

### Guruhdagi e'lon stili

Haydovchi e'loni guruhga rasm + qatorma-qator caption bilan chiqadi (`👤 Haydovchi: HOZIRGA`, yo'nalish, joy, pochta, avto, tel…). Inline tugmalar: **🚖 Zakas berish** (bot lichkasida haydovchiga xabar) va **📞 +998…** (WhatsApp/qo'ng'iroq havolasi; telefon yo'q bo'lsa **✍️ Yozish**).

### Profilni tahrirlash

**👤 Profilim** — ism, telefon, mashina, rang, raqam, joylar, yo'nalish, vaqt shabloni va rasm. Har maydon yonida **✏️** tugma: faqat shu maydon so'raladi, qolganlari o'zgarmaydi. Keyingi e'lonlarda yangi ma'lumot ishlatiladi; qayta registratsiya yo'q.

### Avto-e'lon

Panelda **⏰ Avto-e'lon**:

1. Qaysi e'lon: **Oxirgisi** (standart) yoki **Yangi tashlang**
2. Interval: **30 soniya** | **1 daqiqa** (standart: 1 daqiqa)
3. Davomiylik: **1 / 2 / 3 soat** (standart: 2 soat, max `AUTO_POST_MAX_HOURS`)
4. Tasdiq → yoqiladi; panelda **⏹ To'xtatish** / **🚫 Joy yo'q** ko'rinadi

Har tickda e'lon hozirgi formatda (rasm, vaqt, telefon, Zakas) birinchi haydovchi-post guruhiga, keyin qolganlarga yuboriladi. `RELAY_REPOST_DELETE_OLD=true` bo'lsa: avval yangisi, keyin o'sha guruhdagi eski avto-xabar o'chiriladi. Bitta haydovchida bir vaqtda faqat 1 ta faol sessiya; holat DB da (`auto_post_sessions`) — bot restart dan keyin davom etadi. 429/guruh limiti kutib qayta urinadi, butun bot to'xtamaydi.

| O'zgaruvchi | Ma'nosi |
| --- | --- |
| `AUTO_POST_INTERVALS_SEC` | Interval tanlovlari. Standart: `30,60` |
| `AUTO_POST_DURATIONS_MIN` | Davomiylik (daqiqa). Standart: `60,120,180` |
| `AUTO_POST_MAX_HOURS` | Maksimal soat. Standart: `3` |
| `RELAY_REPOST_DELETE_OLD` | Eski avto-xabarni o'chirish. Standart: `true` |
