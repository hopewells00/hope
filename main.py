"""
ربات مدیریت کانال‌های بله (Bale Messenger)
- اتصال ربات به پیام‌رسان بله
- پنل مدیریت از طریق ربات بله
- دریافت پیام‌های کانال از طریق API بله
- export پیام‌های کانال به HTML + ZIP رمزدار
- آپلود به imgurl.ir و ارسال لینک رمزگذاری‌شده از طریق پیامک
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import pyzipper
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ChatMemberHandler,
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
BOT_TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_ID      = int(os.environ["ADMIN_ID"])
ZIP_PASS      = os.environ["ZIP_PASS"]
CRYPT_PASS    = os.environ["CRYPT_PASS"]
MESSAGE_COUNT = int(os.environ.get("MESSAGE_COUNT", "30"))
MAX_ZIP_MB    = int(os.environ.get("MAX_ZIP_MB", "50"))

# ── آدرس پایه API بله ────────────────────────────────────────────────────────
BALE_BASE_URL      = "https://tapi.bale.ai/bot"
BALE_BASE_FILE_URL = "https://tapi.bale.ai/file/bot"

# ── فایل ذخیره کانال‌ها ──────────────────────────────────────────────────────
CHANNELS_FILE = "channels.json"


# ═══════════════════════════════════════════════════════════════════════════
#  کمک‌کننده‌های API بله
# ═══════════════════════════════════════════════════════════════════════════

def bale_request(method: str, data: dict = None, timeout: int = 30) -> dict:
    """یک درخواست به API بله ارسال می‌کند."""
    url = f"{BALE_BASE_URL}{BOT_TOKEN}/{method}"
    try:
        if data:
            resp = requests.post(url, json=data, timeout=timeout)
        else:
            resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("Bale API error [%s]: %s", method, e)
        return {"ok": False, "error": str(e)}


def bale_get_chat(chat_id) -> Optional[dict]:
    """اطلاعات چت/کانال را دریافت می‌کند."""
    result = bale_request("getChat", {"chat_id": chat_id})
    if result.get("ok"):
        return result.get("result")
    return None


def bale_get_chat_history(chat_id, limit: int = 30, from_message_id: int = 0) -> list:
    """
    پیام‌های اخیر کانال را از API بله دریافت می‌کند.
    ربات باید ادمین کانال باشد.
    """
    data = {
        "chat_id": chat_id,
        "limit": limit,
    }
    if from_message_id:
        data["from_message_id"] = from_message_id

    result = bale_request("getChatHistory", data, timeout=60)
    if result.get("ok"):
        return result.get("result", {}).get("messages", []) or []
    logger.warning("getChatHistory failed: %s", result)
    return []


def bale_get_file(file_id: str) -> Optional[dict]:
    """اطلاعات فایل را دریافت می‌کند."""
    result = bale_request("getFile", {"file_id": file_id})
    if result.get("ok"):
        return result.get("result")
    return None


def bale_download_file(file_id: str, dest_path: str) -> bool:
    """فایل را از بله دانلود می‌کند."""
    file_info = bale_get_file(file_id)
    if not file_info:
        return False
    file_path = file_info.get("file_path", "")
    if not file_path:
        return False
    url = f"{BALE_BASE_FILE_URL}{BOT_TOKEN}/{file_path}"
    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.warning("File download error [%s]: %s", file_id, e)
        return False


def bale_get_chat_photo(chat_id, dest_dir: str) -> Optional[str]:
    """عکس پروفایل کانال را دانلود می‌کند."""
    chat = bale_get_chat(chat_id)
    if not chat:
        return None
    photo = chat.get("photo")
    if not photo:
        return None
    file_id = photo.get("big_file_id") or photo.get("small_file_id")
    if not file_id:
        return None
    dest = os.path.join(dest_dir, "avatar.jpg")
    if bale_download_file(file_id, dest):
        return dest
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  مدیریت کانال‌ها
# ═══════════════════════════════════════════════════════════════════════════

def load_channels() -> list[dict]:
    """کانال‌های ذخیره‌شده را بارگذاری می‌کند."""
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_channels(channels: list[dict]):
    """کانال‌ها را در فایل ذخیره می‌کند."""
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


def add_or_update_channel(channel: dict):
    """کانال را اضافه یا آپدیت می‌کند."""
    channels = load_channels()
    for i, ch in enumerate(channels):
        if str(ch["id"]) == str(channel["id"]):
            channels[i] = channel
            save_channels(channels)
            return
    channels.append(channel)
    save_channels(channels)


def remove_channel(channel_id):
    """کانال را از لیست حذف می‌کند."""
    channels = load_channels()
    channels = [ch for ch in channels if str(ch["id"]) != str(channel_id)]
    save_channels(channels)


# ═══════════════════════════════════════════════════════════════════════════
#  پردازش پیام‌های بله
# ═══════════════════════════════════════════════════════════════════════════

def _extract_message_media(msg: dict) -> tuple[Optional[str], str, str]:
    """
    نوع و file_id رسانه پیام را استخراج می‌کند.
    returns: (file_id, ext, media_type)
    """
    if "photo" in msg:
        # آرایه عکس‌ها - بزرگ‌ترین را انتخاب می‌کنیم
        photos = msg["photo"]
        if photos:
            best = max(photos, key=lambda p: p.get("file_size", 0))
            return best.get("file_id"), ".jpg", "image"
    if "video" in msg:
        v = msg["video"]
        return v.get("file_id"), ".mp4", "video"
    if "animation" in msg:
        a = msg["animation"]
        mime = a.get("mime_type", "video/mp4")
        ext = ".mp4" if "mp4" in mime else ".gif"
        return a.get("file_id"), ext, "animation"
    if "audio" in msg:
        a = msg["audio"]
        mime = a.get("mime_type", "audio/mpeg")
        ext = ".ogg" if "ogg" in mime else ".mp3"
        return a.get("file_id"), ext, "audio"
    if "voice" in msg:
        return msg["voice"].get("file_id"), ".ogg", "voice"
    if "video_note" in msg:
        return msg["video_note"].get("file_id"), ".mp4", "video_note"
    if "sticker" in msg:
        s = msg["sticker"]
        ext = ".webp"
        if s.get("is_animated"):
            ext = ".tgs"
        elif s.get("is_video"):
            ext = ".webm"
        return s.get("file_id"), ext, "sticker"
    if "document" in msg:
        doc = msg["document"]
        fname = doc.get("file_name", "")
        ext = os.path.splitext(fname)[1] if fname else ".bin"
        return doc.get("file_id"), ext, "document"
    return None, "", ""


def _parse_bale_message(msg: dict) -> dict:
    """پیام بله را به فرمت داخلی تبدیل می‌کند."""
    from datetime import datetime, timezone

    entry = {
        "text": msg.get("text") or msg.get("caption") or "",
        "date": datetime.fromtimestamp(msg.get("date", 0), tz=timezone.utc),
        "views": msg.get("views", 0) or 0,
        "reactions": [],
        "fwd_from": None,
        "media_path": None,
        "media_type": "",
        "media_name": "",
        "media_size": 0,
        "sender": "",
        "file_id": None,
        "file_ext": "",
    }

    # Forwarded
    if "forward_from_chat" in msg:
        fwd_chat = msg["forward_from_chat"]
        entry["fwd_from"] = fwd_chat.get("title") or fwd_chat.get("username", "")
    elif "forward_from" in msg:
        fwd = msg["forward_from"]
        name = " ".join(filter(None, [fwd.get("first_name"), fwd.get("last_name")]))
        entry["fwd_from"] = name or fwd.get("username", "")

    # Sender
    if "from" in msg:
        fr = msg["from"]
        name = " ".join(filter(None, [fr.get("first_name"), fr.get("last_name")]))
        entry["sender"] = name or fr.get("username", "")

    # Media
    file_id, ext, media_type = _extract_message_media(msg)
    if file_id:
        entry["file_id"] = file_id
        entry["file_ext"] = ext
        entry["media_type"] = media_type

        # نام فایل برای اسناد
        if media_type == "document" and "document" in msg:
            doc = msg["document"]
            entry["media_name"] = doc.get("file_name", f"file{ext}")
            entry["media_size"] = doc.get("file_size", 0)
        elif media_type == "audio" and "audio" in msg:
            a = msg["audio"]
            entry["media_name"] = a.get("file_name") or a.get("title") or f"audio{ext}"
            entry["media_size"] = a.get("file_size", 0)

    return entry


async def download_messages_with_media(channel_id, limit: int, dest_dir: str) -> list[dict]:
    """
    پیام‌های کانال بله را دریافت و رسانه‌ها را دانلود می‌کند.
    """
    raw_messages = bale_get_chat_history(channel_id, limit=limit)
    if not raw_messages:
        logger.warning("No messages received from Bale API for channel %s", channel_id)
        return []

    messages = []
    for idx, msg in enumerate(raw_messages):
        entry = _parse_bale_message(msg)

        # دانلود رسانه
        if entry["file_id"]:
            ext = entry["file_ext"]
            media_path = os.path.join(dest_dir, f"media_{idx}{ext}")
            try:
                success = bale_download_file(entry["file_id"], media_path)
                if success:
                    entry["media_path"] = media_path
                    if not entry["media_name"]:
                        entry["media_name"] = os.path.basename(media_path)
            except Exception as e:
                logger.warning("Media download error msg %d: %s", idx, e)

        messages.append(entry)

    return messages  # ترتیب قدیمی به جدید (API معمولاً از جدید به قدیم برمی‌گردونه)


# ═══════════════════════════════════════════════════════════════════════════
#  ساخت ZIP رمزدار
# ═══════════════════════════════════════════════════════════════════════════

def create_protected_zip(html_content: str, media_dir: str, zip_path: str) -> None:
    """
    HTML و رسانه‌ها رو توی یک ZIP رمزدار AES-256 قرار می‌ده.
    رسانه‌ها با پسوند اصلی در پوشه media/ ذخیره می‌شوند.
    """
    with pyzipper.AESZipFile(
        zip_path, "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(ZIP_PASS.encode())
        zf.writestr("index.html", html_content.encode("utf-8"))
        # رسانه‌ها رو اضافه کن
        if os.path.isdir(media_dir):
            for fname in sorted(os.listdir(media_dir)):
                fpath = os.path.join(media_dir, fname)
                if os.path.isfile(fpath) and fname != "avatar.jpg":
                    zf.write(fpath, arcname=f"media/{fname}")


# ═══════════════════════════════════════════════════════════════════════════
#  پردازش کامل کانال
# ═══════════════════════════════════════════════════════════════════════════

async def process_channel(channel: dict, status_callback) -> str:
    """
    کل فرآیند رو انجام می‌ده:
    1. دانلود پیام‌ها و رسانه‌ها از بله
    2. ساخت HTML
    3. ساخت ZIP رمزدار (با کاهش خودکار حجم)
    4. تغییر پسوند zip → jpg
    5. آپلود به imgurl.ir
    6. رمزگذاری متغیر URL
    7. ارسال پیامک
    """
    work_dir = tempfile.mkdtemp(prefix="baleexport_")
    media_dir = os.path.join(work_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    try:
        channel_id = channel["id"]
        title = channel["title"]

        await status_callback(f"🖼️ دانلود عکس پروفایل کانال {title}...")
        avatar_path = bale_get_chat_photo(channel_id, work_dir)
        bio = channel.get("about", "")

        msg_count = MESSAGE_COUNT
        zip_path = os.path.join(work_dir, "export.zip")
        jpg_path = os.path.join(work_dir, "export.jpg")

        while msg_count >= 5:
            await status_callback(f"📨 دریافت {msg_count} پیام اخیر از بله...")
            messages = await download_messages_with_media(channel_id, msg_count, media_dir)

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
                shutil.rmtree(media_dir)
                os.makedirs(media_dir, exist_ok=True)
        else:
            raise RuntimeError("حتی با ۵ پیام هم حجم ZIP از حد مجاز بیشتر است.")

        # تغییر پسوند zip → jpg
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
#  ربات بله (management bot)
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
    buttons.append([InlineKeyboardButton("🗑️ حذف کانال", callback_data=f"delete:{index}")])
    return InlineKeyboardMarkup(buttons)


async def _send_channel_card(
    update: Update, context: ContextTypes.DEFAULT_TYPE, index: int, edit: bool = False
):
    channels: list = context.bot_data.get("channels", [])
    if not channels:
        msg = "⚠️ هیچ کانالی ثبت نشده است.\n\nربات را به کانال مورد نظر به عنوان ادمین اضافه کنید."
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return

    ch = channels[index]
    title = ch.get("title", "بدون نام")
    bio = ch.get("about", "") or ""
    username = ch.get("username", "")
    ch_id = ch["id"]

    caption = f"📢 <b>{title}</b>"
    if username:
        caption += f"\n🔗 @{username}"
    if bio:
        caption += f"\n\n📝 {bio}"
    caption += f"\n\n🆔 <code>{ch_id}</code>"
    caption += f"\n\n<i>{index + 1} از {len(channels)}</i>"

    keyboard = _channel_keyboard(index, len(channels))

    # دانلود عکس پروفایل برای نمایش
    tmp = tempfile.mkdtemp()
    try:
        photo_path = bale_get_chat_photo(ch_id, tmp)
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as photo_file:
                if edit and update.callback_query:
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
            if edit and update.callback_query:
                await update.callback_query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
            elif update.message:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 <b>ربات مدیریت کانال بله</b>\n\n"
        "📋 <b>دستورات:</b>\n"
        "/channels — مشاهده کانال‌های ثبت‌شده\n"
        "/addchannel — افزودن کانال با شناسه\n\n"
        "💡 برای افزودن خودکار، ربات را ادمین کانال کنید."
    )
    await update.message.reply_text(text, parse_mode="HTML")


@admin_only
async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست کانال‌های ثبت‌شده."""
    channels = load_channels()
    context.bot_data["channels"] = channels
    if not channels:
        await update.message.reply_text(
            "⚠️ هیچ کانالی ثبت نشده است.\n\n"
            "ربات را به کانال مورد نظر به عنوان ادمین اضافه کنید\n"
            "یا با /addchannel شناسه کانال را وارد کنید."
        )
        return
    await update.message.reply_text(f"✅ {len(channels)} کانال ثبت‌شده یافت شد.")
    await _send_channel_card(update, context, 0, edit=False)


@admin_only
async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """افزودن کانال با شناسه دستی."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "📌 شناسه کانال را وارد کنید:\n"
            "مثال: <code>/addchannel -1001234567890</code>\n"
            "یا: <code>/addchannel @channelusername</code>",
            parse_mode="HTML"
        )
        return

    channel_id = args[0].strip()
    await update.message.reply_text(f"⏳ در حال دریافت اطلاعات کانال {channel_id}...")

    chat = bale_get_chat(channel_id)
    if not chat:
        await update.message.reply_text("❌ کانال یافت نشد یا ربات دسترسی ندارد.")
        return

    channel_data = {
        "id": chat.get("id"),
        "title": chat.get("title", ""),
        "username": chat.get("username", ""),
        "about": chat.get("description", "") or chat.get("bio", ""),
    }
    add_or_update_channel(channel_data)

    channels = load_channels()
    context.bot_data["channels"] = channels

    await update.message.reply_text(
        f"✅ کانال <b>{channel_data['title']}</b> اضافه شد.",
        parse_mode="HTML"
    )


@admin_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("nav:"):
        new_index = int(data.split(":")[1])
        channels = load_channels()
        context.bot_data["channels"] = channels
        await _send_channel_card(update, context, new_index, edit=True)

    elif data.startswith("delete:"):
        index = int(data.split(":")[1])
        channels = load_channels()
        if not channels or index >= len(channels):
            await query.edit_message_text("⚠️ کانال پیدا نشد.")
            return
        ch = channels[index]
        remove_channel(ch["id"])
        await query.edit_message_text(f"🗑️ کانال <b>{ch['title']}</b> حذف شد.", parse_mode="HTML")

    elif data.startswith("confirm:"):
        index = int(data.split(":")[1])
        channels = load_channels()
        context.bot_data["channels"] = channels
        if not channels or index >= len(channels):
            await query.edit_message_caption(caption="⚠️ کانال پیدا نشد.")
            return
        channel = channels[index]

        try:
            await query.edit_message_caption(
                caption=f"⏳ شروع پردازش کانال: <b>{channel['title']}</b>\nلطفاً صبر کنید...",
                parse_mode="HTML",
            )
        except Exception:
            pass

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


async def handle_new_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    وقتی ربات به کانالی اضافه می‌شود، کانال را ثبت می‌کند.
    """
    result = update.my_chat_member
    if not result:
        return

    chat = result.chat
    new_status = result.new_chat_member.status if result.new_chat_member else None

    # بررسی اینکه ربات ادمین شده
    if new_status in ("administrator", "member") and chat.type in ("channel", "supergroup"):
        channel_data = {
            "id": chat.id,
            "title": chat.title or "",
            "username": chat.username or "",
            "about": "",
        }
        add_or_update_channel(channel_data)
        logger.info("Channel registered: %s (%s)", chat.title, chat.id)

        # اطلاع به ادمین
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📢 کانال جدید ثبت شد:\n<b>{chat.title}</b>\n🆔 <code>{chat.id}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # اگر ربات حذف شد، کانال را حذف کن
    elif new_status in ("kicked", "left") and chat.type in ("channel", "supergroup"):
        remove_channel(chat.id)
        logger.info("Channel removed: %s (%s)", chat.title, chat.id)


# ═══════════════════════════════════════════════════════════════════════════
#  main — راه‌اندازی ربات بله
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    logger.info("Starting Bale management bot...")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url(BALE_BASE_URL)
        .base_file_url(BALE_BASE_FILE_URL)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("channels", cmd_channels))
    app.add_handler(CommandHandler("addchannel", cmd_add_channel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(ChatMemberHandler(handle_new_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query", "my_chat_member"]
    )

    logger.info("Bale bot is running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
