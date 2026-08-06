"""
ربات مدیریت کانال‌های تلگرام
- اتصال به اکانت کاربری با Telethon (StringSession)
- پنل مدیریت از طریق ربات تلگرام
- export پیام‌های کانال به HTML + ZIP رمزدار
- آپلود به imgurl.ir و ارسال لینک رمزگذاری‌شده از طریق پیامک
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import pyzipper
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel, PeerChannel, MessageMediaPhoto, MessageMediaDocument
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from crypto_utils import encrypt
from html_generator import generate_html
from sms_sender import send_sms
from uploader import extract_variable, upload_file

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config از Environment Variables ──────────────────────────────────────────
BOT_TOKEN       = os.environ["BOT_TOKEN"]
ADMIN_ID        = int(os.environ["ADMIN_ID"])
API_ID          = int(os.environ["API_ID"])
API_HASH        = os.environ["API_HASH"]
SESSION_STRING  = os.environ["SESSION_STRING"]
ZIP_PASS        = os.environ["ZIP_PASS"]
CRYPT_PASS      = os.environ["CRYPT_PASS"]
MESSAGE_COUNT   = int(os.environ.get("MESSAGE_COUNT", "30"))
MAX_ZIP_MB      = int(os.environ.get("MAX_ZIP_MB", "50"))

# ── Telethon client (global, shared) ─────────────────────────────────────────
userbot: Optional[TelegramClient] = None


# ═══════════════════════════════════════════════════════════════════════════
#  کمک‌کننده‌های Userbot
# ═══════════════════════════════════════════════════════════════════════════

async def get_channels() -> list[dict]:
    """لیست همه کانال‌هایی که اکانت عضوشه رو برمی‌گردونه."""
    channels = []
    async for dialog in userbot.iter_dialogs():
        if isinstance(dialog.entity, Channel) and dialog.entity.broadcast:
            entity = dialog.entity
            channels.append({
                "id": entity.id,
                "username": getattr(entity, "username", None),
                "title": entity.title or "",
                "about": "",  # بعداً با getFullChannel پر می‌شه
            })
    return channels


async def get_channel_bio(channel_id: int) -> str:
    try:
        full = await userbot.get_entity(PeerChannel(channel_id))
        from telethon.tl.functions.channels import GetFullChannelRequest
        full_info = await userbot(GetFullChannelRequest(full))
        return full_info.full_chat.about or ""
    except Exception as e:
        logger.warning("bio fetch error: %s", e)
        return ""


async def download_channel_photo(channel_id: int, dest_dir: str) -> Optional[str]:
    """عکس پروفایل کانال رو دانلود و مسیرش رو برمی‌گردونه."""
    try:
        entity = await userbot.get_entity(PeerChannel(channel_id))
        path = await userbot.download_profile_photo(entity, file=os.path.join(dest_dir, "avatar.jpg"))
        return path
    except Exception as e:
        logger.warning("avatar download error: %s", e)
        return None


async def fetch_messages(channel_id: int, limit: int) -> list[dict]:
    """آخرین `limit` پیام کانال رو می‌گیره."""
    entity = await userbot.get_entity(PeerChannel(channel_id))
    messages = []
    async for msg in userbot.iter_messages(entity, limit=limit):
        entry: dict = {
            "text": msg.text or msg.message or "",
            "date": msg.date,
            "views": getattr(msg, "views", 0) or 0,
            "reactions": [],
            "fwd_from": None,
            "media_path": None,
            "sender": "",
        }
        # Forwarded
        if msg.forward and msg.forward.channel_post:
            try:
                fwd_entity = await msg.forward.get_sender()
                if fwd_entity:
                    entry["fwd_from"] = getattr(fwd_entity, "title", None) or getattr(fwd_entity, "username", "")
            except Exception:
                pass

        # Reactions
        if hasattr(msg, "reactions") and msg.reactions:
            for r in (msg.reactions.results or []):
                emoji = getattr(r.reaction, "emoticon", "?")
                entry["reactions"].append({"emoji": emoji, "count": r.count})

        messages.append(entry)

    return list(reversed(messages))  # ترتیب قدیمی به جدید


async def download_media_for_messages(
    channel_id: int, limit: int, dest_dir: str
) -> list[dict]:
    """پیام‌ها رو با رسانه‌هاشون دانلود می‌کنه."""
    entity = await userbot.get_entity(PeerChannel(channel_id))
    messages = []
    idx = 0
    async for msg in userbot.iter_messages(entity, limit=limit):
        entry: dict = {
            "text": msg.text or msg.message or "",
            "date": msg.date,
            "views": getattr(msg, "views", 0) or 0,
            "reactions": [],
            "fwd_from": None,
            "media_path": None,
            "sender": "",
        }
        # Forwarded from
        if msg.forward:
            try:
                fwd_title = None
                if hasattr(msg.forward, "channel_id") and msg.forward.channel_id:
                    fwd_entity = await userbot.get_entity(PeerChannel(msg.forward.channel_id))
                    fwd_title = getattr(fwd_entity, "title", None)
                entry["fwd_from"] = fwd_title or ""
            except Exception:
                pass

        # Reactions
        if hasattr(msg, "reactions") and msg.reactions:
            for r in (msg.reactions.results or []):
                emoji = getattr(r.reaction, "emoticon", "?")
                entry["reactions"].append({"emoji": emoji, "count": r.count})

        # Media download
        if msg.media:
            try:
                if isinstance(msg.media, MessageMediaPhoto):
                    ext = ".jpg"
                elif isinstance(msg.media, MessageMediaDocument):
                    doc = msg.media.document
                    mime = getattr(doc, "mime_type", "")
                    if "video" in mime:
                        ext = ".mp4"
                    elif "audio" in mime or "ogg" in mime:
                        ext = ".ogg"
                    elif "image" in mime:
                        ext = ".jpg"
                    else:
                        ext = ""
                else:
                    ext = ""

                if ext:
                    media_path = os.path.join(dest_dir, f"media_{idx}{ext}")
                    await userbot.download_media(msg, file=media_path)
                    entry["media_path"] = media_path
            except Exception as e:
                logger.warning("media download error msg %d: %s", idx, e)

        messages.append(entry)
        idx += 1

    return list(reversed(messages))


# ═══════════════════════════════════════════════════════════════════════════
#  ساخت ZIP رمزدار
# ═══════════════════════════════════════════════════════════════════════════

def create_protected_zip(html_content: str, media_dir: str, zip_path: str) -> None:
    """
    HTML و رسانه‌ها رو توی یک ZIP رمزدار AES-256 قرار می‌ده.
    """
    with pyzipper.AESZipFile(
        zip_path, "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(ZIP_PASS.encode())
        zf.writestr("index.html", html_content.encode("utf-8"))
        # رسانه‌ها رو اگه وجود دارن اضافه کن
        if os.path.isdir(media_dir):
            for fname in os.listdir(media_dir):
                fpath = os.path.join(media_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f"media/{fname}")


# ═══════════════════════════════════════════════════════════════════════════
#  پردازش کامل کانال
# ═══════════════════════════════════════════════════════════════════════════

async def process_channel(channel: dict, status_callback) -> str:
    """
    کل فرآیند رو انجام می‌ده:
    1. دانلود پیام‌ها
    2. ساخت HTML
    3. ساخت ZIP رمزدار (با کاهش خودکار حجم)
    4. تغییر پسوند zip → jpg
    5. آپلود به imgurl.ir
    6. رمزگذاری متغیر URL
    7. ارسال پیامک
    برمی‌گردونه متن رمزگذاری‌شده
    """
    work_dir = tempfile.mkdtemp(prefix="tgexport_")
    media_dir = os.path.join(work_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    try:
        channel_id = channel["id"]
        title = channel["title"]

        await status_callback(f"📥 دریافت بیو کانال {title}...")
        bio = await get_channel_bio(channel_id)

        await status_callback("🖼️ دانلود عکس پروفایل...")
        avatar_path = await download_channel_photo(channel_id, work_dir)

        msg_count = MESSAGE_COUNT
        zip_path = os.path.join(work_dir, "export.zip")
        jpg_path = os.path.join(work_dir, "export.jpg")

        while msg_count >= 5:
            await status_callback(f"📨 دریافت {msg_count} پیام اخیر...")
            messages = await download_media_for_messages(channel_id, msg_count, media_dir)

            await status_callback("🔨 ساخت فایل HTML...")
            html_content = generate_html(
                channel_name=title,
                channel_bio=bio,
                channel_avatar_path=avatar_path,
                messages=messages,
                msg_count=len(messages),
            )

            await status_callback("🗜️ ساخت ZIP رمزدار...")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            create_protected_zip(html_content, media_dir, zip_path)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            if size_mb <= MAX_ZIP_MB:
                await status_callback(f"✅ حجم ZIP: {size_mb:.1f} MB (مجاز)")
                break
            else:
                await status_callback(
                    f"⚠️ حجم {size_mb:.1f} MB بیشتر از حد مجاز است. کاهش به {msg_count - 2} پیام..."
                )
                msg_count -= 2
                # پاک کردن رسانه‌های دانلود شده برای دوباره دانلود
                shutil.rmtree(media_dir)
                os.makedirs(media_dir, exist_ok=True)
        else:
            raise RuntimeError("حتی با ۵ پیام هم حجم ZIP از ۵۰ مگابایت بیشتر است.")

        # تغییر پسوند zip → jpg (حذف zip، ذخیره به عنوان jpg)
        await status_callback("🔄 تغییر پسوند zip به jpg...")
        with open(zip_path, "rb") as f_in, open(jpg_path, "wb") as f_out:
            f_out.write(f_in.read())
        os.remove(zip_path)

        # آپلود به imgurl.ir
        await status_callback("☁️ آپلود فایل به imgurl.ir...")
        cdn_url = upload_file(jpg_path)
        await status_callback(f"✅ آپلود موفق: {cdn_url}")

        # استخراج متغیر و رمزگذاری
        await status_callback("🔐 رمزگذاری لینک...")
        variable = extract_variable(cdn_url)
        encrypted = encrypt(variable, CRYPT_PASS)

        # ارسال پیامک
        await status_callback("📱 ارسال پیامک...")
        target = os.environ.get("TARGET_PHONE", "---")
        status_code, result = send_sms(encrypted)
        if status_code == 200:
            await status_callback(f"✅ پیامک به {target} ارسال شد.")
        else:
            await status_callback(f"⚠️ خطا در ارسال پیامک: {result}")

        return encrypted

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
#  ربات تلگرام (management bot)
# ═══════════════════════════════════════════════════════════════════════════

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id != ADMIN_ID:
            return
        return await func(update, context)
    return wrapper


def _channel_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"nav:{index - 1}"))
    if index < total - 1:
        nav.append(InlineKeyboardButton("▶️ بعدی", callback_data=f"nav:{index + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("✅ تایید (export)", callback_data=f"confirm:{index}")])
    return InlineKeyboardMarkup(buttons)


async def _send_channel_card(
    update: Update, context: ContextTypes.DEFAULT_TYPE, index: int, edit: bool = False
):
    channels: list = context.bot_data.get("channels", [])
    if not channels:
        await (update.callback_query.edit_message_text if edit else update.message.reply_text)(
            "⚠️ هیچ کانالی یافت نشد."
        )
        return

    ch = channels[index]
    title = ch["title"]
    bio = ch.get("about", "") or ""

    caption = f"📢 <b>{title}</b>"
    if bio:
        caption += f"\n\n📝 {bio}"
    caption += f"\n\n<i>{index + 1} از {len(channels)}</i>"

    keyboard = _channel_keyboard(index, len(channels))
    channel_id = ch["id"]

    # دانلود عکس پروفایل برای نمایش
    tmp = tempfile.mkdtemp()
    try:
        photo_path = await download_channel_photo(channel_id, tmp)
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as photo_file:
                if edit:
                    # ویرایش پیام با عکس جدید ممکن نیست به این شکل، پس پیام جدید میفرستیم
                    await update.callback_query.message.delete()
                    await context.bot.send_photo(
                        chat_id=ADMIN_ID,
                        photo=photo_file,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                else:
                    await context.bot.send_photo(
                        chat_id=ADMIN_ID,
                        photo=photo_file,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
        else:
            text = f"🖼️ عکس پروفایل موجود نیست.\n\n{caption}"
            if edit:
                await update.callback_query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
            else:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ در حال دریافت لیست کانال‌ها...")
    try:
        channels = await get_channels()
        # بیو رو برای اولین کانال بگیریم
        if channels:
            channels[0]["about"] = await get_channel_bio(channels[0]["id"])
        context.bot_data["channels"] = channels
        if not channels:
            await update.message.reply_text("⚠️ هیچ کانالی در اکانت پیدا نشد.")
            return
        await update.message.reply_text(f"✅ {len(channels)} کانال پیدا شد.")
        await _send_channel_card(update, context, 0, edit=False)
    except Exception as e:
        logger.exception("start error")
        await update.message.reply_text(f"❌ خطا: {e}")


@admin_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("nav:"):
        new_index = int(data.split(":")[1])
        channels: list = context.bot_data.get("channels", [])
        # بیو رو lazy بارگذاری کن
        if channels and not channels[new_index].get("about"):
            channels[new_index]["about"] = await get_channel_bio(channels[new_index]["id"])
        await _send_channel_card(update, context, new_index, edit=True)

    elif data.startswith("confirm:"):
        index = int(data.split(":")[1])
        channels: list = context.bot_data.get("channels", [])
        if not channels or index >= len(channels):
            await query.edit_message_caption(caption="⚠️ کانال پیدا نشد.")
            return
        channel = channels[index]
        await query.edit_message_caption(
            caption=f"⏳ شروع پردازش کانال: <b>{channel['title']}</b>\nلطفاً صبر کنید...",
            parse_mode="HTML",
        )

        status_msg = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="📊 <b>وضعیت پردازش:</b>",
            parse_mode="HTML",
        )

        log_lines = []

        async def status_callback(text: str):
            log_lines.append(text)
            combined = "📊 <b>وضعیت پردازش:</b>\n\n" + "\n".join(log_lines[-20:])
            try:
                await status_msg.edit_text(combined, parse_mode="HTML")
            except Exception:
                pass

        try:
            encrypted = await process_channel(channel, status_callback)
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "✅ <b>پردازش با موفقیت انجام شد</b>\n\n"
                    f"📱 پیامک ارسال شد\n"
                    f"🔐 متن رمزگذاری‌شده:\n<code>{encrypted}</code>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.exception("process_channel error")
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"❌ <b>خطا در پردازش:</b>\n<code>{e}</code>",
                parse_mode="HTML",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  main — راه‌اندازی همزمان userbot و management bot
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    global userbot

    # ── راه‌اندازی Telethon ──────────────────────────────────────────────
    logger.info("Connecting Telethon userbot...")
    userbot = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await userbot.start()
    me = await userbot.get_me()
    logger.info("Userbot connected as: %s (%s)", me.username, me.phone)

    # ── راه‌اندازی Management Bot ────────────────────────────────────────
    logger.info("Starting management bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(handle_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query"])

    logger.info("Bot is running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()  # منتظر بمون تا signal بیاد
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await userbot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
