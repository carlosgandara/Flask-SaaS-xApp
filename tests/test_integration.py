"""Integrated account, membership, CSRF and progress behavior on isolated SQLite."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy import select
import test_billing as fixtures
from auth.db import engine
from integration import progress_table
from learning.services.progress import progress_lock


class IntegrationTests(unittest.TestCase):
    setUp = fixtures.BillingTests.setUp

    def test_lesson_page_holds_lock_before_reading_progress(self):
        self.app.config['BILLING_ENABLED'] = False
        store = self.app.config['LEARNING_PROGRESS_STORE']
        original_load = store.load
        locked = []

        def competing_writer():
            acquired = progress_lock.acquire(blocking=False)
            if acquired:
                progress_lock.release()
            return acquired

        def checked_load(*args):
            with ThreadPoolExecutor(max_workers=1) as pool:
                locked.append(not pool.submit(competing_writer).result(timeout=2))
            return original_load(*args)

        with patch.object(store, 'load', side_effect=checked_load):
            response = self.client.get('/level/level-1-cafe?restart=1')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(locked and all(locked))

    def test_reset_preserves_database_revision_and_study_time(self):
        self.app.config['BILLING_ENABLED'] = False
        self.assertEqual(self.client.post('/study-time', json={'seconds':12}, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/reset').status_code, 302)
        self.assertEqual(self.client.get('/study-time').json['total_seconds'], 12)

    def test_menu_reads_progress_once_for_all_lesson_cards(self):
        store = self.app.config['LEARNING_PROGRESS_STORE']
        with patch('integration.has_membership', return_value=True), patch.object(store, 'load', wraps=store.load) as reads:
            response = self.client.get('/menu', headers={'Accept':'text/html'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(reads.call_count, 1)

    def test_membership_gates_pages_and_writes(self):
        self.assertEqual(self.client.get('/menu', headers={'Accept':'text/html'}).status_code, 302)
        self.assertEqual(self.client.post('/study-time', json={'seconds':10}, headers=self.headers).status_code, 403)
        with patch('integration.has_membership', return_value=True):
            response = self.client.get('/menu', headers={'Accept':'text/html'})
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'nonce=', response.data)
            self.assertIn("'nonce-", response.headers['Content-Security-Policy'])

    def test_progress_uses_account_identity_and_requires_csrf(self):
        self.app.config['BILLING_ENABLED'] = False
        self.assertEqual(self.client.post('/study-time', json={'seconds':10}).status_code, 403)
        response = self.client.post('/study-time', json={'seconds':10,'user_id':2}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        with engine.connect() as db:
            rows = db.execute(select(progress_table)).mappings().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user_id'], 1)
        self.client.set_cookie('access_token', fixtures.generate_jwt('two@example.test'))
        self.assertEqual(self.client.get('/study-time').json['total_seconds'], 0)

    def test_expired_sessions_recover_and_apis_return_401(self):
        self.client.delete_cookie('access_token')
        response = self.client.get('/menu', headers={'Accept':'text/html'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/login')
        self.client.set_cookie('refresh_token', 'expired-session-placeholder')
        response = self.client.get('/menu', headers={'Accept':'text/html'})
        self.assertIn(b'auth-session-recovery', response.data)
        self.assertEqual(self.client.get('/study-time').status_code, 401)
        with self.client.get('/learning/static/course.css') as asset:
            self.assertEqual(asset.status_code, 200)
