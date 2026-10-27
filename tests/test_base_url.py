import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from dotenv import load_dotenv
from configuration import configure_base_url


class BaseUrlTests(unittest.TestCase):
    def test_local_and_railway_share_one_origin(self):
        for address, railway in [('http://localhost:5000', False), ('https://despacio.ai', True)]:
            with self.subTest(address=address), patch.dict(os.environ, {
                'BASE_URL': address, 'APP_ENV': 'development',
                'RAILWAY_PROJECT_ID': 'test' if railway else '', 'RAILWAY_ENVIRONMENT_ID': ''
            }):
                app = Flask(__name__)
                configure_base_url(app)
                for key in ['BASE_URL', 'APP_BASE_URL', 'BILLING_BASE_URL']:
                    self.assertEqual(app.config[key], address)

    def test_deployed_environment_wins_over_local_dotenv(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('BASE_URL=http://localhost:5000\n')
            with patch.dict(os.environ, {'BASE_URL': 'https://despacio.ai'}):
                load_dotenv(path, override=False)
                self.assertEqual(os.environ['BASE_URL'], 'https://despacio.ai')

    def test_railway_rejects_missing_or_local_origin(self):
        with patch('configuration.load_dotenv'), patch.dict(os.environ, {
            'RAILWAY_PROJECT_ID': 'test', 'APP_ENV': 'development'
        }):
            for value in ['', 'http://localhost:5000', 'https://localhost']:
                with self.subTest(value=value), patch.dict(os.environ, {'BASE_URL': value}):
                    with self.assertRaises(ValueError):
                        configure_base_url(Flask(__name__))

    def test_reset_and_verification_email_links_use_canonical_origin(self):
        import test_billing
        from auth.routes import notify
        app = test_billing.create_app({'TESTING': True, 'BASE_URL': 'https://despacio.ai'})
        with app.test_request_context(), patch('auth.routes.send_email') as send:
            for purpose, endpoint in [('reset', 'reset-password'), ('verify', 'verify-email')]:
                notify('test@example.test', 'test-token', purpose)
                expected = 'https://despacio.ai/' + endpoint + '?token=test-token'
                self.assertIn(expected, send.call_args.args[2])
                self.assertIn(expected, send.call_args.kwargs['html'])
                self.assertNotIn('localhost', send.call_args.kwargs['html'])
