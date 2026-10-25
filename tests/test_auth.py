"""Authentication regressions. Synthetic accounts and isolated databases only."""

from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

import bcrypt
from sqlalchemy import select

import test_billing as fixtures
from auth.db import ActionToken, AuthSession, RefreshToken, SessionLocal, User
from auth.errors import AuthError
from auth.service import login_account, register_account, request_action, reset_account_password, rotate_session, verify_account
from auth.validation import normalize_email, password_error, token_hash


class AuthTests(unittest.TestCase):
    setUp = fixtures.BillingTests.setUp

    def post(self, path, data=None, headers=None):
        return self.client.post(path, json=data or {}, headers=self.headers if headers is None else headers)

    def set_password(self, email='one@example.test'):
        with SessionLocal.begin() as db:
            user = db.execute(select(User).where(User.email == email)).scalar_one()
            user.password = bcrypt.hashpw(b'StrongPass123!', bcrypt.gensalt()).decode()

    def test_password_byte_limit_and_unicode(self):
        self.assertIsNone(password_error('Aa1!' + 'x' * 68))
        self.assertIsNotNone(password_error('Aa1!' + 'x' * 69))
        self.assertIsNone(password_error('Aa1!' + 'é' * 34))
        self.assertIsNotNone(password_error('Aa1!' + 'é' * 35))
        self.assertEqual(self.post('/register', {'email':'new@example.test','password':'Aa1!'+'x'*69}).status_code, 400)

    def test_email_validation_and_normalization(self):
        with self.app.app_context():
            self.assertEqual(normalize_email('  FIRST@Example.TEST  '), 'first@example.test')
            for value in ('not-an-email', '', ['email'], 'x\r\nBcc: user@example.test'):
                with self.assertRaises(ValueError):
                    normalize_email(value)

    def test_register_verify_login_and_reject_reused_link(self):
        captured = []
        with patch('auth.routes.send_email', side_effect=lambda to, subject, body, **kwargs: captured.append(body)):
            response = self.post('/register', {'email':' New@Example.TEST ', 'password':'StrongPass123!'})
        self.assertEqual(response.status_code, 200)
        raw = captured[0].split('?token=')[1]
        with SessionLocal() as db:
            action = db.query(ActionToken).one()
            self.assertEqual(action.token_hash, token_hash(raw))
            self.assertNotIn(raw, action.token_hash)
        self.assertEqual(self.post('/login', {'email':'new@example.test','password':'StrongPass123!'}).status_code, 403)
        self.assertEqual(self.client.get('/verify-email?token='+raw).status_code, 200)
        self.assertEqual(self.client.get('/verify-email?token='+raw).status_code, 400)
        self.assertEqual(self.post('/login', {'email':'NEW@example.test','password':'StrongPass123!'}).status_code, 200)
        self.assertEqual(self.client.get('/protected').status_code, 200)

    def test_action_emails_include_button_and_plain_text_fallback(self):
        from auth.routes import notify
        for purpose, label, expiry in [('verify','Verify your email','24 hours'),('reset','Reset your password','15 minutes')]:
            with self.app.app_context(),patch('auth.routes.send_email') as send:
                notify('one@example.test','test-token-value',purpose)
            args,kwargs=send.call_args
            self.assertIn('?token=test-token-value',args[2])
            self.assertIn('>'+label+'</a>',kwargs['html'])
            self.assertIn(expiry,kwargs['html'])
            self.assertIn('copy and paste',kwargs['html'])

    def test_signup_duplicate_identity_is_generic(self):
        with self.app.app_context():
            self.assertIsNone(register_account('one@example.test', 'StrongPass123!'))
        with SessionLocal() as db:
            self.assertEqual(db.query(User).count(), 3)

    def test_action_links_expire_and_resend_invalidates_previous(self):
        with self.app.app_context():
            first = request_action('unverified@example.test', 'verify')
            second = request_action('unverified@example.test', 'verify')
            with self.assertRaises(AuthError):
                verify_account(first)
            with SessionLocal.begin() as db:
                db.query(ActionToken).filter_by(token_hash=token_hash(second)).update({'expires_at':datetime.utcnow()-timedelta(seconds=1)})
            with self.assertRaises(AuthError):
                verify_account(second)

    def test_reset_consumed_once_and_all_access_and_refresh_sessions_revoked(self):
        self.set_password()
        with self.app.app_context():
            access, raw = login_account('one@example.test', 'StrongPass123!')
            other_access, other_raw = login_account('one@example.test', 'StrongPass123!')
            reset = request_action('one@example.test', 'reset')
            reset_account_password(reset, 'NewStrongPass123!')
            with self.assertRaises(AuthError):
                reset_account_password(reset, 'AnotherPass123!')
            for cookie in (raw, other_raw):
                with self.assertRaises(AuthError):
                    rotate_session(cookie)
        for token in (access, other_access):
            self.client.set_cookie('access_token', token)
            self.assertEqual(self.client.get('/protected').status_code, 401)
        self.assertEqual(self.post('/login', {'email':'one@example.test','password':'NewStrongPass123!'}).status_code, 200)

    def test_refresh_rotates_and_replay_revokes_descendants(self):
        self.set_password()
        with self.app.app_context():
            access, first = login_account('one@example.test', 'StrongPass123!')
            refreshed, second = rotate_session(first)
            self.assertNotEqual(first, second)
            with self.assertRaises(AuthError):
                rotate_session(first)
            with self.assertRaises(AuthError):
                rotate_session(second)
        self.client.set_cookie('access_token', refreshed)
        self.assertEqual(self.client.get('/protected').status_code, 401)

    def test_logout_revokes_current_session_without_other_device(self):
        self.set_password()
        with self.app.app_context():
            first, refresh = login_account('one@example.test', 'StrongPass123!')
            other, _ = login_account('one@example.test', 'StrongPass123!')
        self.client.set_cookie('access_token', first)
        self.client.set_cookie('refresh_token', refresh)
        self.assertEqual(self.post('/logout').status_code, 200)
        self.client.set_cookie('access_token', first)
        self.assertEqual(self.client.get('/protected').status_code, 401)
        self.client.set_cookie('access_token', other)
        self.assertEqual(self.client.get('/protected').status_code, 200)

    def test_logout_all_revokes_existing_access_tokens(self):
        self.set_password()
        with self.app.app_context():
            access, raw = login_account('one@example.test','StrongPass123!')
        self.client.set_cookie('access_token', access)
        self.assertEqual(self.post('/logout-all').status_code, 200)
        self.client.set_cookie('access_token', access)
        self.assertEqual(self.client.get('/protected').status_code, 401)

    def test_auth_mutations_require_csrf_and_exact_origin(self):
        for path in ('/login','/register','/refresh','/logout','/logout-all','/forgot-password','/reset-password','/resend-verification'):
            self.assertEqual(self.post(path, headers={}).status_code, 403, path)
            self.assertEqual(self.post(path, headers={**self.headers, 'Origin':'https://evil.invalid'}).status_code, 403, path)

    def test_csrf_endpoint_returns_no_secrets_or_cross_origin_permission(self):
        response = self.client.get('/auth/csrf')
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrf_token', response.get_json())
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_login_attempt_count_commits_and_locks_account(self):
        self.set_password()
        with self.app.app_context():
            for _ in range(5):
                with self.assertRaises(AuthError):
                    login_account('one@example.test','WrongPass123!')
            with self.assertRaises(AuthError):
                login_account('one@example.test','StrongPass123!')
        with SessionLocal() as db:
            user = db.query(User).filter_by(email='one@example.test').one()
            self.assertEqual(user.failed_login_attempts, 5)
            self.assertGreater(user.locked_until, datetime.utcnow())

    def test_expired_lockout_starts_a_new_attempt_window(self):
        self.set_password()
        with SessionLocal.begin() as db:
            user=db.get(User,1)
            user.failed_login_attempts=5
            user.locked_until=datetime.utcnow()-timedelta(seconds=1)
        with self.app.app_context(),self.assertRaises(AuthError):
            login_account('one@example.test','WrongPass123!')
        with SessionLocal() as db:
            self.assertEqual(db.get(User,1).failed_login_attempts,1)
            self.assertIsNone(db.get(User,1).locked_until)

    def test_html_pages_recover_expired_session_without_leaking_data(self):
        self.client.delete_cookie('access_token')
        for path in ('/dashboard','/profile'):
            response=self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'id="auth-session-recovery"',response.data)
            self.assertNotIn(b'one@example.test',response.data)

    def test_revoked_session_cannot_be_used_on_billing_api(self):
        self.assertEqual(self.post('/logout-all').status_code, 200)
        self.assertEqual(self.client.get('/billing/api/membership').status_code, 401)

    def test_smtp_failure_is_safe_and_verification_can_be_resent(self):
        with patch('auth.routes.send_email', side_effect=RuntimeError('sensitive provider details')):
            response=self.post('/register',{'email':'resend@example.test','password':'StrongPass123!'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b'sensitive',response.data)
        with patch('auth.routes.send_email') as send:
            self.assertEqual(self.post('/resend-verification',{'email':'resend@example.test'}).status_code,200)
            send.assert_called_once()

    def test_expired_session_is_not_extended_by_refresh(self):
        self.set_password()
        with self.app.app_context():
            _,raw=login_account('one@example.test','StrongPass123!')
            with SessionLocal.begin() as db:
                db.query(AuthSession).update({'expires_at':datetime.utcnow()-timedelta(seconds=1)})
            with self.assertRaises(AuthError):
                rotate_session(raw)
