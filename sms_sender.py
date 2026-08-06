"""
ارسال پیامک از طریق sms.ir
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)


def send_sms(text: str) -> tuple:
    """
    پیام رو از طریق sms.ir به شماره TARGET_PHONE ارسال می‌کنه.
    env vars: SMS_API_KEY, SMS_LINE_NUMBER, TARGET_PHONE
    """
    api_key = os.environ["SMS_API_KEY"]
    line_number = os.environ["SMS_LINE_NUMBER"]
    target_phone = os.environ["TARGET_PHONE"]

    url = "https://api.sms.ir/v1/send/bulk"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": api_key,
    }
    payload = {
        "lineNumber": line_number,
        "messageText": text,
        "mobiles": [target_phone],
        "sendDateTime": None,
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        logger.info("SMS result: %s %s", r.status_code, data)
        return r.status_code, data
    except Exception as e:
        logger.error("SMS error: %s", e)
        return None, {"error": str(e)}
