"""
ربات مدیریت کانال‌ها از طریق پیام‌رسان بله
"""

import asyncio
import logging
import os
import shutil
import tempfile
import time
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
    MessageHandler,
    filters,
)

from crypto_utils import encrypt
from html_generator import generate_html
from sms_sender import send_sms
from uploader import extract_variable, upload_file

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.environ["BOT_TOKEN"]
ADMIN_ID       = int(os.environ["ADMIN_ID"])
API_ID         = int(os.environ["API_ID"])
API_HASH       = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
ZIP_PASS       = os.environ["ZIP_PASS"]
CRYPT_PASS     = os.environ["CRYPT_PASS"]

BALE_BASE_URL      = "https://tapi.bale.ai/bot"
BALE_BASE_FILE_URL = "https://tapi.bale.ai/file/bot"

SPLIT_SIZE_BYTES  = 10 * 1024 * 1024   # 10 MB
AUTO_DELETE_HOURS = 4

userbot: Optional[TelegramClient] = None


# ── تمیزکاری فایل‌های قدیمی هنگام اجرا ──────────────────────────────────────
def cleanup_old_temp_files():
    try:
        tmp = tempfile.gettempdir()
        now = time.time()
        for name in os.listdir(tmp):
            if name.startswith("tgexport_"):
                path = os.path.join(tmp, name)
                try:
                    age = now - os.path.getmtime(path)
                    if age > AUTO_DELETE_HOURS * 3600:
                        shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass
    except Exception as e:
        logger.warning("cleanup error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
#  کمک‌کننده‌های Telethon
# ═══════════════════════════════════════════════════════════════════════════

async def get_channels() -> list[dict]:
    channels = []
    async for dialog in userbot.iter_dialogs():
        if isinstance(dialog.entity, Channel) and dialog.entity.broadcast:
            entity = dialog.entity
            channels.append({
                "id": entity.id,
                "username": getattr(entity, "username", None) or "",
                "title": entity.title or "",
            })
    return channels


async def download_channel_photo(channel_id: int, dest_dir: str) -> Optional[str]:
    try:
        entity = await userbot.get_entity(PeerChannel(channel_id))
        path = await userbot.download_profile_photo(
            entity, file=os.path.join(dest_dir, "avatar.jpg")
        )
        return path
    except Exception as e:
        logger.warning("avatar download error: %s", e)
        return None


async def _download_media_once(channel_id: int, limit: int, dest_dir: str) -> list[dict]:
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
            "media_type": "",
            "media_name": "",
            "media_size": 0,
            "sender": "",
        }

        if msg.forward:
            try:
                fwd_title = None
                if hasattr(msg.forward, "channel_id") and msg.forward.channel_id:
                    fwd_entity = await userbot.get_entity(
                        PeerChannel(msg.forward.channel_id)
                    )
                    fwd_title = getattr(fwd_entity, "title", None)
                entry["fwd_from"] = fwd_title or ""
            except Exception:
                pass

        if hasattr(msg, "reactions") and msg.reactions:
            for r in (msg.reactions.results or []):
                emoji = getattr(r.reaction, "emoticon", "?")
                entry["reactions"].append({"emoji": emoji, "count": r.count})

        if msg.media:
            try:
                ext = ""
                media_type = ""
                media_name = ""
                media_size = 0

                if isinstance(msg.media, MessageMediaPhoto):
                    ext = ".jpg"
                    media_type = "image"
                elif isinstance(msg.media, MessageMediaDocument):
                    doc = msg.media.document
                    mime = getattr(doc, "mime_type", "") or ""
                    media_size = getattr(doc, "size", 0) or 0

                    for attr in getattr(doc, "attributes", []):
                        from telethon.tl.types import DocumentAttributeFilename
                        if isinstance(attr, DocumentAttributeFilename):
                            media_name = attr.file_name
                            break

                    if "video" in mime:
                        ext = ".mp4"
                        media_type = "video"
                    elif "audio" in mime or "ogg" in mime:
                        ext = ".ogg"
                        media_type = "audio"
                    elif "image" in mime:
                        ext = ".jpg"
                        media_type = "image"
                    else:
                        # هر نوع فایل — پسوند از نام فایل یا .bin
                        if media_name:
                            raw_ext = os.path.splitext(media_name)[1]
                            ext = raw_ext if raw_ext else ".bin"
                        else:
                            ext = ".bin"
                        media_type = "document"

                if ext:
                    media_path = os.path.join(dest_dir, f"media_{idx}{ext}")
                    await userbot.download_media(msg, file=media_path)
                    if os.path.exists(media_path):
                        entry["media_path"] = media_path
                        entry["media_type"] = media_type
                        entry["media_name"] = media_name or os.path.basename(media_path)
                        entry["media_size"] = media_size or os.path.getsize(media_path)

            except Exception as e:
                logger.warning("media download error msg %d: %s", idx, e)

        messages.append(entry)
        idx += 1

    return list(reversed(messages))


async def download_media_for_messages(channel_id: int, limit: int, dest_dir: str) -> list[dict]:
    """با ۳ بار تلاش مجدد"""
    for attempt in range(1, 4):
        try:
            return await _download_media_once(channel_id, limit, dest_dir)
        except Exception as e:
            if attempt == 3:
                raise
            logger.warning("download attempt %d failed: %s", attempt, e)
            await asyncio.sleep(2 * attempt)
    return []


# ═══════════════════════════════════════════════════════════════════════════
#  ساخت ZIP رمزدار
# ═══════════════════════════════════════════════════════════════════════════

def create_protected_zip(html_content: str, media_dir: str, zip_path: str) -> None:
    with pyzipper.AESZipFile(
        zip_path, "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(ZIP_PASS.encode())
        zf.writestr("index.html", html_content.encode("utf-8"))
        if os.path.isdir(media_dir):
            for fname in sorted(os.listdir(media_dir)):
                fpath = os.path.join(media_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f"media/{fname}")


def split_file(file_path: str, part_size: int = SPLIT_SIZE_BYTES) -> list[str]:
    parts = []
    with open(file_path, "rb") as f:
        idx = 1
        while True:
            chunk = f.read(part_size)
            if not chunk:
                break
            part_path = f"{file_path}.part{idx}"
            with open(part_path, "wb") as pf:
                pf.write(chunk)
            parts.append(part_path)
            idx += 1
    return parts


async def send_zip_parts_to_bale(context, zip_path: str):
    parts = split_file(zip_path)
    total = len(parts)
    for i, part_path in enumerate(parts, 1):
        try:
            with open(part_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=f,
                    filename=f"export_part{i}of{total}.zip",
                )
        except Exception as e:
            logger.error("send part %d error: %s", i, e)
        finally:
            try:
                os.remove(part_path)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
#  آپلود با تلاش مجدد
# ═══════════════════════════════════════════════════════════════════════════

def upload_with_retry(file_path: str, max_attempts: int = 3) -> str:
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return upload_file(file_path)
        except Exception as e:
            last_err = e
            logger.warning("upload attempt %d failed: %s", attempt, e)
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    raise last_err


# ═══════════════════════════════════════════════════════════════════════════
#  پردازش کامل کانال
# ═══════════════════════════════════════════════════════════════════════════

TOTAL_STEPS = 7


async def process_channel(
    channel: dict,
    progress_msg,
    context,
    msg_count_override: Optional[int] = None,
    max_zip_mb_override: Optional[int] = None,
) -> str:
    work_dir = tempfile.mkdtemp(prefix="tgexport_")
    media_dir = os.path.join(work_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    # حذف خودکار بعد از ۴ ساعت
    asyncio.get_event_loop().call_later(
        AUTO_DELETE_HOURS * 3600,
        lambda: shutil.rmtree(work_dir, ignore_errors=True)
    )

    msg_count = msg_count_override if msg_count_override is not None else context.bot_data.get("msg_count", 30)
    max_zip_mb = max_zip_mb_override if max_zip_mb_override is not None else context.bot_data.get("max_zip_mb", 50)

    async def set_progress(step: int, total: int = TOTAL_STEPS):
        pct = int(step / total * 100)
        bar_len = 20
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        try:
            await progress_msg.edit_text(f"[{bar}] {pct}%")
        except Exception:
            pass

    try:
        channel_id = channel["id"]
        title = channel["title"]

        await set_progress(1)

        # دانلود عکس پروفایل با ۳ تلاش
        avatar_path = None
        for attempt in range(1, 4):
            try:
                avatar_path = await download_channel_photo(channel_id, work_dir)
                break
            except Exception as e:
                if attempt == 3:
                    logger.warning("avatar failed: %s", e)
                else:
                    await asyncio.sleep(2)

        await set_progress(2)

        zip_path = os.path.join(work_dir, "export.zip")

        # ── حلقه کاهش حجم ──────────────────────────────────────────────
        # شروع از تعداد درخواستی، کاهش یک‌به‌یک تا پیدا شدن حجم مجاز
        current_count = msg_count
        while current_count >= 1:
            messages = await download_media_for_messages(channel_id, current_count, media_dir)

            await set_progress(3)

            html_content = generate_html(
                channel_name=title,
                channel_avatar_path=avatar_path,
                messages=messages,
                msg_count=len(messages),
            )

            await set_progress(4)

            if os.path.exists(zip_path):
                os.remove(zip_path)
            create_protected_zip(html_content, media_dir, zip_path)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            if size_mb <= max_zip_mb:
                break
            else:
                current_count -= 1
                shutil.rmtree(media_dir)
                os.makedirs(media_dir, exist_ok=True)
        else:
            # حتی با ۱ پیام هم از حد بیشتر است — ادامه با همان یک پیام
            pass

        await set_progress(5)

        # تغییر پسوند و آپلود
        jpg_path = os.path.join(work_dir, "export.jpg")
        with open(zip_path, "rb") as f_in, open(jpg_path, "wb") as f_out:
            f_out.write(f_in.read())

        cdn_url = None
        upload_failed = False
        try:
            cdn_url = upload_with_retry(jpg_path)
        except Exception as e:
            logger.error("upload failed after retries: %s", e)
            upload_failed = True

        await set_progress(6)

        if upload_failed:
            # ذخیره مسیر zip برای ارسال مستقیم
            context.bot_data["pending_zip"] = zip_path
            context.bot_data["pending_zip_dir"] = work_dir
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅", callback_data="send_zip:yes"),
                    InlineKeyboardButton("❌", callback_data="send_zip:no"),
                ]
            ])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text="ارسال مستقیم؟",
                reply_markup=keyboard,
            )
            return "__UPLOAD_FAILED__"

        encrypted = encrypt(extract_variable(cdn_url), CRYPT_PASS)

        await set_progress(7)

        # پیامک
        try:
            send_sms(encrypted)
        except Exception as e:
            logger.warning("SMS failed: %s", e)

        shutil.rmtree(work_dir, ignore_errors=True)
        return encrypted

    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


# ═══════════════════════════════════════════════════════════════════════════
#  ردیابی و حذف خودکار پیام‌ها
# ═══════════════════════════════════════════════════════════════════════════

def _track_message(context, msg_id: int):
    tracked = context.bot_data.setdefault("tracked_msgs", [])
    tracked.append(msg_id)


async def auto_delete_messages(context: ContextTypes.DEFAULT_TYPE):
    tracked = context.bot_data.pop("tracked_msgs", [])
    for msg_id in tracked:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
#  دستور /w — واچ خودکار ۲۴ ساعته
# ═══════════════════════════════════════════════════════════════════════════

async def auto_watch_job(context: ContextTypes.DEFAULT_TYPE):
    if not context.bot_data.get("watch_mode"):
        return

    try:
        channels = await get_channels()
    except Exception as e:
        logger.error("auto_watch get_channels error: %s", e)
        return

    for channel in channels:
        work_dir = tempfile.mkdtemp(prefix="tgexport_")
        media_dir = os.path.join(work_dir, "media")
        os.makedirs(media_dir, exist_ok=True)
        try:
            avatar_path = await download_channel_photo(channel["id"], work_dir)
            messages = await download_media_for_messages(channel["id"], 5, media_dir)
            html_content = generate_html(
                channel_name=channel["title"],
                channel_avatar_path=avatar_path,
                messages=messages,
                msg_count=len(messages),
            )
            zip_path = os.path.join(work_dir, "export.zip")
            create_protected_zip(html_content, media_dir, zip_path)

            jpg_path = os.path.join(work_dir, "export.jpg")
            with open(zip_path, "rb") as f_in, open(jpg_path, "wb") as f_out:
                f_out.write(f_in.read())

            try:
                cdn_url = upload_with_retry(jpg_path)
                encrypted = encrypt(extract_variable(cdn_url), CRYPT_PASS)
                try:
                    send_sms(encrypted)
                except Exception:
                    pass
                msg = await context.bot.send_message(chat_id=ADMIN_ID, text=encrypted)
                _track_message(context, msg.message_id)
            except Exception as e:
                logger.error("auto_watch upload error: %s", e)

        except Exception as e:
            logger.error("auto_watch channel %s error: %s", channel["title"], e)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
#  سیستم دسترسی کاربران
# ═══════════════════════════════════════════════════════════════════════════

def _get_allowed(context) -> set:
    allowed = context.bot_data.setdefault("allowed_users", set())
    allowed.add(ADMIN_ID)
    return allowed


def allowed_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in _get_allowed(context):
            return
        return await func(update, context)
    return wrapper


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id != ADMIN_ID:
            return
        return await func(update, context)
    return wrapper


# ═══════════════════════════════════════════════════════════════════════════
#  دستورات ربات بله
# ═══════════════════════════════════════════════════════════════════════════

def _channel_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    buttons = []
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"nav:{index - 1}"))
    if index < total - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"nav:{index + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("✅", callback_data=f"confirm:{index}")])
    return InlineKeyboardMarkup(buttons)


async def _send_channel_card(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    index: int,
    old_msg_id: Optional[int] = None,
) -> int:
    channels: list = context.bot_data.get("channels", [])
    if not channels:
        return 0

    if old_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception:
            pass

    ch = channels[index]
    title = ch["title"]
    username = ch.get("username", "")
    caption = f"<b>{title}</b>"
    if username:
        caption += f"\n@{username}"
    caption += f"\n\n<i>{index + 1} / {len(channels)}</i>"

    keyboard = _channel_keyboard(index, len(channels))
    tmp = tempfile.mkdtemp()
    try:
        photo_path = await download_channel_photo(ch["id"], tmp)
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as photo_file:
                sent = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_file,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
        else:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        return sent.message_id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _track_message(context, update.message.message_id)
    loading = await update.message.reply_text("⠿")
    _track_message(context, loading.message_id)
    try:
        channels = await get_channels()
        context.bot_data["channels"] = channels
        if not channels:
            await loading.edit_text("—")
            return
        await loading.delete()
        card_id = await _send_channel_card(context, ADMIN_ID, 0)
        context.bot_data["current_card_msg_id"] = card_id
        _track_message(context, card_id)
    except Exception as e:
        logger.exception("start error")
        await loading.edit_text(f"✕")


@admin_only
async def cmd_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _track_message(context, update.message.message_id)
    args = context.args
    if not args:
        m = await update.message.reply_text("—")
        _track_message(context, m.message_id)
        return
    try:
        uid = int(args[0])
        _get_allowed(context).add(uid)
        m = await update.message.reply_text("✓")
        _track_message(context, m.message_id)
    except ValueError:
        m = await update.message.reply_text("✕")
        _track_message(context, m.message_id)


@admin_only
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _track_message(context, update.message.message_id)
    context.bot_data["watch_mode"] = True
    m = await update.message.reply_text("✓")
    _track_message(context, m.message_id)


@admin_only
async def cmd_watch_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _track_message(context, update.message.message_id)
    context.bot_data["watch_mode"] = False
    m = await update.message.reply_text("✕")
    _track_message(context, m.message_id)


@admin_only
async def cmd_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظیم حداکثر حجم ZIP به مگابایت — /setlimit N"""
    _track_message(context, update.message.message_id)
    args = context.args
    if not args:
        m = await update.message.reply_text("—")
        _track_message(context, m.message_id)
        return
    try:
        val = int(args[0])
        if val < 1:
            raise ValueError
        context.bot_data["max_zip_mb"] = val
        m = await update.message.reply_text("✓")
        _track_message(context, m.message_id)
    except ValueError:
        m = await update.message.reply_text("✕")
        _track_message(context, m.message_id)


@admin_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _track_message(context, update.message.message_id)
    state = context.user_data.get("state")

    if state == "waiting_count":
        try:
            count = int(update.message.text.strip())
            if count < 1:
                raise ValueError
            context.user_data["state"] = None

            pending = context.user_data.get("pending_channel")
            if not pending:
                return

            progress_msg = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text="[░░░░░░░░░░░░░░░░░░░░] 0%"
            )
            _track_message(context, progress_msg.message_id)

            try:
                encrypted = await process_channel(
                    pending,
                    progress_msg,
                    context,
                    msg_count_override=count,
                )
                if encrypted != "__UPLOAD_FAILED__":
                    result_msg = await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"<code>{encrypted}</code>",
                        parse_mode="HTML",
                    )
                    _track_message(context, result_msg.message_id)
            except Exception as e:
                logger.exception("process error")
                err_msg = await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="✕",
                )
                _track_message(context, err_msg.message_id)

            context.user_data["pending_channel"] = None

        except ValueError:
            m = await update.message.reply_text("—")
            _track_message(context, m.message_id)


@allowed_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("nav:"):
        new_index = int(data.split(":")[1])
        old_id = query.message.message_id
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=old_id)
        except Exception:
            pass
        new_id = await _send_channel_card(context, ADMIN_ID, new_index)
        context.bot_data["current_card_msg_id"] = new_id
        _track_message(context, new_id)

    elif data.startswith("confirm:"):
        index = int(data.split(":")[1])
        channels: list = context.bot_data.get("channels", [])
        if not channels or index >= len(channels):
            return

        channel = channels[index]
        context.user_data["pending_channel"] = channel

        # حذف کامل کارت
        try:
            await context.bot.delete_message(
                chat_id=ADMIN_ID, message_id=query.message.message_id
            )
        except Exception:
            pass
        context.bot_data["current_card_msg_id"] = None

        # درخواست تعداد پیام
        context.user_data["state"] = "waiting_count"
        m = await context.bot.send_message(chat_id=ADMIN_ID, text="?")
        _track_message(context, m.message_id)

    elif data.startswith("send_zip:"):
        choice = data.split(":")[1]
        try:
            await context.bot.delete_message(
                chat_id=ADMIN_ID, message_id=query.message.message_id
            )
        except Exception:
            pass

        if choice == "yes":
            zip_path = context.bot_data.get("pending_zip")
            if zip_path and os.path.exists(zip_path):
                await send_zip_parts_to_bale(context, zip_path)
            work_dir = context.bot_data.pop("pending_zip_dir", None)
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)
        else:
            work_dir = context.bot_data.pop("pending_zip_dir", None)
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)

        context.bot_data.pop("pending_zip", None)


# ═══════════════════════════════════════════════════════════════════════════
#  راه‌اندازی
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    global userbot

    cleanup_old_temp_files()

    logger.info("Connecting userbot...")
    userbot = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await userbot.start()
    me = await userbot.get_me()
    logger.info("Userbot: %s (%s)", me.username, me.phone)

    logger.info("Starting Bale bot...")
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url(BALE_BASE_URL)
        .base_file_url(BALE_BASE_FILE_URL)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("Add", cmd_add_user))
    app.add_handler(CommandHandler("setlimit", cmd_set_limit))
    app.add_handler(CommandHandler("w", cmd_watch))
    app.add_handler(CommandHandler("woff", cmd_watch_off))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # حذف خودکار هر ۱ ساعت
    app.job_queue.run_repeating(auto_delete_messages, interval=3600, first=3600)
    # واچ خودکار هر ۲۴ ساعت
    app.job_queue.run_repeating(auto_watch_job, interval=86400, first=86400)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=["message", "callback_query"]
    )

    logger.info("Bot running.")
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await userbot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
