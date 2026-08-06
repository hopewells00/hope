"""
آپلود فایل به imgurl.ir و استخراج لینک
"""

import os
import re
import logging
import requests

logger = logging.getLogger(__name__)

UPLOAD_URL = "https://imgurl.ir/upload.php"
FIELD_NAME = "userfile[]"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Android 16; Mobile; rv:153.0) Gecko/153.0 Firefox/153.0",
    "Accept": "*/*",
    "Accept-Language": "en-US",
    "Origin": "https://imgurl.ir",
    "Referer": "https://imgurl.ir/",
}


def _get_cookies() -> dict:
    """
    کوکی از env var IMGURL_SESSION می‌خونه (اختیاری).
    اگه تنظیم نشده باشه، آپلود بدون کوکی انجام میشه.
    """
    session_val = os.environ.get("IMGURL_SESSION", "")
    if session_val:
        return {"mmh_user_session": session_val}
    return {}


def upload_file(file_path: str) -> str:
    """
    فایل رو آپلود می‌کنه و URL مستقیم CDN رو برمی‌گردونه.
    مثلاً: https://cdn.imgurl.ir/uploads/p368285_pic.jpg
    """
    cookies = _get_cookies()
    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        files = {FIELD_NAME: (file_name, f)}
        data = {"private_upload": "0"}
        resp = requests.post(
            UPLOAD_URL,
            headers=HEADERS,
            cookies=cookies,
            files=files,
            data=data,
            timeout=60,
        )
    resp.raise_for_status()
    html = resp.text
    logger.debug("imgurl response: %s", html[:500])
    match = re.search(r'https://cdn\.imgurl\.ir/uploads/([^"\s]+)', html)
    if not match:
        raise RuntimeError(f"لینک در پاسخ سرور پیدا نشد. پاسخ:\n{html[:800]}")
    full_url = match.group(0)
    return full_url


def extract_variable(cdn_url: str) -> str:
    """
    قسمت متغیر URL رو استخراج می‌کنه.
    مثلاً از  https://cdn.imgurl.ir/uploads/p368285_pic.jpg
    مقدار p368285_pic برگردانده می‌شه.
    """
    filename = cdn_url.split("/")[-1]      # p368285_pic.jpg
    variable = os.path.splitext(filename)[0]  # p368285_pic
    return variable
