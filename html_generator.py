"""
تولید فایل HTML شبیه تلگرام از پیام‌های کانال بله
رسانه‌ها به صورت فایل جداگانه در پوشه media/ ذخیره می‌شوند
"""

import html
import os
from datetime import datetime
from typing import Optional


# ── نقشه MIME ──────────────────────────────────────────────────────────────

MIME_MAP = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".ogg": "audio/ogg", ".oga": "audio/ogg",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".rar": "application/x-rar-compressed",
    ".7z": "application/x-7z-compressed",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".apk": "application/vnd.android.package-archive",
}

# آیکون‌های فایل بر اساس پسوند
FILE_ICONS = {
    ".pdf": "📄",
    ".doc": "📝", ".docx": "📝",
    ".xls": "📊", ".xlsx": "📊",
    ".ppt": "📊", ".pptx": "📊",
    ".zip": "🗜️", ".rar": "🗜️", ".7z": "🗜️",
    ".txt": "📃", ".csv": "📃",
    ".apk": "📱",
    ".mp3": "🎵", ".m4a": "🎵", ".aac": "🎵", ".flac": "🎵", ".wav": "🎵",
    ".ogg": "🎵", ".oga": "🎵",
    ".mp4": "🎬", ".mkv": "🎬", ".avi": "🎬", ".mov": "🎬",
    ".jpg": "🖼️", ".jpeg": "🖼️", ".png": "🖼️", ".gif": "🖼️", ".webp": "🖼️",
}

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".oga"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".webp"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return MIME_MAP.get(ext, "application/octet-stream")


def _format_date(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        try:
            return dt.strftime("%H:%M · %d %b %Y")
        except Exception:
            return str(dt)
    return str(dt)


def _format_size(size_bytes: int) -> str:
    """حجم فایل را به فرمت خوانا تبدیل می‌کند."""
    if size_bytes <= 0:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def _get_relative_media_path(media_path: str) -> str:
    """مسیر نسبی فایل رسانه را برای HTML برمی‌گرداند."""
    return f"media/{os.path.basename(media_path)}"


def _render_media(msg: dict) -> str:
    """رسانه پیام را به HTML تبدیل می‌کند (با مسیر نسبی)."""
    media_path = msg.get("media_path")
    media_type = msg.get("media_type", "")
    media_name = msg.get("media_name", "")
    media_size = msg.get("media_size", 0)

    if not media_path or not os.path.exists(media_path):
        return ""

    ext = os.path.splitext(media_path)[1].lower()
    rel_path = _get_relative_media_path(media_path)
    esc_path = html.escape(rel_path)

    # عکس
    if media_type in ("image", "sticker") or (ext in IMAGE_EXTS and media_type not in ("document",)):
        return f'''
    <div class="msg-media">
      <a href="{esc_path}" target="_blank" class="media-link">
        <img src="{esc_path}" loading="lazy" alt="" class="media-img">
      </a>
    </div>'''

    # ویدیو / انیمیشن / video_note
    if media_type in ("video", "animation", "video_note") or ext in VIDEO_EXTS:
        return f'''
    <div class="msg-media">
      <video controls preload="metadata" class="media-video">
        <source src="{esc_path}" type="{_mime(media_path)}">
        <a href="{esc_path}" class="dl-btn">⬇️ دانلود ویدیو</a>
      </video>
    </div>'''

    # صدا / پیام صوتی
    if media_type in ("audio", "voice") or ext in AUDIO_EXTS:
        icon = "🎙️" if media_type == "voice" else "🎵"
        name_html = f'<span class="audio-name">{icon} {html.escape(media_name or "پیام صوتی")}</span>' if media_name else f'<span class="audio-name">{icon} پیام صوتی</span>'
        return f'''
    <div class="msg-media audio-wrap">
      {name_html}
      <audio controls preload="metadata" class="media-audio">
        <source src="{esc_path}" type="{_mime(media_path)}">
      </audio>
    </div>'''

    # اسناد و فایل‌های دیگر
    file_icon = FILE_ICONS.get(ext, "📎")
    display_name = html.escape(media_name or os.path.basename(media_path))
    size_str = _format_size(media_size) if media_size else _format_size(os.path.getsize(media_path))
    mime = _mime(media_path)

    return f'''
    <div class="msg-media document-wrap">
      <a href="{esc_path}" download="{display_name}" class="doc-card">
        <div class="doc-icon">{file_icon}</div>
        <div class="doc-info">
          <div class="doc-name">{display_name}</div>
          <div class="doc-meta">{size_str} · {ext.lstrip(".").upper() if ext else "فایل"}</div>
        </div>
        <div class="doc-dl">⬇️</div>
      </a>
    </div>'''


def _render_message(msg: dict, index: int) -> str:
    """یک پیام را به HTML تبدیل می‌کند."""
    media_html = _render_media(msg)
    text_raw = msg.get("text", "") or ""
    text = html.escape(text_raw).replace("\n", "<br>")
    text_html = f'<div class="msg-text">{text}</div>' if text else ""
    date_str = _format_date(msg.get("date"))
    sender = html.escape(msg.get("sender", "") or "")
    sender_html = f'<div class="msg-sender">{sender}</div>' if sender else ""

    # واکنش‌ها
    reactions_html = ""
    reactions = msg.get("reactions", [])
    if reactions:
        items = "".join(
            f'<span class="react-item">{html.escape(str(r["emoji"]))} <span class="react-count">{r["count"]}</span></span>'
            for r in reactions
        )
        reactions_html = f'<div class="reactions">{items}</div>'

    views = msg.get("views", 0)
    views_html = f'<span class="views">👁 {views:,}</span>' if views else ""

    # فوروارد
    fwd_html = ""
    fwd_from = msg.get("fwd_from")
    if fwd_from:
        fwd_html = f'<div class="fwd-tag">↪️ Forwarded from <b>{html.escape(str(fwd_from))}</b></div>'

    return f"""
  <div class="message" id="msg-{index}">
    {fwd_html}
    {sender_html}
    {media_html}
    {text_html}
    <div class="msg-footer">
      <span class="msg-date">{date_str}</span>
      {views_html}
    </div>
    {reactions_html}
  </div>"""


def generate_html(
    channel_name: str,
    channel_bio: str,
    channel_avatar_path: Optional[str],
    messages: list,
    msg_count: int,
) -> str:
    """
    لیستی از پیام‌ها را می‌گیرد و یک HTML کامل برمی‌گرداند.
    رسانه‌ها با مسیر نسبی media/ ارجاع داده می‌شوند.
    """
    if channel_avatar_path and os.path.exists(channel_avatar_path):
        avatar_html = '<img class="ch-avatar" src="media/avatar.jpg" alt="">'
    else:
        initial = html.escape((channel_name or "?")[:1])
        avatar_html = f'<div class="ch-avatar-placeholder">{initial}</div>'

    msgs_html = "".join(_render_message(m, i) for i, m in enumerate(messages))
    safe_name = html.escape(channel_name or "")
    safe_bio = html.escape(channel_bio or "").replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_name}</title>
<style>
/* ═══════════════ Reset & Base ═══════════════ */
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  font-family:-apple-system,'Segoe UI',Tahoma,Arial,sans-serif;
  background:#0e1621;
  color:#d1d5db;
  min-height:100vh;
  direction:rtl;
}}
a{{color:inherit;text-decoration:none}}

/* ═══════════════ Header ═══════════════ */
.channel-header{{
  position:sticky;top:0;z-index:100;
  background:linear-gradient(180deg,#1c2b3a 0%,#17212b 100%);
  border-bottom:1px solid rgba(47,159,224,0.15);
  padding:12px 16px;
  display:flex;align-items:center;gap:12px;
  backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);
  box-shadow:0 2px 12px rgba(0,0,0,.4);
}}
.ch-avatar{{
  width:48px;height:48px;border-radius:50%;
  object-fit:cover;
  border:2px solid #2f9fe0;
  flex-shrink:0;
  box-shadow:0 0 0 3px rgba(47,159,224,.18);
}}
.ch-avatar-placeholder{{
  width:48px;height:48px;border-radius:50%;
  background:linear-gradient(135deg,#2f9fe0,#1565c0);
  display:flex;align-items:center;justify-content:center;
  font-size:20px;font-weight:700;color:#fff;
  flex-shrink:0;
  box-shadow:0 0 0 3px rgba(47,159,224,.18);
}}
.ch-info{{flex:1;min-width:0}}
.ch-name{{
  font-size:17px;font-weight:700;color:#fff;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  letter-spacing:.01em;
}}
.ch-bio{{
  font-size:13px;color:#8ea0b4;
  margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}}
.msg-count-badge{{
  font-size:11px;color:#5fb8e8;
  background:rgba(47,159,224,.12);
  border:1px solid rgba(47,159,224,.2);
  padding:4px 10px;border-radius:20px;
  white-space:nowrap;font-weight:600;
}}

/* ═══════════════ Feed ═══════════════ */
.feed{{
  max-width:700px;margin:0 auto;
  padding:12px 8px 80px;
  display:flex;flex-direction:column;gap:3px;
}}

/* ═══════════════ Message ═══════════════ */
.message{{
  background:linear-gradient(135deg,#1e2e3e 0%,#192330 100%);
  border-radius:12px;
  padding:10px 14px 8px;
  position:relative;
  border:1px solid rgba(47,159,224,.06);
  transition:background .15s ease,border-color .15s ease;
}}
.message:hover{{
  background:linear-gradient(135deg,#233447 0%,#1c2838 100%);
  border-color:rgba(47,159,224,.14);
}}

/* فوروارد */
.fwd-tag{{
  font-size:12.5px;color:#5fb8e8;
  border-right:3px solid #2f9fe0;
  padding:2px 8px 2px 0;
  margin-bottom:7px;
  opacity:.9;
}}

/* فرستنده */
.msg-sender{{
  font-size:13px;font-weight:600;
  color:#5fb8e8;margin-bottom:4px;
}}

/* ═══════════════ Media ═══════════════ */
.msg-media{{margin:8px 0 4px}}

/* عکس */
.media-link{{display:block}}
.media-img{{
  max-width:100%;max-height:500px;
  border-radius:10px;display:block;
  object-fit:contain;
  background:#111c28;
  cursor:zoom-in;
  transition:opacity .2s;
}}
.media-img:hover{{opacity:.92}}

/* ویدیو */
.media-video{{
  width:100%;max-height:500px;
  border-radius:10px;
  background:#000;
  display:block;
}}

/* صدا */
.audio-wrap{{
  background:rgba(47,159,224,.07);
  border-radius:10px;
  padding:10px 12px;
}}
.audio-name{{
  display:block;
  font-size:13px;color:#8ea0b4;
  margin-bottom:6px;
}}
.media-audio{{
  width:100%;
  accent-color:#2f9fe0;
}}

/* سند / فایل */
.document-wrap{{
  background:rgba(47,159,224,.05);
  border-radius:10px;
  padding:0;
}}
.doc-card{{
  display:flex;align-items:center;gap:12px;
  padding:10px 14px;
  border-radius:10px;
  border:1px solid rgba(47,159,224,.12);
  background:rgba(30,46,62,.6);
  transition:background .15s,border-color .15s;
  cursor:pointer;
}}
.doc-card:hover{{
  background:rgba(47,159,224,.1);
  border-color:rgba(47,159,224,.25);
}}
.doc-icon{{
  font-size:28px;flex-shrink:0;
  width:40px;text-align:center;
}}
.doc-info{{flex:1;min-width:0;overflow:hidden}}
.doc-name{{
  font-size:14px;font-weight:600;color:#c9d4df;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.doc-meta{{
  font-size:12px;color:#5e8db3;margin-top:2px;
}}
.doc-dl{{
  font-size:18px;flex-shrink:0;
  color:#5fb8e8;opacity:.7;
}}
.doc-card:hover .doc-dl{{opacity:1}}

/* ═══════════════ Text ═══════════════ */
.msg-text{{
  font-size:15px;line-height:1.65;
  color:#d1d5db;word-break:break-word;
  margin-top:2px;
}}

/* ═══════════════ Footer ═══════════════ */
.msg-footer{{
  display:flex;align-items:center;gap:8px;
  margin-top:7px;
  flex-wrap:wrap;
}}
.msg-date{{font-size:11.5px;color:#4e6070}}
.views{{font-size:11.5px;color:#4e6070}}

/* ═══════════════ Reactions ═══════════════ */
.reactions{{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}}
.react-item{{
  background:rgba(47,159,224,.1);
  border:1px solid rgba(47,159,224,.2);
  border-radius:20px;padding:3px 9px;
  font-size:13.5px;display:flex;align-items:center;gap:4px;
  cursor:default;
  transition:background .15s;
}}
.react-item:hover{{background:rgba(47,159,224,.18)}}
.react-count{{font-size:12px;color:#8ea0b4;font-weight:500}}

/* ═══════════════ Scrollbar ═══════════════ */
::-webkit-scrollbar{{width:5px}}
::-webkit-scrollbar-track{{background:#0e1621}}
::-webkit-scrollbar-thumb{{background:#2b3a4a;border-radius:3px}}
::-webkit-scrollbar-thumb:hover{{background:#3a5068}}

/* ═══════════════ Date Divider ═══════════════ */
.date-divider{{
  display:flex;align-items:center;gap:10px;
  margin:14px 0 6px;
  opacity:.6;
}}
.date-divider::before,.date-divider::after{{
  content:'';flex:1;
  height:1px;background:rgba(47,159,224,.15);
}}
.date-divider span{{
  font-size:11px;color:#5e7a90;
  white-space:nowrap;
  padding:3px 10px;
  background:rgba(47,159,224,.06);
  border-radius:12px;
  border:1px solid rgba(47,159,224,.1);
}}

/* ═══════════════ Responsive ═══════════════ */
@media(max-width:520px){{
  .ch-name{{font-size:15px}}
  .msg-text{{font-size:14px}}
  .media-img,.media-video{{border-radius:8px}}
  .doc-icon{{font-size:22px;width:32px}}
  .channel-header{{padding:10px 12px}}
  .feed{{padding:8px 4px 80px}}
}}
</style>
</head>
<body>

<!-- Header -->
<div class="channel-header">
  {avatar_html}
  <div class="ch-info">
    <div class="ch-name">{safe_name}</div>
    {"<div class='ch-bio'>" + safe_bio + "</div>" if safe_bio else ""}
  </div>
  <div class="msg-count-badge">{msg_count} پیام</div>
</div>

<!-- Messages Feed -->
<div class="feed">
{msgs_html}
</div>

<script>
(function() {{
  // اسکرول به آخرین پیام
  window.addEventListener('load', function() {{
    var msgs = document.querySelectorAll('.message');
    if (msgs.length) {{
      msgs[msgs.length - 1].scrollIntoView({{ behavior: 'smooth', block: 'end' }});
    }}
  }});

  // Lightbox ساده برای عکس‌ها
  var overlay = null;
  document.addEventListener('click', function(e) {{
    var img = e.target.closest('.media-img');
    if (img) {{
      e.preventDefault();
      if (!overlay) {{
        overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.9);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:zoom-out;';
        overlay.addEventListener('click', function() {{ overlay.remove(); overlay = null; }});
      }}
      overlay.innerHTML = '';
      var bigImg = document.createElement('img');
      bigImg.src = img.src;
      bigImg.style.cssText = 'max-width:95vw;max-height:95vh;object-fit:contain;border-radius:8px;';
      overlay.appendChild(bigImg);
      document.body.appendChild(overlay);
    }}
  }});
}})();
</script>
</body>
</html>"""
