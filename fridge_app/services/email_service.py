from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from .settings_service import get_int_setting, get_setting, parse_emails


def send_digest_email(subject: str, body: str) -> tuple[bool, str]:
    """
    Sends email via SMTP settings stored in settings table.
    Supports env override for password: FRIDGE_SMTP_PASSWORD.
    """
    host = get_setting("smtp_host", "smtp.qq.com").strip() or "smtp.qq.com"
    port = get_int_setting("smtp_port", 465)
    use_ssl = get_setting("smtp_ssl", "1").strip() != "0"
    user = get_setting("smtp_user", "").strip()
    password = os.getenv("FRIDGE_SMTP_PASSWORD") or get_setting("smtp_password", "")
    to_raw = get_setting("smtp_to", "").strip()
    to_emails = parse_emails(to_raw)

    if not user or not password or not to_emails:
        return False, "SMTP 未配置完整（需要 smtp_user、smtp_password/环境变量、smtp_to）。"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, password)
                s.send_message(msg)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)

