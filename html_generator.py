"""
تولید فایل HTML شبیه تلگرام از پیام‌های کانال
"""

import base64
import html
import os
from datetime import datetime
from typing import Optional


def _b64(path: str) -> str:
    """فایل رو به base64 تبدیل می‌کنه."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".ogg": "audio/ogg", ".oga": "audio/ogg",
        ".mp3": "audio/mpeg",
    }.get(ext, "application/octet-stream")


def _format_date(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%H:%M · %d %b %Y")
    return str(dt)


def _render_media(media_path: Optional[str]) -> str:
    """رسانه رو به صورت data URI تبدیل می‌کنه."""
    if not media_path or not os.path.exists(media_path):
        return ""
    mime = _mime(media_path)
    b64 = _b64(media_path)
    data_uri = f"data:{mime};base64,{b64}"
    if mime.startswith("image/"):
        return f'<div class="msg-media"><img src="{data_uri}" loading="lazy" alt=""></div>'
    if mime.startswith("video/"):
        return (
            f'<div class="msg-media">'
            f'<video controls preload="metadata" src="{data_uri}"></video>'
            f'</div>'
        )
    if mime.startswith("audio/"):
        return (
            f'<div class="msg-media audio-wrap">'
            f'<audio controls src="{data_uri}"></audio>'
            f'</div>'
        )
    return ""


def _render_message(msg: dict, index: int) -> str:
    """یک پیام رو به HTML تبدیل می‌کنه."""
    media_html = _render_media(msg.get("media_path"))
    text = html.escape(msg.get("text", "") or "")
    text_html = f'<div class="msg-text">{text.replace(chr(10), "<br>")}</div>' if text else ""
    date_str = _format_date(msg.get("date"))
    sender = html.escape(msg.get("sender", "") or "")
    sender_html = f'<div class="msg-sender">{sender}</div>' if sender else ""
    reactions_html = ""
    reactions = msg.get("reactions", [])
    if reactions:
        items = "".join(
            f'<span class="react-item">{html.escape(r["emoji"])} <span class="react-count">{r["count"]}</span></span>'
            for r in reactions
        )
        reactions_html = f'<div class="reactions">{items}</div>'

    views = msg.get("views", 0)
    views_html = f'<span class="views">👁 {views:,}</span>' if views else ""

    fwd_html = ""
    fwd_from = msg.get("fwd_from")
    if fwd_from:
        fwd_html = f'<div class="fwd-tag">↪️ Forwarded from <b>{html.escape(fwd_from)}</b></div>'

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
    لیستی از پیام‌ها رو می‌گیره و یه HTML کامل و standalone برمی‌گردونه.
    هر پیام یه dict هست با کلیدهای:
      text, date, media_path, sender, views, reactions, fwd_from
    """
    avatar_html = ""
    if channel_avatar_path and os.path.exists(channel_avatar_path):
        mime = _mime(channel_avatar_path)
        b64 = _b64(channel_avatar_path)
        avatar_html = f'<img class="ch-avatar" src="data:{mime};base64,{b64}" alt="">'

    msgs_html = "".join(
        _render_message(m, i) for i, m in enumerate(messages)
    )

    safe_name = html.escape(channel_name or "")
    safe_bio = html.escape(channel_bio or "").replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_name}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  font-family:-apple-system,'Segoe UI',Tahoma,sans-serif;
  background:#0e1621;color:#d1d5db;
  min-height:100vh;
}}
.channel-header{{
  position:sticky;top:0;z-index:100;
  background:linear-gradient(135deg,#1c2b3a,#17212b);
  border-bottom:1px solid #2b3a4a;
  padding:14px 16px;
  display:flex;align-items:center;gap:14px;
  backdrop-filter:blur(8px);
}}
.ch-avatar{{
  width:50px;height:50px;border-radius:50%;
  object-fit:cover;
  border:2px solid #2f9fe0;
  flex-shrink:0;
}}
.ch-avatar-placeholder{{
  width:50px;height:50px;border-radius:50%;
  background:linear-gradient(135deg,#2f9fe0,#1565c0);
  display:flex;align-items:center;justify-content:center;
  font-size:20px;font-weight:700;color:#fff;
  flex-shrink:0;
}}
.ch-info{{flex:1;min-width:0}}
.ch-name{{font-size:17px;font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ch-bio{{font-size:13px;color:#8ea0b4;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.msg-count-badge{{
  font-size:11px;color:#5e8db3;background:#1c2e40;
  padding:3px 10px;border-radius:20px;white-space:nowrap;
}}
.feed{{
  max-width:680px;margin:0 auto;
  padding:12px 8px 60px;
  display:flex;flex-direction:column;gap:4px;
}}
.message{{
  background:linear-gradient(135deg,#1e2e3e,#192330);
  border-radius:12px;
  padding:10px 14px 8px;
  position:relative;
  border:1px solid rgba(47,159,224,0.08);
  transition:background .15s;
}}
.message:hover{{background:linear-gradient(135deg,#223347,#1c2838)}}
.fwd-tag{{
  font-size:12px;color:#5fb8e8;
  border-right:3px solid #2f9fe0;
  padding-right:8px;margin-bottom:6px;
}}
.msg-sender{{font-size:13px;font-weight:600;color:#5fb8e8;margin-bottom:4px}}
.msg-media{{margin:6px 0}}
.msg-media img,.msg-media video{{
  max-width:100%;max-height:480px;
  border-radius:8px;display:block;
  object-fit:contain;
}}
.msg-media video{{background:#000;width:100%}}
.audio-wrap audio{{width:100%;margin:4px 0}}
.msg-text{{font-size:15px;line-height:1.6;color:#d1d5db;word-break:break-word}}
.msg-footer{{
  display:flex;align-items:center;gap:10px;
  margin-top:6px;
}}
.msg-date{{font-size:12px;color:#5e6e7e}}
.views{{font-size:12px;color:#5e6e7e}}
.reactions{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}}
.react-item{{
  background:#1c3450;border:1px solid #2b4a6a;
  border-radius:20px;padding:3px 10px;
  font-size:14px;display:flex;align-items:center;gap:4px;
}}
.react-count{{font-size:12px;color:#8ea0b4}}
::-webkit-scrollbar{{width:6px}}
::-webkit-scrollbar-track{{background:#0e1621}}
::-webkit-scrollbar-thumb{{background:#2b3a4a;border-radius:3px}}
@media(max-width:500px){{
  .ch-name{{font-size:15px}}
  .msg-text{{font-size:14px}}
}}
</style>
</head>
<body>

<div class="channel-header">
  {'<div class="ch-avatar-placeholder">' + safe_name[:1] + '</div>' if not channel_avatar_path or not os.path.exists(str(channel_avatar_path)) else avatar_html}
  <div class="ch-info">
    <div class="ch-name">{safe_name}</div>
    <div class="ch-bio">{safe_bio}</div>
  </div>
  <div class="msg-count-badge">{msg_count} پیام</div>
</div>

<div class="feed">
{msgs_html}
</div>

<script>
// smooth scroll to last message on load
window.addEventListener('load',()=>{{
  const msgs=document.querySelectorAll('.message');
  if(msgs.length) msgs[msgs.length-1].scrollIntoView({{behavior:'smooth',block:'end'}});
}});
</script>
</body>
</html>"""
