import unittest
from unittest.mock import MagicMock, patch
import requests
import test_billing
from auth.mail_service import send_email, EmailDeliveryError


class MailServiceTests(unittest.TestCase):
    def test_https_payload_and_safe_failures(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 201
        with patch('auth.mail_service.requests.post', return_value=response) as post:
            send_email('learner@example.test', 'Reset', 'Reset link', '<p>Reset link</p>')
        self.assertEqual(post.call_args.args, ('https://api.brevo.com/v3/smtp/email',))
        options = post.call_args.kwargs
        self.assertEqual(options['headers']['api-key'], 'test-api-key-not-real')
        self.assertEqual(options['json']['to'], [{'email': 'learner@example.test'}])
        self.assertEqual(options['json']['textContent'], 'Reset link')
        self.assertEqual(options['json']['htmlContent'], '<p>Reset link</p>')
        self.assertEqual(options['timeout'], (3.05, 10))
        self.assertFalse(options['allow_redirects'])
        for status in [301, 400, 401, 403, 429, 500]:
            response.status_code = status
            with self.subTest(status=status), patch('auth.mail_service.requests.post', return_value=response):
                with self.assertRaisesRegex(EmailDeliveryError, f'HTTP {status}'):
                    send_email('learner@example.test', 'Reset', 'secret-token')

    def test_network_error_is_sanitized_and_not_retried(self):
        with patch('auth.mail_service.requests.post', side_effect=requests.Timeout('private-key')) as post:
            with self.assertRaises(EmailDeliveryError) as caught:
                send_email('learner@example.test', 'Reset', 'secret-token')
            self.assertNotIn('private-key', str(caught.exception))
            post.assert_called_once()
