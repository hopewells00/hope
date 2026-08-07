"""تولید خروجی HTML چندکاناله با تجربه‌ای نزدیک به فید تلگرام."""

from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Optional

MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".rar": "application/x-rar-compressed",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".oga"}
FILE_ICONS = {
    ".pdf": "PDF",
    ".doc": "DOC",
    ".docx": "DOC",
    ".xls": "XLS",
    ".xlsx": "XLS",
    ".ppt": "PPT",
    ".pptx": "PPT",
    ".zip": "ZIP",
    ".rar": "RAR",
    ".txt": "TXT",
}


def _mime(path: str) -> str:
    return MIME_MAP.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def _format_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M · %d/%m/%Y")
    return str(value or "")


def _format_size(value: int) -> str:
    if not value:
        return ""
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    if value < 1024 * 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    return f"{value / (1024 * 1024 * 1024):.1f} GB"


def _render_media(message: dict) -> str:
    path = message.get("media_path")
    if not path or not os.path.exists(path):
        if message.get("media_skipped"):
            return '<div class="skipped">این فایل به‌دلیل حجم زیاد همراه خروجی نیست.</div>'
        return ""
    rel = html.escape(message.get("media_rel_path") or f"media/{os.path.basename(path)}")
    media_type = message.get("media_type", "")
    extension = os.path.splitext(path)[1].lower()
    if media_type == "image" or extension in IMAGE_EXTS:
        return f'<a class="image-link" href="{rel}" target="_blank"><img class="media-image" src="{rel}" loading="lazy" alt=""></a>'
    if media_type == "video" or extension in VIDEO_EXTS:
        poster = message.get("media_poster", "")
        poster_attr = f' poster="{html.escape(poster)}"' if poster else ""
        return (
            f'<video class="media-video" controls preload="metadata"{poster_attr}>'
            f'<source src="{rel}" type="{_mime(path)}"><a href="{rel}" download>دانلود ویدئو</a></video>'
        )
    if media_type == "audio" or extension in AUDIO_EXTS:
        name = html.escape(message.get("media_name") or os.path.basename(path))
        return f'<div class="audio"><div>{name}</div><audio controls preload="metadata" src="{rel}"></audio></div>'
    name = html.escape(message.get("media_name") or os.path.basename(path))
    label = FILE_ICONS.get(extension, "FILE")
    size = _format_size(int(message.get("media_size", 0) or os.path.getsize(path)))
    return (
        f'<a class="document" href="{rel}" download="{name}">'
        f'<span class="file-icon">{label}</span><span class="file-copy"><b>{name}</b><small>{size}</small></span>'
        f'<span class="download">↓</span></a>'
    )


def _render_message(message: dict, index: int) -> str:
    text = html.escape(message.get("text", "") or "").replace("\n", "<br>")
    reactions = "".join(
        f'<span>{html.escape(str(item.get("emoji", "")))} {item.get("count", 0)}</span>'
        for item in message.get("reactions", [])
    )
    reaction_html = f'<div class="reactions">{reactions}</div>' if reactions else ""
    body = _render_media(message)
    if text:
        body += f'<div class="message-text">{text}</div>'
    if not body:
        body = '<div class="empty-message">رسانه یا متن قابل نمایش نیست</div>'
    return (
        f'<article class="message" id="message-{index}">'
        f'{body}<footer><span>{_format_date(message.get("date"))}</span>'
        f'<span>{int(message.get("views", 0) or 0):,} بازدید</span></footer>{reaction_html}</article>'
    )


def _render_channel(channel: dict, index: int) -> str:
    name = html.escape(channel.get("name", ""))
    username = html.escape(channel.get("username", ""))
    avatar = channel.get("avatar_rel_path", "")
    if avatar:
        avatar_html = f'<img class="avatar" src="{html.escape(avatar)}" alt="">'
    else:
        avatar_html = f'<div class="avatar fallback">{html.escape((name or "?")[:1])}</div>'
    messages = "".join(
        _render_message(message, index * 100000 + message_index)
        for message_index, message in enumerate(channel.get("messages", []))
    )
    handle = f'<span>@{username}</span>' if username else ""
    return (
        f'<section class="channel"><header class="channel-header">{avatar_html}'
        f'<div><h2>{name}</h2><p>{handle} · {len(channel.get("messages", []))} پیام</p></div></header>'
        f'<div class="feed">{messages}</div></section>'
    )


def generate_html(
    channel_name: Optional[str] = None,
    channel_avatar_path: Optional[str] = None,
    messages: Optional[list] = None,
    msg_count: int = 0,
    *,
    channels: Optional[list[dict]] = None,
) -> str:
    if channels is None:
        channels = [
            {
                "name": channel_name or "",
                "avatar_rel_path": "media/avatar.jpg" if channel_avatar_path else "",
                "messages": messages or [],
            }
        ]
    title = html.escape(channel_name or (channels[0].get("name", "") if channels else "Archive"))
    channel_html = "".join(_render_channel(channel, index) for index, channel in enumerate(channels))
    total_messages = sum(len(channel.get("messages", [])) for channel in channels)
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:dark;--bg:#0e1621;--panel:#17212b;--panel2:#202b36;--line:#2b3946;--text:#e9f1f7;--muted:#8293a3;--blue:#2aabee;--bubble:#182533}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);font-family:Tahoma,"Segoe UI",sans-serif}}
body{{background:radial-gradient(circle at 50% -10%,#1b3244 0,#0e1621 42rem);padding-bottom:48px}}
.topbar{{position:sticky;top:0;z-index:3;background:rgba(14,22,33,.94);backdrop-filter:blur(14px);border-bottom:1px solid var(--line);padding:16px clamp(16px,4vw,42px);display:flex;align-items:center;justify-content:space-between}}
.brand{{display:flex;gap:12px;align-items:center}}.brand-mark{{width:36px;height:36px;border-radius:50%;background:var(--blue);display:grid;place-items:center;font-weight:800;color:white;font-size:19px}}
.brand h1{{margin:0;font-size:17px}}.brand small{{display:block;color:var(--muted);margin-top:3px;font-size:11px}}.count{{color:var(--muted);font-size:12px}}
.channel{{max-width:780px;margin:28px auto 0;padding:0 12px}}.channel-header{{display:flex;gap:12px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;position:sticky;top:69px;z-index:2;box-shadow:0 8px 24px #07101866}}
.avatar{{width:48px;height:48px;border-radius:50%;object-fit:cover;flex:none}}.avatar.fallback{{display:grid;place-items:center;background:linear-gradient(135deg,#2aabee,#17628b);font-size:22px;font-weight:700}}
.channel h2{{font-size:16px;margin:0 0 4px}}.channel p{{margin:0;color:var(--muted);font-size:11px}}.channel p span{{color:var(--blue)}}
.feed{{padding:14px 4px;display:flex;flex-direction:column;gap:8px}}.message{{max-width:650px;background:var(--bubble);border:1px solid #ffffff08;border-radius:12px 12px 3px 12px;padding:10px 12px;box-shadow:0 3px 14px #0000001c}}
.message:hover{{border-color:#2aabee55}}.message-text{{font-size:14px;line-height:1.85;word-break:break-word;margin-top:8px}}.message footer{{display:flex;gap:12px;color:var(--muted);font-size:10px;margin-top:8px}}.reactions{{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}}.reactions span{{background:#24445a;border:1px solid #2aabee55;border-radius:12px;padding:3px 8px;font-size:11px;color:#b9e8ff}}
.image-link{{display:block}}.media-image{{display:block;max-width:100%;max-height:520px;border-radius:9px;object-fit:contain;background:#0c1219;cursor:zoom-in}}.media-video{{display:block;width:100%;max-height:520px;border-radius:9px;background:#090d12}}.audio{{background:var(--panel2);border-radius:9px;padding:10px;color:var(--muted);font-size:11px}}.audio audio{{display:block;width:100%;margin-top:7px}}
.document{{display:flex;align-items:center;gap:10px;background:var(--panel2);border-radius:9px;padding:10px;color:var(--text);text-decoration:none}}.file-icon{{width:42px;height:42px;border-radius:9px;background:#236184;display:grid;place-items:center;font-size:10px;font-weight:700}}.file-copy{{min-width:0;flex:1}}.file-copy b{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}}.file-copy small{{display:block;color:var(--muted);margin-top:4px}}.download{{color:var(--blue);font-size:24px}}.skipped,.empty-message{{color:var(--muted);font-size:12px;padding:8px;background:#ffffff05;border-radius:8px}}
@media(max-width:600px){{.channel-header{{top:68px}}.channel{{margin-top:18px}}.message-text{{font-size:13px}}.count{{display:none}}}}
</style>
</head>
<body>
<header class="topbar"><div class="brand"><div class="brand-mark">➤</div><div><h1>آرشیو</h1><small>خروجی پیام‌ها</small></div></div><div class="count">{len(channels)} کانال · {total_messages} پیام</div></header>
{channel_html}
<script>
document.addEventListener("click",function(event){{
  const image=event.target.closest(".media-image"); if(!image)return;
  event.preventDefault(); const layer=document.createElement("div");
  layer.style="position:fixed;inset:0;background:#000d;z-index:9;display:grid;place-items:center;padding:20px;cursor:zoom-out";
  const copy=document.createElement("img"); copy.src=image.src; copy.style="max-width:96vw;max-height:94vh;object-fit:contain";
  layer.appendChild(copy); layer.onclick=()=>layer.remove(); document.body.appendChild(layer);
}});
</script>
</body>
</html>"""