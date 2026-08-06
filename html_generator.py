"""
تولید HTML کانال با تم Cyberpunk + Futuristic
"""

import html
import os
from datetime import datetime
from typing import Optional


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

FILE_ICONS = {
    ".pdf": "⬡",
    ".doc": "⬡", ".docx": "⬡",
    ".xls": "⬡", ".xlsx": "⬡",
    ".ppt": "⬡", ".pptx": "⬡",
    ".zip": "◈", ".rar": "◈", ".7z": "◈",
    ".txt": "⬡", ".csv": "⬡",
    ".apk": "◉",
    ".mp3": "♪", ".m4a": "♪", ".aac": "♪", ".flac": "♪", ".wav": "♪",
    ".ogg": "♪", ".oga": "♪",
    ".mp4": "▶", ".mkv": "▶", ".avi": "▶", ".mov": "▶",
    ".jpg": "◈", ".jpeg": "◈", ".png": "◈", ".gif": "◈", ".webp": "◈",
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
    return f"media/{os.path.basename(media_path)}"


def _render_media(msg: dict) -> str:
    media_path = msg.get("media_path")
    media_type = msg.get("media_type", "")
    media_name = msg.get("media_name", "")
    media_size = msg.get("media_size", 0)

    if not media_path or not os.path.exists(media_path):
        return ""

    ext = os.path.splitext(media_path)[1].lower()
    rel_path = _get_relative_media_path(media_path)
    esc_path = html.escape(rel_path)

    if media_type in ("image", "sticker") or (ext in IMAGE_EXTS and media_type != "document"):
        return f'''
    <div class="msg-media">
      <a href="{esc_path}" target="_blank" class="media-link">
        <img src="{esc_path}" loading="lazy" alt="" class="media-img">
      </a>
    </div>'''

    if media_type in ("video", "animation", "video_note") or (ext in VIDEO_EXTS and media_type != "document"):
        return f'''
    <div class="msg-media">
      <video controls preload="metadata" class="media-video">
        <source src="{esc_path}" type="{_mime(media_path)}">
        <a href="{esc_path}" class="dl-btn">⬇ دانلود</a>
      </video>
    </div>'''

    if media_type in ("audio", "voice") or (ext in AUDIO_EXTS and media_type != "document"):
        icon = "◉" if media_type == "voice" else "♪"
        name_esc = html.escape(media_name or "audio")
        name_html = f'<span class="audio-name">{icon} {name_esc}</span>'
        return f'''
    <div class="msg-media audio-wrap">
      {name_html}
      <audio controls preload="metadata" class="media-audio">
        <source src="{esc_path}" type="{_mime(media_path)}">
      </audio>
    </div>'''

    # هر نوع فایل دیگر — نمایش به عنوان سند قابل دانلود
    file_icon = FILE_ICONS.get(ext, "◈")
    display_name = html.escape(media_name or os.path.basename(media_path))
    size_str = _format_size(media_size) if media_size else _format_size(os.path.getsize(media_path))
    ext_label = ext.lstrip(".").upper() if ext else "FILE"

    return f'''
    <div class="msg-media document-wrap">
      <a href="{esc_path}" download="{display_name}" class="doc-card">
        <div class="doc-icon">{file_icon}</div>
        <div class="doc-info">
          <div class="doc-name">{display_name}</div>
          <div class="doc-meta">{size_str} · {ext_label}</div>
        </div>
        <div class="doc-dl">⬇</div>
      </a>
    </div>'''


def _render_message(msg: dict, index: int) -> str:
    media_html = _render_media(msg)
    text_raw = msg.get("text", "") or ""
    text = html.escape(text_raw).replace("\n", "<br>")
    text_html = f'<div class="msg-text">{text}</div>' if text else ""
    date_str = _format_date(msg.get("date"))
    sender = html.escape(msg.get("sender", "") or "")
    sender_html = f'<div class="msg-sender">{sender}</div>' if sender else ""

    reactions_html = ""
    reactions = msg.get("reactions", [])
    if reactions:
        items = "".join(
            f'<span class="react-item">{html.escape(str(r["emoji"]))} <span class="react-count">{r["count"]}</span></span>'
            for r in reactions
        )
        reactions_html = f'<div class="reactions">{items}</div>'

    views = msg.get("views", 0)
    views_html = f'<span class="views">◎ {views:,}</span>' if views else ""

    fwd_html = ""
    fwd_from = msg.get("fwd_from")
    if fwd_from:
        fwd_html = f'<div class="fwd-tag">↪ <b>{html.escape(str(fwd_from))}</b></div>'

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
    channel_avatar_path: Optional[str],
    messages: list,
    msg_count: int,
) -> str:
    if channel_avatar_path and os.path.exists(channel_avatar_path):
        avatar_html = '<img class="ch-avatar" src="media/avatar.jpg" alt="">'
    else:
        initial = html.escape((channel_name or "?")[:1])
        avatar_html = f'<div class="ch-avatar-placeholder">{initial}</div>'

    msgs_html = "".join(_render_message(m, i) for i, m in enumerate(messages))
    safe_name = html.escape(channel_name or "")

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_name}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

/* ══ Reset ══ */
*{{box-sizing:border-box;margin:0;padding:0}}

/* ══ Cyberpunk Variables ══ */
:root{{
  --bg:       #020510;
  --bg2:      #060d1a;
  --bg3:      #0a1628;
  --cyan:     #00d4ff;
  --blue:     #0066cc;
  --blue2:    #0044aa;
  --accent:   #0088ee;
  --glow:     rgba(0,212,255,.18);
  --glow2:    rgba(0,136,238,.25);
  --text:     #c8d8e8;
  --text-dim: #4a6a8a;
  --border:   rgba(0,212,255,.12);
  --border2:  rgba(0,212,255,.25);
  --scan:     rgba(0,212,255,.025);
}}

/* ══ Base ══ */
body{{
  font-family:'Share Tech Mono',monospace,'Segoe UI',sans-serif;
  background:var(--bg);
  color:var(--text);
  min-height:100vh;
  direction:rtl;
  position:relative;
  overflow-x:hidden;
}}

/* CRT scanlines overlay */
body::before{{
  content:'';
  position:fixed;
  inset:0;
  background:repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    var(--scan) 2px,
    var(--scan) 4px
  );
  pointer-events:none;
  z-index:9998;
}}

/* Ambient glow corners */
body::after{{
  content:'';
  position:fixed;
  top:-200px;left:-200px;
  width:500px;height:500px;
  background:radial-gradient(circle,rgba(0,100,200,.12) 0%,transparent 70%);
  pointer-events:none;
  z-index:0;
  animation:pulse 6s ease-in-out infinite;
}}
@keyframes pulse{{
  0%,100%{{opacity:.5;transform:scale(1)}}
  50%{{opacity:1;transform:scale(1.08)}}
}}

a{{color:var(--cyan);text-decoration:none}}

/* ══ Header ══ */
.channel-header{{
  position:sticky;top:0;z-index:100;
  background:linear-gradient(180deg,rgba(6,13,26,.98) 0%,rgba(2,5,16,.95) 100%);
  border-bottom:1px solid var(--cyan);
  padding:12px 16px;
  display:flex;align-items:center;gap:14px;
  backdrop-filter:blur(16px);
  box-shadow:0 0 30px rgba(0,212,255,.08),0 2px 0 var(--cyan);
}}
.channel-header::before{{
  content:'';
  position:absolute;
  bottom:-1px;left:0;right:0;
  height:1px;
  background:linear-gradient(90deg,transparent,var(--cyan),transparent);
  animation:scan-h 3s linear infinite;
}}
@keyframes scan-h{{
  0%{{opacity:0;transform:translateX(-100%)}}
  50%{{opacity:1}}
  100%{{opacity:0;transform:translateX(100%)}}
}}

/* Avatar */
.ch-avatar{{
  width:52px;height:52px;border-radius:4px;
  object-fit:cover;
  border:1px solid var(--cyan);
  flex-shrink:0;
  box-shadow:0 0 12px var(--glow),inset 0 0 6px rgba(0,212,255,.06);
  image-rendering:crisp-edges;
  clip-path:polygon(6px 0%,100% 0%,100% calc(100% - 6px),calc(100% - 6px) 100%,0% 100%,0% 6px);
}}
.ch-avatar-placeholder{{
  width:52px;height:52px;
  border:1px solid var(--cyan);
  background:linear-gradient(135deg,var(--bg3),var(--blue2));
  display:flex;align-items:center;justify-content:center;
  font-size:22px;font-weight:700;color:var(--cyan);
  flex-shrink:0;
  box-shadow:0 0 12px var(--glow);
  clip-path:polygon(6px 0%,100% 0%,100% calc(100% - 6px),calc(100% - 6px) 100%,0% 100%,0% 6px);
}}
.ch-info{{flex:1;min-width:0}}
.ch-name{{
  font-size:15px;font-weight:700;
  color:var(--cyan);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  letter-spacing:.08em;
  text-shadow:0 0 10px rgba(0,212,255,.5);
  text-transform:uppercase;
}}
.msg-count-badge{{
  font-size:10px;
  color:var(--cyan);
  background:rgba(0,212,255,.06);
  border:1px solid var(--border2);
  padding:4px 10px;
  clip-path:polygon(4px 0%,100% 0%,calc(100% - 4px) 100%,0% 100%);
  white-space:nowrap;
  letter-spacing:.12em;
  font-weight:700;
  text-shadow:0 0 8px rgba(0,212,255,.4);
}}

/* ══ Feed ══ */
.feed{{
  max-width:720px;margin:0 auto;
  padding:14px 10px 90px;
  display:flex;flex-direction:column;gap:4px;
  position:relative;z-index:1;
}}

/* ══ Message ══ */
.message{{
  background:linear-gradient(135deg,rgba(10,22,40,.92) 0%,rgba(6,13,26,.95) 100%);
  border:1px solid var(--border);
  border-right:2px solid rgba(0,212,255,.2);
  border-radius:2px;
  padding:10px 14px 8px;
  position:relative;
  transition:border-color .2s,box-shadow .2s;
  clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px));
}}
.message::before{{
  content:'';
  position:absolute;
  top:0;right:0;
  width:8px;height:8px;
  background:var(--cyan);
  clip-path:polygon(0 0,100% 100%,0 100%);
  opacity:.15;
}}
.message:hover{{
  border-color:var(--border2);
  box-shadow:0 0 16px rgba(0,212,255,.08),inset 0 0 20px rgba(0,212,255,.02);
}}

/* فوروارد */
.fwd-tag{{
  font-size:11.5px;color:var(--accent);
  border-right:2px solid var(--cyan);
  padding:2px 8px 2px 0;
  margin-bottom:7px;
  letter-spacing:.04em;
  opacity:.85;
}}

/* فرستنده */
.msg-sender{{
  font-size:12px;font-weight:700;
  color:var(--cyan);margin-bottom:5px;
  letter-spacing:.06em;
  text-shadow:0 0 6px rgba(0,212,255,.3);
}}

/* ══ Media ══ */
.msg-media{{margin:8px 0 5px}}
.media-link{{display:block}}
.media-img{{
  max-width:100%;max-height:480px;
  border-radius:2px;display:block;
  object-fit:contain;
  background:#030810;
  cursor:zoom-in;
  transition:opacity .2s,filter .2s;
  border:1px solid var(--border);
}}
.media-img:hover{{opacity:.88;filter:brightness(1.05)}}

.media-video{{
  width:100%;max-height:480px;
  border-radius:2px;
  background:#000;display:block;
  border:1px solid var(--border);
}}

.audio-wrap{{
  background:rgba(0,100,200,.06);
  border:1px solid var(--border);
  border-radius:2px;padding:10px 12px;
}}
.audio-name{{
  display:block;font-size:12px;
  color:var(--text-dim);margin-bottom:7px;
  letter-spacing:.04em;
}}
.media-audio{{width:100%;accent-color:var(--cyan)}}

/* Сонда */
.document-wrap{{border-radius:2px;padding:0}}
.doc-card{{
  display:flex;align-items:center;gap:12px;
  padding:10px 14px;
  border:1px solid var(--border);
  background:rgba(0,68,170,.06);
  transition:background .15s,border-color .15s,box-shadow .15s;
  cursor:pointer;
  clip-path:polygon(0 0,calc(100% - 6px) 0,100% 6px,100% 100%,6px 100%,0 calc(100% - 6px));
}}
.doc-card:hover{{
  background:rgba(0,136,238,.1);
  border-color:var(--border2);
  box-shadow:0 0 12px rgba(0,212,255,.08);
}}
.doc-icon{{
  font-size:22px;flex-shrink:0;width:36px;
  text-align:center;color:var(--cyan);
  text-shadow:0 0 8px rgba(0,212,255,.4);
}}
.doc-info{{flex:1;min-width:0;overflow:hidden}}
.doc-name{{
  font-size:13px;font-weight:700;
  color:var(--text);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  letter-spacing:.02em;
}}
.doc-meta{{font-size:11px;color:var(--text-dim);margin-top:2px;letter-spacing:.04em}}
.doc-dl{{font-size:16px;flex-shrink:0;color:var(--cyan);opacity:.6}}
.doc-card:hover .doc-dl{{opacity:1;text-shadow:0 0 6px rgba(0,212,255,.5)}}

/* ══ Text ══ */
.msg-text{{
  font-size:14px;line-height:1.72;
  color:var(--text);
  word-break:break-word;margin-top:3px;
  font-family:-apple-system,'Segoe UI',Tahoma,sans-serif;
}}

/* ══ Footer ══ */
.msg-footer{{
  display:flex;align-items:center;gap:10px;
  margin-top:8px;flex-wrap:wrap;
}}
.msg-date{{font-size:10.5px;color:var(--text-dim);letter-spacing:.06em}}
.views{{font-size:10.5px;color:var(--text-dim);letter-spacing:.04em}}

/* ══ Reactions ══ */
.reactions{{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}}
.react-item{{
  background:rgba(0,136,238,.08);
  border:1px solid var(--border);
  clip-path:polygon(4px 0%,100% 0%,calc(100% - 4px) 100%,0% 100%);
  padding:3px 9px;font-size:13px;
  display:flex;align-items:center;gap:4px;
  cursor:default;
  transition:background .15s,border-color .15s;
}}
.react-item:hover{{
  background:rgba(0,136,238,.15);
  border-color:var(--border2);
}}
.react-count{{font-size:11px;color:var(--text-dim);font-weight:700}}

/* ══ Date Divider ══ */
.date-divider{{
  display:flex;align-items:center;gap:10px;
  margin:16px 0 8px;
}}
.date-divider::before,.date-divider::after{{
  content:'';flex:1;
  height:1px;
  background:linear-gradient(90deg,transparent,var(--cyan),transparent);
  opacity:.15;
}}
.date-divider span{{
  font-size:10px;color:var(--text-dim);
  white-space:nowrap;padding:3px 10px;
  border:1px solid var(--border);
  background:rgba(0,212,255,.04);
  letter-spacing:.08em;
  text-transform:uppercase;
}}

/* ══ Scrollbar ══ */
::-webkit-scrollbar{{width:4px}}
::-webkit-scrollbar-track{{background:var(--bg)}}
::-webkit-scrollbar-thumb{{
  background:var(--blue2);border-radius:0;
  box-shadow:0 0 6px var(--glow);
}}
::-webkit-scrollbar-thumb:hover{{background:var(--accent)}}

/* ══ Responsive ══ */
@media(max-width:520px){{
  .ch-name{{font-size:13px}}
  .msg-text{{font-size:13px}}
  .channel-header{{padding:10px 12px}}
  .feed{{padding:8px 6px 80px}}
  .doc-icon{{font-size:18px;width:28px}}
}}

/* ══ Glitch animation on name ══ */
@keyframes glitch{{
  0%,100%{{clip-path:polygon(0 0,100% 0,100% 35%,0 35%);transform:translate(-2px,0)}}
  20%{{clip-path:polygon(0 65%,100% 65%,100% 80%,0 80%);transform:translate(2px,0)}}
  40%{{clip-path:polygon(0 45%,100% 45%,100% 55%,0 55%);transform:translate(-1px,0)}}
  60%{{clip-path:polygon(0 20%,100% 20%,100% 40%,0 40%);transform:translate(1px,0)}}
  80%{{clip-path:polygon(0 70%,100% 70%,100% 90%,0 90%);transform:translate(-2px,0)}}
}}
</style>
</head>
<body>

<!-- Header -->
<div class="channel-header">
  {avatar_html}
  <div class="ch-info">
    <div class="ch-name">{safe_name}</div>
  </div>
  <div class="msg-count-badge">MSG // {msg_count}</div>
</div>

<!-- Feed -->
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

  // Lightbox
  var overlay = null;
  document.addEventListener('click', function(e) {{
    var img = e.target.closest('.media-img');
    if (img) {{
      e.preventDefault();
      if (!overlay) {{
        overlay = document.createElement('div');
        overlay.style.cssText = [
          'position:fixed;inset:0;',
          'background:rgba(0,0,0,.92);',
          'z-index:9999;',
          'display:flex;align-items:center;justify-content:center;',
          'cursor:zoom-out;',
          'border:1px solid rgba(0,212,255,.15);',
          'backdrop-filter:blur(8px);'
        ].join('');
        overlay.addEventListener('click', function() {{
          overlay.remove();
          overlay = null;
        }});
      }}
      overlay.innerHTML = '';
      var bigImg = document.createElement('img');
      bigImg.src = img.src;
      bigImg.style.cssText = 'max-width:96vw;max-height:96vh;object-fit:contain;border:1px solid rgba(0,212,255,.2);box-shadow:0 0 40px rgba(0,212,255,.15);';
      overlay.appendChild(bigImg);
      document.body.appendChild(overlay);
    }}
  }});
}})();
</script>
</body>
</html>"""
