"""
اسکریپت برای ساخت SESSION_STRING از اکانت تلگرام
این اسکریپت رو یک بار روی کامپیوتر خودت اجرا کن (نه Railway)
و مقدار خروجی رو توی Railway به عنوان SESSION_STRING ثبت کن.

نیاز داری:
  pip install telethon
اجرا:
  python session_setup.py
"""

import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID   = int(input("API_ID رو وارد کن: ").strip())
API_HASH = input("API_HASH رو وارد کن: ").strip()

async def generate():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        session_string = client.session.save()
        print("\n" + "=" * 60)
        print("SESSION_STRING (این مقدار رو در Railway ثبت کن):")
        print(session_string)
        print("=" * 60)

asyncio.run(generate())
