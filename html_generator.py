"""تولید خروجی HTML چندکاناله با رابط خوانا، قابل جست‌وجو و راست‌به‌چپ."""

from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")
MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4", ".wav": "audio/wav", ".pdf": "application/pdf",
    ".zip": "application/zip", ".rar": "application/x-rar-compressed",
    ".doc": "application/msword", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".oga"}
FILE_ICONS = {
    ".pdf": "PDF", ".doc": "DOC", ".docx": "DOC", ".xls": "XLS",
    ".xlsx": "XLS", ".ppt": "PPT", ".pptx": "PPT", ".zip": "ZIP",
    ".rar": "RAR", ".txt": "TXT",
}


def _mime(path: str) -> str:
    return MIME_MAP.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def _format_date(value: object) -> str:
    if not isinstance(value, datetime):
        return html.escape(str(value or ""))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(TEHRAN)
    return f"{local:%H:%M} · {local.day:02d}/{local.month:02d}/{local.year}"


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
            return '<div class="skipped"><span>فایل بزرگ‌تر از سقف انتخابی است</span></div>'
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
        return f'<div class="audio"><div class="media-label">{name}</div><audio controls preload="metadata" src="{rel}"></audio></div>'
    name = html.escape(message.get("media_name") or os.path.basename(path))
    label = FILE_ICONS.get(extension, "FILE")
    size = _format_size(int(message.get("media_size", 0) or os.path.getsize(path)))
    return (
        f'<a class="document" href="{rel}" download="{name}"><span class="file-icon">{label}</span>'
        f'<span class="file-copy"><b>{name}</b><small>{size}</small></span><span class="download">↓</span></a>'
    )


def _render_message(message: dict, index: int) -> str:
    text = html.escape(message.get("text", "") or "").replace("\n", "<br>")
    reactions = "".join(
        f'<span>{html.escape(str(item.get("emoji", "")))} {item.get("count", 0)}</span>'
        for item in message.get("reactions", [])
    )
    body = _render_media(message)
    if text:
        body += f'<div class="message-text">{text}</div>'
    if not body:
        body = '<div class="empty-message">رسانه یا متن قابل نمایش نیست</div>'
    type_name = "متن" if not message.get("media_type") else message.get("media_type")
    return (
        f'<article class="message" data-search="{html.escape((message.get("text", "") or "").casefold())}" '
        f'data-type="{html.escape(str(type_name))}" id="message-{index}">'
        f'{body}<footer><span>{_format_date(message.get("date"))}</span>'
        f'<span>{int(message.get("views", 0) or 0):,} بازدید</span></footer>'
        f'<div class="reactions">{reactions}</div></article>'
    )


def _render_channel(channel: dict, index: int) -> str:
    name = html.escape(channel.get("name", ""))
    username = html.escape(channel.get("username", ""))
    avatar = channel.get("avatar_rel_path", "")
    avatar_html = (
        f'<img class="avatar" src="{html.escape(avatar)}" alt="">'
        if avatar
        else f'<div class="avatar fallback">{html.escape((name or "?")[:1])}</div>'
    )
    messages = "".join(
        _render_message(message, index * 100000 + message_index)
        for message_index, message in enumerate(channel.get("messages", []))
    )
    handle = f'<span>@{username}</span>' if username else ""
    return (
        f'<section class="channel" data-channel="{name}" id="channel-{index}">'
        f'<header class="channel-header">{avatar_html}<div class="channel-copy"><h2>{name}</h2>'
        f'<p>{handle} <i>·</i> {len(channel.get("messages", []))} پیام</p></div>'
        f'<a class="channel-link" href="#channel-{index}" aria-label="لینک کانال">#</a></header>'
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
        channels = [{
            "name": channel_name or "",
            "avatar_rel_path": "media/avatar.jpg" if channel_avatar_path else "",
            "messages": messages or [],
        }]
    title = html.escape(channel_name or (channels[0].get("name", "") if channels else "Archive"))
    channel_html = "".join(_render_channel(channel, index) for index, channel in enumerate(channels))
    total_messages = sum(len(channel.get("messages", [])) for channel in channels)
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{title}</title>
<style>
:root{{--bg:#09111f;--panel:#111d2d;--panel-2:#17263a;--bubble:#13243a;--line:#29415e;--text:#edf6ff;--muted:#8fa7bb;--accent:#55c7ff;--accent-2:#9c7cff;--shadow:0 18px 60px #0007}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;min-height:100vh;background:radial-gradient(ellipse at 50% -10%,#234c6b 0,#0b1423 42%,var(--bg) 75%);color:var(--text);font-family:Vazirmatn,Tahoma,"Segoe UI",sans-serif;padding-bottom:72px}}
.topbar{{position:sticky;top:0;z-index:10;padding:18px clamp(16px,4vw,54px);background:#0b1423dd;border-bottom:1px solid #ffffff12;backdrop-filter:blur(22px);box-shadow:0 8px 32px #0003}}
.topbar-inner{{max-width:900px;margin:auto;display:flex;align-items:center;gap:18px;justify-content:space-between}}.brand{{display:flex;align-items:center;gap:12px;min-width:0}}.brand-mark{{width:43px;height:43px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(140deg,var(--accent),var(--accent-2));box-shadow:0 8px 24px #55c7ff44;font-size:21px;font-weight:900;color:#07111d}}.brand h1{{margin:0;font-size:18px;letter-spacing:-.3px}}.brand small{{display:block;color:var(--muted);font-size:11px;margin-top:4px}}.count{{color:#d4e9f8;font-size:12px;white-space:nowrap}}
.toolbar{{max-width:900px;margin:22px auto 0;padding:0 14px;display:grid;grid-template-columns:1fr auto;gap:10px}}.search-wrap{{position:relative}}.search{{width:100%;background:#122238cc;border:1px solid var(--line);border-radius:14px;color:var(--text);padding:13px 42px 13px 14px;outline:none;font-size:13px;transition:.2s}}.search:focus{{border-color:var(--accent);box-shadow:0 0 0 4px #55c7ff18}}.search-icon{{position:absolute;right:15px;top:11px;color:var(--muted);font-size:17px}}.filters{{display:flex;gap:6px;align-items:center}}.filter{{cursor:pointer;border:1px solid var(--line);background:#122238cc;color:var(--muted);border-radius:11px;padding:9px 11px;font-size:11px}}.filter.active,.filter:hover{{background:#1b4565;color:var(--text);border-color:#55c7ff88}}
.summary{{max-width:900px;margin:14px auto 0;padding:0 14px;color:var(--muted);font-size:11px}}.summary strong{{color:var(--accent)}}.channel{{max-width:760px;margin:30px auto 0;padding:0 14px}}.channel-header{{display:flex;gap:13px;align-items:center;padding:13px 15px;background:#122238ee;border:1px solid var(--line);border-radius:18px;position:sticky;top:80px;z-index:4;box-shadow:var(--shadow)}}.avatar{{width:52px;height:52px;border-radius:16px;object-fit:cover;flex:none;border:1px solid #ffffff22}}.avatar.fallback{{display:grid;place-items:center;background:linear-gradient(140deg,#2a9dcc,#6354bc);font-size:23px;font-weight:800}}.channel-copy{{min-width:0;flex:1}}.channel h2{{font-size:16px;margin:0 0 5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.channel p{{margin:0;color:var(--muted);font-size:11px}}.channel p span{{color:var(--accent)}}.channel p i{{font-style:normal;color:#5d748a;margin:0 4px}}.channel-link{{color:var(--accent);text-decoration:none;font-size:20px;padding:7px 10px;border-radius:10px;background:#55c7ff12}}.feed{{padding:15px 3px;display:flex;flex-direction:column;gap:11px}}.message{{max-width:680px;background:linear-gradient(145deg,#142945,#102238);border:1px solid #ffffff0d;border-radius:17px 17px 5px 17px;padding:12px 13px;box-shadow:0 7px 25px #0002;transition:transform .2s,border-color .2s,opacity .2s}}.message:hover{{transform:translateY(-2px);border-color:#55c7ff66}}.message-text{{font-size:14px;line-height:2;word-break:break-word;margin-top:9px}}.message footer{{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:10px;margin-top:10px;padding-top:8px;border-top:1px solid #ffffff0a}}.reactions:empty{{display:none}}.reactions{{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}}.reactions span{{background:#1d4562;border:1px solid #55c7ff44;border-radius:12px;padding:3px 8px;font-size:11px;color:#c7efff}}.image-link{{display:block;overflow:hidden;border-radius:12px;background:#07101a}}.media-image{{display:block;width:100%;max-height:560px;object-fit:cover;cursor:zoom-in;transition:transform .35s}}.image-link:hover .media-image{{transform:scale(1.02)}}.media-video{{display:block;width:100%;max-height:560px;border-radius:12px;background:#050a10}}.audio{{background:var(--panel-2);border-radius:12px;padding:12px;color:var(--muted);font-size:11px}}.audio audio{{display:block;width:100%;margin-top:9px}}.document{{display:flex;align-items:center;gap:11px;background:var(--panel-2);border:1px solid #ffffff0a;border-radius:12px;padding:11px;color:var(--text);text-decoration:none}}.file-icon{{width:43px;height:43px;border-radius:11px;background:linear-gradient(145deg,#236184,#263d76);display:grid;place-items:center;font-size:10px;font-weight:800;color:#d9f5ff}}.file-copy{{min-width:0;flex:1}}.file-copy b{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}}.file-copy small{{display:block;color:var(--muted);margin-top:5px}}.download{{color:var(--accent);font-size:24px}}.skipped,.empty-message{{color:var(--muted);font-size:12px;padding:12px;background:#ffffff06;border-radius:10px}}.empty-state{{max-width:760px;margin:50px auto;text-align:center;color:var(--muted);padding:30px}}.top{{position:fixed;left:18px;bottom:18px;border:1px solid var(--line);background:#122238e8;color:var(--accent);width:42px;height:42px;border-radius:13px;cursor:pointer;font-size:18px}}
@media(max-width:650px){{.topbar-inner{{align-items:flex-start}}.count{{display:none}}.toolbar{{grid-template-columns:1fr;margin-top:14px}}.filters{{justify-content:stretch}}.filter{{flex:1}}.channel{{margin-top:20px}}.channel-header{{top:78px}}.message-text{{font-size:13px}}}}
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner"><div class="brand"><div class="brand-mark">✦</div><div><h1>آرشیو پیام‌ها</h1><small>زمان‌ها به وقت تهران نمایش داده می‌شوند</small></div></div><div class="count">{len(channels)} کانال · {total_messages} پیام</div></div></header>
<div class="toolbar"><div class="search-wrap"><span class="search-icon">⌕</span><input class="search" id="search" type="search" placeholder="جست‌وجو در متن پیام‌ها و نام کانال..." aria-label="جست‌وجو"></div><div class="filters"><button class="filter active" data-filter="all">همه</button><button class="filter" data-filter="متن">متن</button><button class="filter" data-filter="image">عکس</button><button class="filter" data-filter="video">ویدئو</button></div></div>
<div class="summary" id="summary">نمایش <strong>{total_messages}</strong> پیام از <strong>{len(channels)}</strong> کانال</div>
{channel_html or '<div class="empty-state">پیامی برای نمایش وجود ندارد.</div>'}
<button class="top" id="top" aria-label="بازگشت به بالا">↑</button>
<script>
const search=document.getElementById("search"), filters=[...document.querySelectorAll(".filter")], channels=[...document.querySelectorAll(".channel")], messages=[...document.querySelectorAll(".message")], summary=document.getElementById("summary");
let active="all";
function apply(){{const q=(search.value||"").trim().toLocaleLowerCase();let visible=0,visibleChannels=0;channels.forEach(ch=>{{let shown=0;const channelName=(ch.dataset.channel||"").toLocaleLowerCase();ch.querySelectorAll(".message").forEach(m=>{{const okType=active==="all"||m.dataset.type===active;const okText=!q||channelName.includes(q)||(m.dataset.search||"").includes(q);m.hidden=!(okType&&okText);if(!m.hidden)shown++}});ch.hidden=!shown;if(shown)visibleChannels++;visible+=shown}});summary.innerHTML=`نمایش <strong>${{visible}}</strong> پیام از <strong>${{visibleChannels}}</strong> کانال`;}}
search.addEventListener("input",apply);filters.forEach(btn=>btn.addEventListener("click",()=>{{filters.forEach(x=>x.classList.remove("active"));btn.classList.add("active");active=btn.dataset.filter;apply()}}));document.getElementById("top").addEventListener("click",()=>scrollTo({{top:0,behavior:"smooth"}}));
document.addEventListener("click",event=>{{const image=event.target.closest(".media-image");if(!image)return;event.preventDefault();const layer=document.createElement("div");layer.style="position:fixed;inset:0;background:#020711eF;z-index:20;display:grid;place-items:center;padding:20px;cursor:zoom-out";const copy=document.createElement("img");copy.src=image.src;copy.style="max-width:96vw;max-height:94vh;object-fit:contain;border-radius:12px;box-shadow:0 20px 80px #000";layer.appendChild(copy);layer.onclick=()=>layer.remove();document.body.appendChild(layer)}}); 
</script>
</body>
</html>"""