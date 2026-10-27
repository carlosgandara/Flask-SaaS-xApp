# /auth/mail_service.py

import requests

from .config import (
    BREVO_API_KEY,
    EMAIL_FROM,
    EMAIL_DISPLAY_NAME,
)


class EmailDeliveryError(RuntimeError):
    """Safe delivery failure without credentials or reset tokens."""


def send_email(to, subject, text, html=None):
    payload = {'sender': {'email': EMAIL_FROM, 'name': EMAIL_DISPLAY_NAME},
               'to': [{'email': to}], 'subject': subject, 'textContent': text}
    if html:
        payload['htmlContent'] = html
    try:
        with requests.post('https://api.brevo.com/v3/smtp/email',
                           headers={'api-key': BREVO_API_KEY, 'Accept': 'application/json'},
                           json=payload, timeout=(3.05, 10), allow_redirects=False) as response:
            if response.status_code != 201:
                raise EmailDeliveryError(f'Brevo rejected email (HTTP {response.status_code})')
    except requests.RequestException:
        raise EmailDeliveryError('Brevo email connection failed') from None
