# Beshariq–Toshkent taksi boti

Aiogram 3 dagi bot. Hosting: **Railway**. Buyurtma, haydovchi va admin oqimlari o'z holicha qoladi. Quyidagi bo'lim guruhlardagi yo'lovchi xabarini **426. Global** asosiy guruhga zudlik bilan olib o'tadi.

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
