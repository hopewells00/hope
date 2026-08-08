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
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

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

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
ZIP_PASS = os.environ["ZIP_PASS"]
CRYPT_PASS = os.environ["CRYPT_PASS"]

BALE_BASE_URL = "https://tapi.bale.ai/bot"
BALE_BASE_FILE_URL = "https://tapi.bale.ai/file/bot"
BASE_DIR = Path(__file__).resolve().parent
LOG_MAX_BYTES = max(256 * 1024, int(os.environ.get("LOG_MAX_BYTES", str(10 * 1024 * 1024))))
LOG_BACKUP_COUNT = max(1, int(os.environ.get("LOG_BACKUP_COUNT", "5")))


def _configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            BASE_DIR / "bot.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
        RotatingFileHandler(
            BASE_DIR / "bot-error.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    handlers[0].setLevel(logging.INFO)
    handlers[1].setLevel(logging.ERROR)
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


_configure_logging()
logger = logging.getLogger(__name__)
_configured_data_dir = Path(os.environ.get("DATA_DIR", "data")).expanduser()
DATA_DIR = (
    _configured_data_dir
    if _configured_data_dir.is_absolute()
    else BASE_DIR / _configured_data_dir
)
STATE_PATH = DATA_DIR / "state.json"
AVATAR_DIR = DATA_DIR / "avatars"
STATE_BACKUP_DIR = Path(
    os.environ.get("STATE_BACKUP_DIR", str(DATA_DIR / "backups"))
).expanduser()

SPLIT_SIZE_BYTES = 10 * 1024 * 1024
AUTO_DELETE_HOURS = 4
DEFAULT_WATCH_INTERVAL_SECONDS = 12 * 60 * 60
WATCH_MESSAGE_COUNT = 20
WATCH_MAX_FILE_BYTES = 30 * 1024 * 1024
MEDIA_CONCURRENCY = max(2, int(os.environ.get("MEDIA_CONCURRENCY", "8")))
DEFAULT_MESSAGE_COUNT = 30
DEFAULT_MAX_ZIP_MB = 50
STATE_BACKUP_KEEP = max(2, int(os.environ.get("STATE_BACKUP_KEEP", "14")))
STATE_BACKUP_INTERVAL_SECONDS = max(
    300, int(os.environ.get("STATE_BACKUP_INTERVAL_SECONDS", str(6 * 3600)))
)


def _optional_int_env(name: str) -> Optional[int]:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        logger.warning("invalid integer environment variable %s=%r", name, value)
        return None


DEFAULT_MAX_MEDIA_BYTES = _optional_int_env("MAX_MEDIA_BYTES") or WATCH_MAX_FILE_BYTES

userbot: Optional[TelegramClient] = None


def _ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    STATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict[str, Any]:
    _ensure_data_dirs()
    candidates = [STATE_PATH] + sorted(
        STATE_BACKUP_DIR.glob("state-*.json"), reverse=True
    )
    for candidate in candidates:
        try:
            with candidate.open("r", encoding="utf-8") as fh:
                value = json.load(fh)
            if isinstance(value, dict):
                value.setdefault("channels", {})
                value.setdefault("watch_mode", False)
                value.setdefault("allowed_users", [])
                value.setdefault(
                    "watch_interval_seconds", DEFAULT_WATCH_INTERVAL_SECONDS
                )
                return value
        except (OSError, ValueError) as exc:
            logger.warning("could not load state candidate %s: %s", candidate, exc)
    return {
        "channels": {},
        "watch_mode": False,
        "allowed_users": [],
        "watch_interval_seconds": DEFAULT_WATCH_INTERVAL_SECONDS,
    }


def _save_state(state: dict[str, Any]) -> None:
    _ensure_data_dirs()
    temp_path = STATE_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    temp_path.replace(STATE_PATH)


def backup_state() -> None:
    """Write a rolling snapshot; set STATE_BACKUP_DIR to a persistent mount."""
    _ensure_data_dirs()
    if not STATE_PATH.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = STATE_BACKUP_DIR / f"state-{stamp}.json"
    temp_path = target.with_suffix(".tmp")
    try:
        shutil.copy2(STATE_PATH, temp_path)
        temp_path.replace(target)
        backups = sorted(STATE_BACKUP_DIR.glob("state-*.json"), key=lambda p: p.stat().st_mtime)
        for old in backups[:-STATE_BACKUP_KEEP]:
            with contextlib.suppress(OSError):
                old.unlink()
    except OSError as exc:
        logger.warning("state backup failed: %s", exc)


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
    old_ids = set(state.get("channels", {})) - set(fresh)
    for old_id in old_ids:
        for avatar in AVATAR_DIR.glob(f"{old_id}.*"):
            with contextlib.suppress(OSError):
                avatar.unlink()
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
    max_media_bytes: Optional[int] = None
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
            await _show_cancelled_progress(job)
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
                    await job.context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"❌ پردازش {job.label} با خطا متوقف شد.\n"
                            f"شناسهٔ درخواست: {job.job_id}\n"
                            "لطفاً دوباره تلاش کنید."
                        ),
                    )
            finally:
                self.active_task = None
                self.jobs.pop(job.job_id, None)
                self.active = None
                self.queue.task_done()

    async def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.cancelled = True
        await _show_cancelled_progress(job)
        if self.active is job and self.active_task and not self.active_task.done():
            self.active_task.cancel()
        return True


def _queue(application: Application) -> ExportQueue:
    return application.bot_data["export_queue"]


async def _show_cancelled_progress(job: ExportJob) -> None:
    if not job.progress_message_id:
        return
    with contextlib.suppress(Exception):
        await job.context.bot.edit_message_text(
            chat_id=ADMIN_ID,
            message_id=job.progress_message_id,
            text=f"⏹ درخواست {job.label} لغو شد.\nشناسهٔ درخواست: {job.job_id}",
        )


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
    markup = (
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏹ لغو export", callback_data=f"cancel:{job.job_id}")]]
        )
        if percent < 100
        else None
    )
    with contextlib.suppress(Exception):
        await job.context.bot.edit_message_text(
            chat_id=ADMIN_ID,
            message_id=job.progress_message_id,
            text=text,
            reply_markup=markup,
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
        zip_path, payload = await _build_bundle(
            job, None, work_dir, job.max_media_bytes
        )
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
        zip_path, payload = await _build_bundle(
            job, mid, work_dir, job.max_media_bytes
        )
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
    zip_path, payload = await _build_bundle(
        job, 1, work_dir, job.max_media_bytes
    )
    return zip_path, work_dir, payload


async def _request_approval(job: ExportJob, encrypted: str) -> str:
    event = asyncio.Event()
    approval = {"event": event, "value": None, "job_id": job.job_id}
    job.context.application.bot_data["pending_approval"] = approval
    try:
        approval_message = await job.context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ خروجی «{job.label}» آماده شد.\n"
                "متن رمز‌شده در پیام بعدی است.\n\n"
                "اگر می‌خواهید همین متن با پیامک هم ارسال شود، "
                "پاسخ دهید: y\n"
                "اگر فقط ارسال در بله کافی است، پاسخ دهید: n"
            ),
        )
        _track_message(job.context, approval_message.message_id)
        encrypted_message = await job.context.bot.send_message(
            chat_id=ADMIN_ID, text=encrypted
        )
        _track_message(job.context, encrypted_message.message_id)
        await event.wait()
    finally:
        if job.context.application.bot_data.get("pending_approval") is approval:
            job.context.application.bot_data.pop("pending_approval", None)
    value = approval["value"]
    if value == "y":
        confirmation = await job.context.bot.send_message(
            chat_id=ADMIN_ID,
            text="✅ تأیید دریافت شد؛ پیامک در حال ارسال است.",
        )
        _track_message(job.context, confirmation.message_id)
        try:
            sms_status, _ = await asyncio.to_thread(send_sms, encrypted)
            if sms_status == 200:
                sms_message = await job.context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="✅ پیامک با موفقیت ارسال شد. متن رمز‌شده دوباره در بله ارسال می‌شود.",
                )
                _track_message(job.context, sms_message.message_id)
            else:
                sms_message = await job.context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "⚠️ ارسال پیامک موفق نبود؛ متن رمز‌شده همچنان در بله "
                        "قابل استفاده است."
                    ),
                )
                _track_message(job.context, sms_message.message_id)
        except Exception as exc:
            logger.warning("SMS failed after approval: %s", exc)
            sms_message = await job.context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ ارسال پیامک موفق نبود؛ متن رمز‌شده همچنان در بله "
                    "قابل استفاده است."
                ),
            )
            _track_message(job.context, sms_message.message_id)
        repeated_encrypted = await job.context.bot.send_message(
            chat_id=ADMIN_ID, text=encrypted
        )
        _track_message(job.context, repeated_encrypted.message_id)
    elif value == "n":
        response = await job.context.bot.send_message(
            chat_id=ADMIN_ID,
            text="✅ پاسخ n ثبت شد؛ پیامک ارسال نمی‌شود.",
        )
        _track_message(job.context, response.message_id)
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
            await _set_progress(
                job,
                100,
                "✅ آپلود لینک انجام نشد؛ فایل به‌صورت مستقیم ارسال شد.",
            )
            return

        encrypted = encrypt(extract_variable(cdn_url), CRYPT_PASS)
        await _set_progress(
            job,
            92,
            "✅ خروجی آماده است؛ در انتظار پاسخ y برای پیامک یا n برای ادامه بدون پیامک.",
        )
        if job.manual_approval:
            approval_result = await _request_approval(job, encrypted)
            if approval_result == "y":
                final_detail = "✅ export کامل شد؛ پیامک و بله انجام شد."
            else:
                final_detail = "✅ export کامل شد؛ فقط در بله ارسال شد."
        else:
            notice = await job.context.bot.send_message(
                chat_id=ADMIN_ID,
                text="✅ واچ خودکار کامل شد؛ متن رمز‌شده در پیام بعدی ارسال می‌شود.",
            )
            _track_message(job.context, notice.message_id)
            message = await job.context.bot.send_message(
                chat_id=ADMIN_ID,
                text=encrypted,
            )
            _track_message(job.context, message.message_id)
            final_detail = "✅ واچ خودکار کامل شد؛ پیامک ارسال نشد."
        await _set_progress(job, 100, final_detail)
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
    tracked = context.application.bot_data.setdefault("tracked_msgs", [])
    tracked.append({"message_id": message_id, "created_at": time.time()})


async def auto_delete_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    tracked = context.application.bot_data.setdefault("tracked_msgs", [])
    cutoff = time.time() - AUTO_DELETE_HOURS * 3600
    keep: list[dict[str, Any]] = []
    for item in tracked:
        if isinstance(item, int):
            item = {"message_id": item, "created_at": 0}
        message_id = int(item.get("message_id", 0))
        created_at = float(item.get("created_at", 0))
        if created_at > cutoff:
            keep.append(item)
            continue
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=message_id)
    context.application.bot_data["tracked_msgs"] = keep


def _get_allowed(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    allowed = context.application.bot_data.setdefault("allowed_users", set())
    if not allowed:
        state_users = _load_state().get("allowed_users", [])
        allowed.update(int(user_id) for user_id in state_users if str(user_id).isdigit())
    allowed.add(ADMIN_ID)
    return allowed


def admin_only(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if (
            not update.effective_user
            or update.effective_user.id not in _get_allowed(context)
        ):
            return None
        return await func(update, context)

    return wrapper


def _channel_keyboard(
    channels: list[dict[str, Any]], selected_ids: set[int]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{'☑' if int(channel['id']) in selected_ids else '☐'} {channel['title'][:38]}",
                callback_data=f"toggle:{channel['id']}",
            )
        ]
        for channel in channels
    ]
    rows.append(
        [
            InlineKeyboardButton("انتخاب همه", callback_data="select:all"),
            InlineKeyboardButton("پاک‌کردن انتخاب", callback_data="select:none"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(f"ادامه با {len(selected_ids)} کانال", callback_data="confirm:selected")]
    )
    return InlineKeyboardMarkup(rows)


async def _send_channel_selector(
    context: ContextTypes.DEFAULT_TYPE, old_message_id: Optional[int] = None
) -> Optional[int]:
    channels = await get_channels()
    if not channels:
        return None
    selected_ids = set(context.user_data.get("selected_channel_ids", set()))
    if old_message_id:
        with contextlib.suppress(Exception):
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=old_message_id)
    caption = (
        "کانال‌های موردنظر را انتخاب کنید:\n"
        f"☑ انتخاب‌شده: {len(selected_ids)} از {len(channels)}\n"
        "بعد روی «ادامه» بزنید."
    )
    message = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=caption,
        reply_markup=_channel_keyboard(channels, selected_ids),
    )
    return message.message_id


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        intro = await update.message.reply_text(
            "سلام. یک یا چند کانال را با دکمه‌ها انتخاب کنید؛ "
            "بعد تعداد پیام‌های موردنظر را ارسال کنید."
        )
        _track_message(context, intro.message_id)
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    if not channels:
        message = await update.message.reply_text(
            "هیچ کانالی در فهرست ذخیره‌شده پیدا نشد. "
            "برای همگام‌سازی دوباره /refresh را بزنید."
        )
        _track_message(context, message.message_id)
        return
    context.user_data["selected_channel_ids"] = set()
    message_id = await _send_channel_selector(context)
    if message_id:
        context.application.bot_data["current_card_msg_id"] = message_id
        _track_message(context, message_id)


@admin_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    text = (
        "کانال‌های ذخیره‌شده:\n\n"
        + "\n".join(f"• {channel['title']}" for channel in channels)
        if channels
        else "فهرست کانال‌ها خالی است. برای دریافت دوباره /refresh را بزنید."
    )
    message = await update.message.reply_text(text)
    _track_message(context, message.message_id)


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    message = await update.message.reply_text(
        "راهنمای دستورها\n\n"
        "/start — انتخاب هم‌زمان چند کانال با دکمه\n"
        "/list — نمایش فهرست کانال‌های ذخیره‌شده\n"
        "/refresh — همگام‌سازی کانال‌ها و پاک‌کردن آواتارهای قدیمی\n"
        "/export all --count 30 — خروجی از همهٔ کانال‌ها\n"
        "/export نام‌کانال --days 3 --type photos — خروجی محدودشده\n"
        "  --media-max 30M — سقف رسانه برای همین export\n"
        "/setlimit 100 — سقف حجم ZIP برحسب مگابایت\n"
        "/w 6h — فعال‌سازی واچ با فاصلهٔ دلخواه (مثلاً 30m، 2h، 1d)\n"
        "/woff — توقف واچ و لغو exportهای در صف\n"
        "/status — وضعیت واچ، صف و کانال‌های فعلی\n"
        "/Add 12345 — افزودن کاربر مجاز\n"
        "/y یا /n — تأیید یا رد ارسال پیامک",
    )
    _track_message(context, message.message_id)


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    state = _load_state()
    channels = await get_channels()
    queue = _queue(context.application)
    active = queue.active
    interval = int(
        context.application.bot_data.get(
            "watch_interval_seconds",
            state.get("watch_interval_seconds", DEFAULT_WATCH_INTERVAL_SECONDS),
        )
    )
    status = "روشن" if context.application.bot_data.get("watch_mode") else "خاموش"
    active_text = (
        f"{active.label} ({active.job_id}) · {active.progress}%"
        if active
        else "ندارد"
    )
    message = await update.message.reply_text(
        "وضعیت فعلی\n\n"
        f"واچ: {status}\n"
        f"فاصلهٔ واچ: {_format_duration(interval)}\n"
        f"کانال‌های فعلی: {len(channels)}\n"
        f"پردازش فعال: {active_text}\n"
        f"در صف انتظار: {queue.queue.qsize()}\n"
        f"سقف ZIP: {context.application.bot_data.get('max_zip_mb', DEFAULT_MAX_ZIP_MB)} MB\n"
        f"سقف رسانه: {_format_bytes(DEFAULT_MAX_MEDIA_BYTES) if DEFAULT_MAX_MEDIA_BYTES else 'بدون سقف'}",
    )
    _track_message(context, message.message_id)


@admin_only
async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    channels = await refresh_channels()
    message = await update.message.reply_text(
        f"✅ فهرست کانال‌ها به‌روزرسانی شد.\nتعداد کانال‌ها: {len(channels)}"
    )
    _track_message(context, message.message_id)


def _parse_export_args(args: list[str], channels: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options: dict[str, Any] = {
        "days": None,
        "media_filter": "all",
        "count": DEFAULT_MESSAGE_COUNT,
        "max_zip_mb": DEFAULT_MAX_ZIP_MB,
        "max_media_bytes": DEFAULT_MAX_MEDIA_BYTES,
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
            elif key in {"media-max", "max-media", "max-media-bytes"}:
                options["max_media_bytes"] = _parse_size(value)
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


def _parse_size(value: str) -> int:
    """Parse a human-friendly byte limit such as 30M, 1.5GB, or 3145728."""
    normalized = value.strip().upper().replace(" ", "")
    units = (("GB", 1024**3), ("G", 1024**3), ("MB", 1024**2), ("M", 1024**2),
             ("KB", 1024), ("K", 1024), ("B", 1))
    for suffix, multiplier in units:
        if normalized.endswith(suffix):
            return max(1, int(float(normalized[:-len(suffix)]) * multiplier))
    return max(1, int(normalized))


def _format_bytes(value: Optional[int]) -> str:
    if not value:
        return "بدون سقف"
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GB"
    if value >= 1024**2:
        return f"{value / 1024**2:.0f} MB"
    return f"{value / 1024:.0f} KB"


def _parse_duration(value: str) -> int:
    normalized = value.strip().lower().replace(" ", "")
    if normalized.isdigit():
        return max(60, int(normalized) * 3600)
    units = {"m": 60, "min": 60, "h": 3600, "d": 86400}
    for suffix, multiplier in units.items():
        if normalized.endswith(suffix):
            amount = float(normalized[: -len(suffix)])
            return max(60, int(amount * multiplier))
    raise ValueError("invalid duration")


def _format_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} روز"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ساعت"
    return f"{seconds // 60} دقیقه"


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
    max_media_bytes: Optional[int] = None,
) -> None:
    if not channels:
        message = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "❌ هیچ کانالی برای export پیدا نشد.\n"
                "نام کانال را بررسی کنید یا ابتدا /refresh را بزنید."
            ),
        )
        _track_message(context, message.message_id)
        return
    job = ExportJob(
        context=context,
        channels=channels,
        count=count,
        days=days,
        media_filter=media_filter,
        max_zip_mb=max_zip_mb,
        max_media_bytes=max_media_bytes,
        manual_approval=manual_approval,
        label=label,
    )
    channel_names = "، ".join(channel["title"] for channel in channels)
    options_text = []
    if days is not None:
        options_text.append(f"{days} روز اخیر")
    else:
        options_text.append(f"{count} پیام از هر کانال")
    if media_filter == "photos":
        options_text.append("فقط عکس‌ها")
    elif media_filter == "text":
        options_text.append("فقط متن‌ها")
    request_message = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📥 درخواست {label} ثبت شد.\n"
            f"کانال‌ها: {channel_names}\n"
            f"محدوده: {'، '.join(options_text)}\n"
            "دانلود رسانه‌ها به‌صورت هم‌زمان انجام می‌شود."
        ),
    )
    _track_message(context, request_message.message_id)
    progress = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text="[░░░░░░░░░░░░░░░░░░░░] 0%\nدر حال آماده‌سازی...",
    )
    job.progress_message_id = progress.message_id
    _track_message(context, progress.message_id)
    position = await _queue(context.application).enqueue(job)
    if position > 1:
        queue_message = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"⏳ درخواست در صف قرار گرفت.\n"
                f"جایگاه در صف: {position}\n"
                "وقتی نوبت برسد، همین‌جا وضعیت پردازش نمایش داده می‌شود."
            ),
        )
        _track_message(context, queue_message.message_id)


@admin_only
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    channels = await get_channels()
    if not channels:
        channels = await refresh_channels()
    try:
        selected, options = _parse_export_args(context.args, channels)
    except (TypeError, ValueError):
        await update.message.reply_text(
            "❌ قالب دستور درست نیست.\n"
            "نمونه: /export all --count 30\n"
            "یا: /export نام‌کانال --days 3 --type photos"
        )
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
    _track_message(context, update.message.message_id)
    if not context.args:
        message = await update.message.reply_text("قالب درست: /Add شناسهٔ عددی کاربر")
        _track_message(context, message.message_id)
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        message = await update.message.reply_text("شناسهٔ کاربر باید عددی باشد.")
        _track_message(context, message.message_id)
        return
    allowed = _get_allowed(context)
    allowed.add(user_id)
    state = _load_state()
    state["allowed_users"] = sorted(allowed - {ADMIN_ID})
    _save_state(state)
    message = await update.message.reply_text(
        f"✅ کاربر {user_id} اضافه شد و از این پس به دستورهای بات دسترسی دارد."
    )
    _track_message(context, message.message_id)


@admin_only
async def cmd_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    if not context.args:
        message = await update.message.reply_text("قالب درست: /setlimit 100")
        _track_message(context, message.message_id)
        return
    try:
        value = max(1, int(context.args[0]))
    except ValueError:
        message = await update.message.reply_text("حجم باید یک عدد مثبت برحسب مگابایت باشد.")
        _track_message(context, message.message_id)
        return
    context.application.bot_data["max_zip_mb"] = value
    state = _load_state()
    state["max_zip_mb"] = value
    _save_state(state)
    message = await update.message.reply_text(
        f"✅ سقف حجم ZIP روی {value} مگابایت تنظیم شد."
    )
    _track_message(context, message.message_id)


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
        max_media_bytes=WATCH_MAX_FILE_BYTES,
    )


@admin_only
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    state = _load_state()
    try:
        interval = _parse_duration(context.args[0]) if context.args else int(
            state.get("watch_interval_seconds", DEFAULT_WATCH_INTERVAL_SECONDS)
        )
    except (TypeError, ValueError):
        message = await update.message.reply_text(
            "قالب زمان‌بندی درست نیست. نمونه‌ها: /w 30m ، /w 6h ، /w 1d"
        )
        _track_message(context, message.message_id)
        return
    state["watch_mode"] = True
    state["watch_interval_seconds"] = interval
    _save_state(state)
    context.application.bot_data["watch_mode"] = True
    context.application.bot_data["watch_interval_seconds"] = interval
    context.application.bot_data["watch_next_run_at"] = time.time() + interval
    start_message = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "▶️ واچ فعال شد.\n"
            "همین حالا ۲۰ پیام آخر تمام کانال‌ها جمع‌آوری می‌شود.\n"
            f"بعد از این، هر {_format_duration(interval)} یک اجرای خودکار انجام خواهد شد."
        ),
    )
    _track_message(context, start_message.message_id)
    await _manual_watch(context)


@admin_only
async def cmd_watch_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    state = _load_state()
    state["watch_mode"] = False
    _save_state(state)
    context.application.bot_data["watch_mode"] = False
    await _queue(context.application).cancel_all()
    pending = context.application.bot_data.get("pending_approval")
    if pending:
        pending["value"] = "n"
        pending["event"].set()
    message = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "⏹ واچ متوقف شد.\n"
            "همهٔ درخواست‌های منتظر و درخواست در حال پردازش لغو شدند."
        ),
    )
    _track_message(context, message.message_id)


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
        max_media_bytes=WATCH_MAX_FILE_BYTES,
    )


async def schedule_watch_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.application.bot_data.get("watch_mode"):
        return
    now = time.time()
    next_run = float(context.application.bot_data.get("watch_next_run_at", 0))
    if now < next_run:
        return
    interval = int(
        context.application.bot_data.get(
            "watch_interval_seconds", DEFAULT_WATCH_INTERVAL_SECONDS
        )
    )
    context.application.bot_data["watch_next_run_at"] = now + interval
    await auto_watch_job(context)


async def scheduled_state_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(backup_state)


async def _resolve_approval(context: ContextTypes.DEFAULT_TYPE, value: str) -> bool:
    pending = context.application.bot_data.get("pending_approval")
    if not pending:
        return False
    pending["value"] = value
    pending["event"].set()
    return True


@admin_only
async def cmd_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    if await _resolve_approval(context, "y"):
        return
    message = await update.message.reply_text("در حال حاضر خروجی‌ای برای تأیید وجود ندارد.")
    _track_message(context, message.message_id)


@admin_only
async def cmd_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
    if await _resolve_approval(context, "n"):
        return
    message = await update.message.reply_text("در حال حاضر خروجی‌ای برای ردکردن وجود ندارد.")
    _track_message(context, message.message_id)


@admin_only
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _track_message(context, update.message.message_id)
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
    pending_channels = context.user_data.pop("pending_channels", [])
    context.user_data["state"] = None
    if pending_channels:
        await _enqueue_job(
            context,
            pending_channels,
            label="export",
            manual_approval=True,
            count=count,
            max_zip_mb=int(
                context.application.bot_data.get("max_zip_mb", DEFAULT_MAX_ZIP_MB)
            ),
            max_media_bytes=DEFAULT_MAX_MEDIA_BYTES,
        )
    else:
        message = await update.message.reply_text(
            "انتخاب کانال منقضی شده است. دوباره /start را بزنید."
        )
        _track_message(context, message.message_id)


@admin_only
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("cancel:"):
        job_id = data.split(":", 1)[1]
        cancelled = await _queue(context.application).cancel_job(job_id)
        message = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ درخواست {job_id} لغو شد."
                if cancelled
                else "این درخواست دیگر در صف یا حال پردازش نیست."
            ),
        )
        _track_message(context, message.message_id)
    elif data.startswith("toggle:"):
        channels = await get_channels()
        channel_id = int(data.split(":", 1)[1])
        if channel_id not in {int(channel["id"]) for channel in channels}:
            return
        selected = set(context.user_data.get("selected_channel_ids", set()))
        if channel_id in selected:
            selected.remove(channel_id)
        else:
            selected.add(channel_id)
        context.user_data["selected_channel_ids"] = selected
        new_id = await _send_channel_selector(context, query.message.message_id)
        if new_id:
            context.application.bot_data["current_card_msg_id"] = new_id
    elif data == "select:all":
        channels = await get_channels()
        context.user_data["selected_channel_ids"] = {
            int(channel["id"]) for channel in channels
        }
        new_id = await _send_channel_selector(context, query.message.message_id)
        if new_id:
            context.application.bot_data["current_card_msg_id"] = new_id
    elif data == "select:none":
        context.user_data["selected_channel_ids"] = set()
        new_id = await _send_channel_selector(context, query.message.message_id)
        if new_id:
            context.application.bot_data["current_card_msg_id"] = new_id
    elif data == "confirm:selected":
        channels = await get_channels()
        selected_ids = set(context.user_data.get("selected_channel_ids", set()))
        selected_channels = [
            channel for channel in channels if int(channel["id"]) in selected_ids
        ]
        if not selected_channels:
            await query.answer("حداقل یک کانال را انتخاب کنید.", show_alert=True)
            return
        with contextlib.suppress(Exception):
            await context.bot.delete_message(
                chat_id=ADMIN_ID, message_id=query.message.message_id
            )
        context.user_data["pending_channels"] = selected_channels
        context.user_data["state"] = "waiting_count"
        prompt = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"{len(selected_channels)} کانال انتخاب شد.\n"
                "چند پیام آخر را می‌خواهید دریافت کنید؟\n"
                "لطفاً فقط یک عدد مثبت بفرستید؛ مثلاً 30."
            ),
        )
        _track_message(context, prompt.message_id)


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
    state = _load_state()
    watch_interval = int(
        state.get("watch_interval_seconds", DEFAULT_WATCH_INTERVAL_SECONDS)
    )
    app.bot_data["watch_mode"] = state.get("watch_mode", False)
    app.bot_data["watch_interval_seconds"] = watch_interval
    app.bot_data["watch_next_run_at"] = (
        time.time() + watch_interval if app.bot_data["watch_mode"] else 0
    )
    app.bot_data["max_zip_mb"] = state.get("max_zip_mb", DEFAULT_MAX_ZIP_MB)
    app.bot_data["allowed_users"] = {
        int(user_id) for user_id in state.get("allowed_users", []) if str(user_id).isdigit()
    }
    app.bot_data["export_queue"] = ExportQueue(app)
    await _queue(app).start()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
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

    app.job_queue.run_repeating(auto_delete_messages, interval=60, first=60)
    app.job_queue.run_repeating(
        schedule_watch_job, interval=60, first=60
    )
    app.job_queue.run_repeating(
        scheduled_state_backup,
        interval=STATE_BACKUP_INTERVAL_SECONDS,
        first=STATE_BACKUP_INTERVAL_SECONDS,
    )
    backup_state()

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