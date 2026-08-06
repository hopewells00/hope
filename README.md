# ربات مدیریت کانال تلگرام — Railway

## معرفی

این ربات با اکانت تلگرام شما وصل می‌شه، کانال‌ها رو لیست می‌کنه، پیام‌های اخیر هر کانال رو به یک فایل HTML شبیه تلگرام تبدیل می‌کنه، اون رو در یک ZIP رمزدار بسته‌بندی می‌کنه، پسوند رو به JPG تغییر می‌ده، به imgurl.ir آپلود می‌کنه، لینک رو رمزگذاری می‌کنه و از طریق پیامک ارسال می‌کنه.

---

## ۱. پیش‌نیازها

- حساب کاربری Railway
- ربات تلگرام (از @BotFather بگیر)
- API ID و API Hash از my.telegram.org
- حساب کاربری imgurl.ir (برای آپلود)
- حساب sms.ir (برای ارسال پیامک)

---

## ۲. ساخت SESSION_STRING (یک بار انجام بده)

روی کامپیوتر خودت (نه Railway) این دستورات رو بزن:

```bash
pip install telethon
python session_setup.py
```

شماره تلفن و کد تأیید رو وارد کن. مقدار SESSION_STRING رو که در پایان نمایش داده می‌شه کپی کن.

---

## ۳. تنظیم Environment Variables در Railway

در داشبورد Railway → پروژه → Variables این متغیرها رو اضافه کن:

| متغیر | توضیح | مثال |
|-------|-------|------|
| `BOT_TOKEN` | توکن ربات از @BotFather | `123456:ABC...` |
| `ADMIN_ID` | آی‌دی عددی تلگرام تو | `123456789` |
| `API_ID` | از my.telegram.org | `12345678` |
| `API_HASH` | از my.telegram.org | `abcdef...` |
| `SESSION_STRING` | خروجی session_setup.py | `1BVtsOK...` |
| `ZIP_PASS` | رمز ZIP (قوی انتخاب کن) | `MyStr0ngP@ss!` |
| `CRYPT_PASS` | رمز رمزگذاری URL (قوی) | `AnotherP@ss#99` |
| `IMGURL_SESSION` | مقدار کوکی `mmh_user_session` از سایت imgurl.ir | `a%3A2%3A%7B...` |
| `SMS_API_KEY` | کلید API از sms.ir | `your-api-key` |
| `SMS_LINE_NUMBER` | شماره خط sms.ir | `3000...` |
| `TARGET_PHONE` | شماره موبایل گیرنده پیامک | `09211722046` |
| `MESSAGE_COUNT` | تعداد پیام‌های اخیر (پیش‌فرض: ۳۰) | `30` |

---

## ۴. گرفتن کوکی imgurl.ir

1. به imgurl.ir برو و لاگین کن
2. F12 → Application → Cookies → imgurl.ir
3. مقدار `mmh_user_session` رو کپی کن
4. توی Railway به عنوان `IMGURL_SESSION` ثبت کن

> ⚠️ این کوکی منقضی می‌شه. هر بار که expire شد باید مجدد ثبت کنی.

---

## ۵. دیپلوی روی Railway

```bash
# اگه CLI داری:
railway up

# یا مستقیم از GitHub/GitLab به Railway متصل کن
```

Procfile مشخص کرده که چه دستوری اجرا می‌شه:
```
worker: python main.py
```

---

## ۶. نحوه استفاده

1. ربات رو در تلگرام پیدا کن و `/start` بزن
2. لیست کانال‌ها با عکس پروفایل، اسم و بیو نمایش داده می‌شه
3. با دکمه‌های **◀️ قبلی** و **▶️ بعدی** بین کانال‌ها جابجا شو
4. روی **✅ تایید** بزن تا پردازش شروع بشه
5. پیشرفت کار به صورت real-time نمایش داده می‌شه
6. در پایان:
   - فایل ZIP رمزدار ساخته می‌شه
   - به JPG تبدیل و آپلود می‌شه
   - لینک رمزگذاری می‌شه
   - پیامک ارسال می‌شه
   - متن رمزگذاری‌شده در چت نمایش داده می‌شه

---

## ۷. رمزگشایی لینک

برای رمزگشایی متن دریافتی، فایل `crypt.html` رو باز کن:
- برگه «رمزگشایی» رو انتخاب کن
- متن رمزگذاری‌شده رو وارد کن
- `CRYPT_PASS` رو وارد کن
- مقدار اصلی (متغیر لینک CDN) نمایش داده می‌شه
- لینک کامل: `https://cdn.imgurl.ir/uploads/[متغیر].jpg`

---

## ساختار فایل‌ها

```
railway-bot/
├── main.py            # منطق اصلی ربات + userbot
├── crypto_utils.py    # رمزگذاری AES-256-GCM (معادل Python از crypt.html)
├── html_generator.py  # تولید HTML شبیه تلگرام
├── uploader.py        # آپلود به imgurl.ir
├── sms_sender.py      # ارسال پیامک با sms.ir
├── session_setup.py   # ساخت SESSION_STRING (اجرا روی کامپیوتر)
├── requirements.txt
├── Procfile
└── README.md
```
