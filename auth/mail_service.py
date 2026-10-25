# /auth/mail_service.py

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .config import (
    EMAIL_HOST,
    EMAIL_PORT,
    EMAIL_USER,
    EMAIL_PASS,
    EMAIL_FROM,
    EMAIL_DISPLAY_NAME,
)


def send_email(to, subject, text, html=None):
    from_formatted = f"{EMAIL_DISPLAY_NAME} <{EMAIL_FROM}>"

    message = MIMEMultipart("alternative")
    message["From"] = from_formatted
    message["To"] = to
    message["Subject"] = subject

    message.attach(MIMEText(text, "plain", "utf-8"))

    if html:
        message.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=10) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(
            EMAIL_FROM,
            [to],
            message.as_string(),
        )
