"""
ربات آرشیو کانال‌ها از طریق بله.

نکتهٔ مهم: این برنامه فقط برای کانال‌ها و حساب‌هایی است که کاربر مجوز
دسترسی و آرشیو آن‌ها را دارد. دادهٔ ذخیره‌شده روی دیسک شامل فهرست کانال‌ها
و کش تصویر پروفایل است تا /start و /list سریع باشند.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import pyzipper
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
    PeerChannel,
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
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

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
ZIP_PASS = os.environ["ZIP_PASS"]
CRYPT_PASS = os.environ["CRYPT_PASS"]

BALE_BASE_URL = "https://tapi.bale.ai/bot"
BALE_BASE_FILE_URL = "https://tapi.bale.ai/file/bot"
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
STATE_PATH = DATA_DIR / "state.json"
AVATAR_DIR = DATA_DIR / "avatars"

SPLIT_SIZE_BYTES = 10 * 1024 * 1024
AUTO_DELETE_HOURS = 4
WATCH_INTERVAL_SECONDS = 12 * 60 * 60
WATCH_MESSAGE_COUNT = 20
WATCH_MAX_FILE_BYTES = 30 * 1024 * 1024
MEDIA_CONCURRENCY = max(2, int(os.environ.get("MEDIA_CONCURRENCY", "8")))
DEFAULT_MESSAGE_COUNT = 30
DEFAULT_MAX_ZIP_MB = 50

userbot: Optional[TelegramClient] = None


def _ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict[str, Any]:
    _ensure_data_dirs()
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
            if isinstance(value, dict):
                value.setdefault("channels", {})
                return value
    except (OSError, ValueError):
        logger.warning("state file could not be read; starting with an empty cache")
    return {"channels": {}, "watch_mode": False}


def _save_state(state: dict[str, Any]) -> None:
    _ensure_data_dirs()
    temp_path = STATE_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    temp_path.replace(STATE_PATH)


def _photo_signature(entity: Channel) -> str:
    photo = getattr(entity, "photo", None)
    if not photo:
        return ""
    return f"{getattr(photo, 'photo_id', '')}:{getattr(photo, 'dc_id', '')}"


def _channel_from_entity(entity: Channel) -> dict[str, Any]:
    return {
        "id": int(entity.id),
        "username": getattr(entity, "username", None) or "",
        "title": getattr(entity, "title", None) or "",
        "photo_signature": _photo_signature(entity),
    }


async def _cache_avatar(entity: Channel, channel: dict[str, Any], state: dict[str, Any]) -> None:
    """Only downloads an avatar when Telegram reports a different photo."""
    key = str(channel["id"])
    old = state["channels"].get(key, {})
    target = AVATAR_DIR / f"{key}.jpg"
    signature = channel.get("photo_signature", "")
    channel["avatar_path"] = str(target) if target.exists() else ""

    if old.get("photo_signature") == signature and target.exists():
        return
    if not signature:
        with contextlib.suppress(OSError):
            target.unlink()
        channel["avatar_path"] = ""
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="avatar_", dir=str(DATA_DIR)))
    try:
        downloaded = await userbot.download_profile_photo(
            entity, file=str(temp_dir / "avatar.jpg")
        )
        if downloaded and Path(downloaded).exists():
            Path(downloaded).replace(target)
            channel["avatar_path"] = str(target)
    except Exception as exc:
        logger.warning("avatar download error for %s: %s", channel["title"], exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def refresh_channels() -> list[dict[str, Any]]:
    state = _load_state()
    fresh: dict[str, dict[str, Any]] = {}
    async for dialog in userbot.iter_dialogs():
        if not isinstance(dialog.entity, Channel) or not dialog.entity.broadcast:
            continue
        channel = _channel_from_entity(dialog.entity)
        await _cache_avatar(dialog.entity, channel, state)
        fresh[str(channel["id"])] = channel
    state["channels"] = fresh
    _save_state(state)
    return list(fresh.values())


async def get_channels(refresh: bool = False) -> list[dict[str, Any]]:
    state = _load_state()
    channels = list(state.get("channels", {}).values())
    if channels and not refresh:
        return channels
    return await refresh_channels()


async def _copy_cached_avatar(channel: dict[str, Any], media_dir: Path) -> str:
    source = Path(channel.get("avatar_path", ""))
    if not source.exists():
        return ""
    name = f"avatar_{channel['id']}{source.suffix or '.jpg'}"
    target = media_dir / name
    await asyncio.to_thread(shutil.copy2, source, target)
    return f"media/{name}"


def _media_descriptor(message: Any) -> tuple[str, str, str, int]:
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaPhoto):
        size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        return "image", ".jpg", "", size
    if not isinstance(media, MessageMediaDocument):
        return "", "", "", 0

    document = media.document
    mime = getattr(document, "mime_type", "") or ""
    size = int(getattr(document, "size", 0) or 0)
    filename = ""
    for attr in getattr(document, "attributes", []):
        if isinstance(attr, DocumentAttributeFilename):
            filename = attr.file_name
            break
    if "video" in mime:
        return "video", ".mp4", filename, size
    if "audio" in mime or "ogg" in mime:
        return "audio", ".ogg", filename, size
    if "image" in mime:
        return "image", ".jpg", filename, size
    suffix = Path(filename).suffix or ".bin"
    return "document", suffix, filename, size


def _message_entry(message: Any, media_type: str, media_name: str, media_size: int) -> dict[str, Any]:
    reactions: list[dict[str, Any]] = []
    msg_reactions = getattr(message, "reactions", None)
    for reaction in getattr(msg_reactions, "results", []) or []:
        reactions.append(
            {
                "emoji": getattr(getattr(reaction, "reaction", None), "emoticon", "?"),
                "count": getattr(reaction, "count", 0),
            }
        )
    return {
        "id": int(getattr(message, "id", 0) or 0),
        "text": getattr(message, "text", None) or getattr(message, "message", None) or "",
        "date": getattr(message, "date", None),
        "views": int(getattr(message, "views", 0) or 0),
        "reactions": reactions,
        "fwd_from": None,
        "media_path": None,
        "media_rel_path": "",
        "media_poster": "",
        "media_type": media_type,
        "media_name": media_name,
        "media_size": media_size,
    }


async def _download_one_media(
    message: Any,
    media_dir: Path,
    semaphore: asyncio.Semaphore,
    media_filter: str,
    max_media_bytes: Optional[int],
) -> Optional[dict[str, Any]]:
    media_type, extension, media_name, media_size = _media_descriptor(message)
    text = getattr(message, "text", None) or getattr(message, "message", None) or ""

    if media_filter == "text":
        if not text:
            return None
        return _message_entry(message, "", "", 0)
    if media_filter == "photos" and media_type != "image":
        return None
    if not media_type:
        if media_filter == "photos":
            return None
        return _message_entry(message, "", "", 0)

    entry = _message_entry(message, media_type, media_name, media_size)
    if max_media_bytes is not None and media_size > max_media_bytes:
        entry["media_skipped"] = True
        return entry

    filename = f"msg_{int(getattr(message, 'id', 0))}{extension}"
    target = media_dir / filename
    async with semaphore:
        try:
            downloaded = await userbot.download_media(message, file=str(target))
            if downloaded and target.exists():
                entry["media_path"] = str(target)
                entry["media_rel_path"] = f"media/{filename}"
                entry["media_size"] = media_size or target.stat().st_size
                if media_type == "video":
                    poster_target = media_dir / f"msg_{int(getattr(message, 'id', 0))}_poster.jpg"
                    try:
                        poster = await userbot.download_media(
                            message, file=str(poster_target), thumb=-1
                        )
                        if poster and poster_target.exists():
                            entry["media_poster"] = f"media/{poster_target.name}"
                    except Exception as exc:
                        logger.debug("video poster unavailable: %s", exc)
        except Exception as exc:
            logger.warning("media download error for message %s: %s", entry["id"], exc)
    return entry


async def collect_channel_messages(
    channel: dict[str, Any],
    limit: Optional[int],
    days: Optional[int],
    media_filter: str = "all",
    max_media_bytes: Optional[int] = None,
    media_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    entity = await userbot.get_entity(PeerChannel(channel["id"]))
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days is not None
        else None
    )
    messages: list[Any] = []
    fetch_limit = limit if limit is not None else 1000
    async for message in userbot.iter_messages(entity, limit=fetch_limit):
        date = getattr(message, "date", None)
        if cutoff and date:
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            if date < cutoff:
                break
        messages.append(message)

    if media_dir is None:
        media_dir = Path(tempfile.mkdtemp(prefix="media_"))
    media_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(MEDIA_CONCURRENCY)
    results = await asyncio.gather(
        *(
            _download_one_media(
                message, media_dir, semaphore, media_filter, max_media_bytes
            )
            for message in messages
        ),
        return_exceptions=True,
    )
    entries: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning("message processing error: %s", result)
        elif result is not None:
            entries.append(result)
    entries.sort(key=lambda item: item["id"])
    return entries


def create_protected_zip(html_content: str, media_dir: Path, zip_path: Path) -> None:
    with pyzipper.AESZipFile(
        zip_path,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zip_file:
        zip_file.setpassword(ZIP_PASS.encode())
        zip_file.writestr("index.html", html_content.encode("utf-8"))
        if media_dir.is_dir():
            for path in sorted(media_dir.rglob("*")):
                if path.is_file():
                    zip_file.write(path, arcname=f"media/{path.relative_to(media_dir)}")


def split_file(file_path: Path, part_size: int = SPLIT_SIZE_BYTES) -> list[Path]:
    parts: list[Path] = []
    with file_path.open("rb") as source:
        index = 1
        while chunk := source.read(part_size):
            part = Path(f"{file_path}.part{index}")
            part.write_bytes(chunk)
            parts.append(part)
            index += 1
    return parts


async def send_zip_parts_to_bale(context: ContextTypes.DEFAULT_TYPE, zip_path: Path) -> None:
    parts = split_file(zip_path)
    total = len(parts)
    for index, part in enumerate(parts, 1):
        try:
            with part.open("rb") as fh:
                await context.bot.send_document(
                    chat_id=ADMIN_ID,
                    document=fh,
                    filename=f"export_part{index}of{total}.zip",
                )
        finally:
            with contextlib.suppress(OSError):
                part.unlink()


def upload_with_retry(file_path: str, max_attempts: int = 3) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return upload_file(file_path)
        except Exception as exc:
            last_error = exc
            logger.warning("upload attempt %d failed: %s", attempt, exc)
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    raise last_error or RuntimeError("upload failed")


@dataclass
class ExportJob:
    context: ContextTypes.DEFAULT_TYPE
    channels: list[dict[str, Any]]
    count: int = DEFAULT_MESSAGE_COUNT
    days: Optional[int] = None
    media_filter: str = "all"
    max_zip_mb: int = DEFAULT_MAX_ZIP_MB
    manual_approval: bool = True
    label: str = "export"
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    progress_message_id: Optional[int] = None
    progress: int = 0
    started_at: float = 0.0
    cancelled: bool = False


class ExportQueue:
    def __init__(self, application: Application) -> None:
        self.application = application
        self.queue: asyncio.Queue[ExportJob] = asyncio.Queue()
        self.jobs: dict[str, ExportJob] = {}
        self.active: Optional[ExportJob] = None
        self.active_task: Optional[asyncio.Task[Any]] = None
        self.worker_task: Optional[asyncio.Task[Any]] = None

    async def start(self) -> None:
        self.worker_task = asyncio.create_task(self._worker(), name="export-queue")

    async def enqueue(self, job: ExportJob) -> int:
        self.jobs[job.job_id] = job
        position = self.queue.qsize() + (1 if self.active else 0) + 1
        await self.queue.put(job)
        return position

    async def cancel_all(self) -> None:
        for job in self.jobs.values():
            job.cancelled = True
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
        while not self.queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
                self.queue.task_done()

    async def stop(self) -> None:
        await self.cancel_all()
        if self.worker_task:
            self.worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker_task

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            self.active = job
            self.active_task = asyncio.create_task(
                run_export_job(job), name=f"export-{job.job_id}"
            )
            try:
                if not job.cancelled:
                    await self.active_task
            except asyncio.CancelledError:
                if self.active_task and not self.active_task.cancelled():
                    self.active_task.cancel()
            except Exception:
                logger.exception("export job %s failed", job.job_id)
                with contextlib.suppress(Exception):
                    await job.context.bot.send_message(chat_id=ADMIN_ID, text="✕")
            finally:
                self.active_task = None
                self.jobs.pop(job.job_id, None)
                self.active = None
                self.queue.task_done()


def _queue(application: Application) -> ExportQueue:
    return application.bot_data["export_queue"]


async def _set_progress(job: ExportJob, percent: int, detail: str = "") -> None:
    if job.cancelled:
        raise asyncio.CancelledError
    job.progress = max(0, min(100, percent))
    if not job.progress_message_id:
        return
    remaining = ""
    if job.started_at and 0 < job.progress < 100:
        elapsed = time.monotonic() - job.started_at
        seconds = max(0, int(elapsed * (100 - job.progress) / job.progress))
        remaining = f" · حدود {seconds} ثانیه باقی‌مانده"
    bar_len = 20
    filled = int(bar_len * job.progress / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    text = f"[{bar}] {job.progress}%{remaining}"
    if detail:
        text += f"\n{detail}"
    with contextlib.suppress(Exception):
        await job.context.bot.edit_message_text(
            chat_id=ADMIN_ID, message_id=job.progress_message_id, text=text
        )


async def _build_bundle(
    job: ExportJob, count: Optional[int], work_dir: Path, max_media_bytes: Optional[int] = None
) -> tuple[Path, list[dict[str, Any]]]:
    media_dir = work_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    channel_payloads: list[dict[str, Any]] = []

    async def build_one(channel: dict[str, Any]) -> dict[str, Any]:
        channel_media = media_dir / f"channel_{channel['id']}"
        messages = await collect_channel_messages(
            channel,
            limit=count,
            days=job.days,
            media_filter=job.media_filter,
            max_media_bytes=max_media_bytes,
            media_dir=channel_media,
        )
        for message in messages:
            if message.get("media_rel_path"):
                message["media_rel_path"] = (
                    f"media/channel_{channel['id']}/"
                    f"{Path(message['media_rel_path']).name}"
                )
            if message.get("media_poster"):
                message["media_poster"] = (
                    f"media/channel_{channel['id']}/"
                    f"{Path(message['media_poster']).name}"
                )
        avatar_rel = await _copy_cached_avatar(channel, media_dir)
        return {
            "name": channel["title"],
            "username": channel.get("username", ""),
            "avatar_rel_path": avatar_rel,
            "messages": messages,
        }

    channel_payloads = await asyncio.gather(*(build_one(ch) for ch in job.channels))
    html_content = generate_html(channels=channel_payloads)
    zip_path = work_dir / "export.zip"
    create_protected_zip(html_content, media_dir, zip_path)
    return zip_path, channel_payloads


async def _fit_bundle(job: ExportJob) -> tuple[Path, Path, list[dict[str, Any]]]:
    """Find a suitable count with binary search rather than decrementing one by one."""
    if job.days is not None:
        work_dir = Path(tempfile.mkdtemp(prefix="tgexport_"))
        zip_path, payload = await _build_bundle(job, None, work_dir)
        return zip_path, work_dir, payload

    low, high = 1, max(1, job.count)
    best: Optional[tuple[Path, Path, list[dict[str, Any]]]] = None
    best_any: Optional[tuple[Path, Path, list[dict[str, Any]]]] = None
    while low <= high:
        await _set_progress(
            job,
            min(55, 15 + int((job.count - high + low) / max(1, job.count) * 35)),
            f"بررسی سریع تعداد پیام: {((low + high) // 2)}",
        )
        mid = (low + high) // 2
        work_dir = Path(tempfile.mkdtemp(prefix="tgexport_"))
        zip_path, payload = await _build_bundle(job, mid, work_dir)
        result = (zip_path, work_dir, payload)
        best_any = result
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        if size_mb <= job.max_zip_mb:
            best = result
            low = mid + 1
        else:
            high = mid - 1
            shutil.rmtree(work_dir, ignore_errors=True)
        if job.cancelled:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise asyncio.CancelledError
    return best or best_any or await _build_fallback_bundle(job)


async def _build_fallback_bundle(
    job: ExportJob,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    work_dir = Path(tempfile.mkdtemp(prefix="tgexport_"))
    zip_path, payload = await _build_bundle(job, 1, work_dir)
    return zip_path, work_dir, payload


async def _request_approval(job: ExportJob, encrypted: str) -> str:
    event = asyncio.Event()
    approval = {"event": event, "value": None, "job_id": job.job_id}
    job.context.application.bot_data["pending_approval"] = approval
    try:
        await job.context.bot.send_message(chat_id=ADMIN_ID, text=encrypted)
        await event.wait()
    finally:
        if job.context.application.bot_data.get("pending_approval") is approval:
            job.context.application.bot_data.pop("pending_approval", None)
    value = approval["value"]
    if value == "y":
        try:
            await asyncio.to_thread(send_sms, encrypted)
        except Exception as exc:
            logger.warning("SMS failed after approval: %s", exc)
        await job.context.bot.send_message(chat_id=ADMIN_ID, text=encrypted)
    return value or "n"


async def run_export_job(job: ExportJob) -> None:
    job.started_at = time.monotonic()
    work_dir: Optional[Path] = None
    try:
        await _set_progress(job, 5, f"صف {job.label} · {len(job.channels)} کانال")
        zip_path, work_dir, _ = await _fit_bundle(job)
        await _set_progress(job, 65, "بسته آماده شد")

        upload_copy = work_dir / "export.jpg"
        await asyncio.to_thread(shutil.copyfile, zip_path, upload_copy)
        try:
            cdn_url = await asyncio.to_thread(upload_with_retry, str(upload_copy))
        except Exception:
            await send_zip_parts_to_bale(job.context, zip_path)
            await _set_progress(job, 100, "ارسال مستقیم انجام شد")
            return

        encrypted = encrypt(extract_variable(cdn_url), CRYPT_PASS)
        await _set_progress(job, 88, "متن آمادهٔ تأیید است")
        if job.manual_approval:
            await _request_approval(job, encrypted)
        else:
            message = await job.context.bot.send_message(chat_id=ADMIN_ID, text=encrypted)
            _track_message(job.context, message.message_id)
        await _set_progress(job, 100, "تمام شد")
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def cleanup_old_temp_files() -> None:
    try:
        root = Path(tempfile.gettempdir())
        cutoff = time.time() - AUTO_DELETE_HOURS * 3600
        for path in root.glob("tgexport_*"):
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
    except Exception as exc:
        logger.warning("cleanup error: %s", exc)


def _track_message(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> None:
    context.application.bot_data.setdefault("tracked_msgs", []).append(message_id)


async def auto_delete_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    ids = context.application.bot_data.pop("tracked_msgs", [])
    for message_id in ids:
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=message_id)


def _get_allowed(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    allowed = context.application.bot_data.setdefault("allowed_users", set())
    allowed.add(ADMIN_ID)
    return allowed


def admin_only(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if not update.effective_user or update.effective_user.id != ADMIN_ID:
            return None
        return await func(update, context)

    return wrapper


def _channel_keyboard(index: int, total: int) -> InlineKeyboardMarkup:
    navigation: list[InlineKeyboardButton] = []
    if index > 0:
        navigation.append(InlineKeyboardButton("◀", callback_data=f"nav:{index - 1}"))
    if index < total - 1:
        navigation.append(InlineKeyboardButton("▶", callback_data=f"nav:{index + 1}"))
    rows = [navigation] if navigation else []
    rows.append([InlineKeyboardButton("✓", callback_data=f"confirm:{index}")])
    return InlineKeyboardMarkup(rows)


async def _send_channel_card(
    context: ContextTypes.DEFAULT_TYPE, index: int, old_message_id: Optional[int] = None
) -> Optional[int]:
    channels = await get_channels()
    if not channels:
        return None
    if old_message_id:
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=old_message_id)
    channel = channels[index]
    caption = f"<b>{channel['title']}</b>"
    if channel.get("username"):
        caption += f"\n@{channel['username']}"
    caption += f"\n\n{index + 1} / {len(channels)}"
    avatar = Path(channel.get("avatar_path", ""))
    if avatar.exists():
        with avatar.open("rb") as fh:
            message = await context.bot.send_photo(
                chat_id=ADMIN_ID,
                photo=fh,
                caption=caption,
                parse_mode="HTML",
                reply_markup=_channel_keyboard(index, len(channels)),
            )
    else:
        message = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=caption,
            parse_mode="HTML",
            reply_markup=_channel_keyboard(index, len(channels)),
        )
    return message.message_id


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        _track_message(context, update.message.message_id)
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    if not channels:
        await update.message.reply_text("—")
        return
    message_id = await _send_channel_card(context, 0)
    if message_id:
        context.application.bot_data["current_card_msg_id"] = message_id
        _track_message(context, message_id)


@admin_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    text = "\n".join(channel["title"] for channel in channels) or "—"
    message = await update.message.reply_text(text)
    _track_message(context, message.message_id)


@admin_only
async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channels = await refresh_channels()
    message = await update.message.reply_text(str(len(channels)))
    _track_message(context, message.message_id)


def _parse_export_args(args: list[str], channels: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options: dict[str, Any] = {
        "days": None,
        "media_filter": "all",
        "count": DEFAULT_MESSAGE_COUNT,
        "max_zip_mb": DEFAULT_MAX_ZIP_MB,
    }
    selectors: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token.startswith("--"):
            key, _, value = token[2:].partition("=")
            if not value and index + 1 < len(args):
                index += 1
                value = args[index]
            if key in {"days", "recent"}:
                options["days"] = max(1, int(value))
            elif key in {"type", "filter"}:
                value = value.lower()
                options["media_filter"] = {
                    "photo": "photos",
                    "photos": "photos",
                    "image": "photos",
                    "images": "photos",
                    "text": "text",
                    "texts": "text",
                }.get(value, "all")
            elif key in {"count", "messages"}:
                options["count"] = max(1, int(value))
            elif key in {"max", "limit"}:
                options["max_zip_mb"] = max(1, int(value))
        else:
            selectors.append(token.lstrip("@").casefold())
        index += 1

    if not selectors or "all" in selectors:
        selected = channels
    else:
        selected = [
            channel
            for channel in channels
            if channel["title"].casefold() in selectors
            or channel.get("username", "").casefold() in selectors
        ]
    return selected, options


async def _enqueue_job(
    context: ContextTypes.DEFAULT_TYPE,
    channels: list[dict[str, Any]],
    *,
    label: str,
    manual_approval: bool,
    count: int = DEFAULT_MESSAGE_COUNT,
    days: Optional[int] = None,
    media_filter: str = "all",
    max_zip_mb: int = DEFAULT_MAX_ZIP_MB,
) -> None:
    if not channels:
        await context.bot.send_message(chat_id=ADMIN_ID, text="—")
        return
    job = ExportJob(
        context=context,
        channels=channels,
        count=count,
        days=days,
        media_filter=media_filter,
        max_zip_mb=max_zip_mb,
        manual_approval=manual_approval,
        label=label,
    )
    progress = await context.bot.send_message(chat_id=ADMIN_ID, text="[░░░░░░░░░░░░░░░░░░░░] 0%")
    job.progress_message_id = progress.message_id
    _track_message(context, progress.message_id)
    position = await _queue(context.application).enqueue(job)
    if position > 1:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"{position}")


@admin_only
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    try:
        selected, options = _parse_export_args(context.args, channels)
    except (TypeError, ValueError):
        await update.message.reply_text("✕")
        return
    await _enqueue_job(
        context,
        selected,
        label="export",
        manual_approval=True,
        **options,
    )


@admin_only
async def cmd_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        return
    with contextlib.suppress(ValueError):
        _get_allowed(context).add(int(context.args[0]))


@admin_only
async def cmd_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        return
    with contextlib.suppress(ValueError):
        value = max(1, int(context.args[0]))
        context.application.bot_data["max_zip_mb"] = value
        state = _load_state()
        state["max_zip_mb"] = value
        _save_state(state)


async def _manual_watch(context: ContextTypes.DEFAULT_TYPE) -> None:
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    await _enqueue_job(
        context,
        channels,
        label="watch",
        manual_approval=True,
        count=WATCH_MESSAGE_COUNT,
        max_zip_mb=10**9,
    )


@admin_only
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_state()
    state["watch_mode"] = True
    _save_state(state)
    context.application.bot_data["watch_mode"] = True
    await _manual_watch(context)


@admin_only
async def cmd_watch_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_state()
    state["watch_mode"] = False
    _save_state(state)
    context.application.bot_data["watch_mode"] = False
    await _queue(context.application).cancel_all()
    pending = context.application.bot_data.get("pending_approval")
    if pending:
        pending["value"] = "n"
        pending["event"].set()
    await context.bot.send_message(chat_id=ADMIN_ID, text="✕")


async def auto_watch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get("watch_mode"):
        return
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    await _enqueue_job(
        context,
        channels,
        label="watch-auto",
        manual_approval=False,
        count=WATCH_MESSAGE_COUNT,
        max_zip_mb=10**9,
    )


async def _resolve_approval(context: ContextTypes.DEFAULT_TYPE, value: str) -> bool:
    pending = context.application.bot_data.get("pending_approval")
    if not pending:
        return False
    pending["value"] = value
    pending["event"].set()
    return True


@admin_only
async def cmd_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_approval(context, "y")


@admin_only
async def cmd_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_approval(context, "n")


@admin_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip().casefold()
    if text in {"y", "yes", "بله"} and await _resolve_approval(context, "y"):
        return
    if text in {"n", "no", "خیر"} and await _resolve_approval(context, "n"):
        return

    state = context.user_data.get("state")
    if state != "waiting_count":
        return
    try:
        count = max(1, int(text))
    except ValueError:
        return
    pending_channel = context.user_data.pop("pending_channel", None)
    context.user_data["state"] = None
    if pending_channel:
        await _enqueue_job(
            context,
            [pending_channel],
            label="export",
            manual_approval=True,
            count=count,
            max_zip_mb=int(
                context.application.bot_data.get("max_zip_mb", DEFAULT_MAX_ZIP_MB)
            ),
        )


@admin_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("nav:"):
        old_id = query.message.message_id
        new_id = await _send_channel_card(context, int(data.split(":")[1]), old_id)
        if new_id:
            context.application.bot_data["current_card_msg_id"] = new_id
    elif data.startswith("confirm:"):
        channels = await get_channels()
        index = int(data.split(":")[1])
        if index >= len(channels):
            return
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=query.message.message_id)
        context.user_data["pending_channel"] = channels[index]
        context.user_data["state"] = "waiting_count"
        await context.bot.send_message(chat_id=ADMIN_ID, text="?")


async def main() -> None:
    global userbot
    cleanup_old_temp_files()
    _ensure_data_dirs()
    userbot = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await userbot.start()
    logger.info("Userbot connected")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url(BALE_BASE_URL)
        .base_file_url(BALE_BASE_FILE_URL)
        .build()
    )
    app.bot_data["watch_mode"] = _load_state().get("watch_mode", False)
    app.bot_data["max_zip_mb"] = _load_state().get("max_zip_mb", DEFAULT_MAX_ZIP_MB)
    app.bot_data["export_queue"] = ExportQueue(app)
    await _queue(app).start()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("Add", cmd_add_user))
    app.add_handler(CommandHandler("setlimit", cmd_set_limit))
    app.add_handler(CommandHandler("w", cmd_watch))
    app.add_handler(CommandHandler("woff", cmd_watch_off))
    app.add_handler(CommandHandler("y", cmd_yes))
    app.add_handler(CommandHandler("n", cmd_no))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.job_queue.run_repeating(auto_delete_messages, interval=3600, first=3600)
    app.job_queue.run_repeating(
        auto_watch_job, interval=WATCH_INTERVAL_SECONDS, first=WATCH_INTERVAL_SECONDS
    )

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query"])
    logger.info("Bot running")
    try:
        await asyncio.Event().wait()
    finally:
        await _queue(app).stop()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await userbot.disconnect()


if __name__ == "__main__":
    asyncio.run(main())