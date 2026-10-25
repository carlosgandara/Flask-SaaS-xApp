"""Real database concurrency checks in an isolated, randomly named schema.

Set TEST_POSTGRES_URL only to a dedicated test database. No public tables are used.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import os
import re
import secrets
import threading
import unittest
from unittest.mock import patch

import bcrypt
from sqlalchemy import create_engine, delete, select, text

import test_billing as fixtures
from app import create_app
from auth.db import ActionToken, Base, SessionLocal, User
from auth.errors import AuthError
from auth.service import login_account, request_action, reset_account_password, rotate_session
from billing.errors import BillingConflict, SyncBusy
from billing.inbox import claim
from billing.models import CheckoutIntent, Subscription, SyncLease, WebhookInbox
from billing.providers.base import Event
from billing.service import create_checkout, process_event
from database_migrations import upgrade_database


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'Set TEST_POSTGRES_URL for isolated PostgreSQL concurrency tests')
class PostgreSQLTests(unittest.TestCase):
    intent = fixtures.BillingTests.intent
    snapshot = fixtures.BillingTests.snapshot
    signed_stripe = fixtures.BillingTests.signed_stripe

    @classmethod
    def setUpClass(cls):
        cls.schema='saas_test_'+secrets.token_hex(12)
        assert re.fullmatch(r'saas_test_[0-9a-f]{24}',cls.schema)
        cls.admin=create_engine(os.environ['TEST_POSTGRES_URL'],hide_parameters=True)
        with cls.admin.begin() as db:
            db.execute(text('CREATE SCHEMA '+cls.admin.dialect.identifier_preparer.quote(cls.schema)))
        cls.pg=create_engine(os.environ['TEST_POSTGRES_URL'],hide_parameters=True,
            connect_args={'options':'-csearch_path='+cls.schema},pool_size=8)
        upgrade_database(cls.pg)

    @classmethod
    def tearDownClass(cls):
        SessionLocal.configure(bind=fixtures.engine)
        cls.pg.dispose()
        assert re.fullmatch(r'saas_test_[0-9a-f]{24}',cls.schema)
        with cls.admin.begin() as db:
            db.execute(text('DROP SCHEMA '+cls.admin.dialect.identifier_preparer.quote(cls.schema)+' CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        SessionLocal.configure(bind=self.pg)
        with self.pg.begin() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(delete(table))
        self.app=create_app({'TESTING':True,'RATELIMIT_ENABLED':False,'BILLING_ENABLED':True,'BILLING_MODE':'test',
            'BILLING_BASE_URL':'http://localhost:5000','STRIPE_SECRET_KEY':'sk_test_'+'s'*32,
            'STRIPE_WEBHOOK_SECRET':'whsec_'+'s'*32,'BILLING_PLANS':{'monthly':{
                'name':'Monthly','description':'Access','price_label':'$10/month','provider':'stripe','product_id':'price_monthly'}}})
        with SessionLocal.begin() as db:
            db.add(User(id=1,email='one@example.test',verified=True,
                password=bcrypt.hashpw(b'StrongPass123!',bcrypt.gensalt()).decode()))
        self.stripe=self.app.extensions['billing_providers']['stripe']

    def tearDown(self):
        SessionLocal.configure(bind=fixtures.engine)

    def concurrent(self, callback, count=2):
        barrier=threading.Barrier(count)
        def run(index):
            with self.app.app_context():
                barrier.wait(timeout=10)
                return callback(index)
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run,range(count)))

    def test_refresh_token_can_only_be_rotated_once(self):
        with self.app.app_context(): _,raw=login_account('one@example.test','StrongPass123!')
        def rotate(_):
            try:rotate_session(raw);return 'success'
            except AuthError:return 'rejected'
        results=self.concurrent(rotate)
        self.assertEqual(results.count('success'),1)
        self.assertEqual(results.count('rejected'),1)

    def test_reset_link_can_only_be_consumed_once(self):
        with self.app.app_context(): raw=request_action('one@example.test','reset')
        def reset(index):
            try:reset_account_password(raw,f'ChangedPass{index}123!');return index
            except AuthError:return None
        results=self.concurrent(reset)
        winners=[value for value in results if value is not None]
        self.assertEqual(len(winners),1)
        with SessionLocal() as db:
            user=db.get(User,1)
            self.assertTrue(bcrypt.checkpw(f'ChangedPass{winners[0]}123!'.encode(),user.password.encode()))

    def test_parallel_failed_logins_do_not_lose_attempt_counts(self):
        def wrong(_):
            try:login_account('one@example.test','WrongPass123!')
            except AuthError:pass
        self.concurrent(wrong,count=5)
        with SessionLocal() as db:self.assertEqual(db.get(User,1).failed_login_attempts,5)

    def test_parallel_checkout_starts_create_one_intent_and_one_provider_post(self):
        def checkout(_):
            try:return create_checkout(1,'monthly')
            except BillingConflict:return 'pending'
        with patch.object(self.stripe,'checkout',return_value='https://checkout.stripe.com/test') as provider:
            results=self.concurrent(checkout)
            self.assertEqual(provider.call_count,1)
        self.assertIn('https://checkout.stripe.com/test',results)
        with SessionLocal() as db:self.assertEqual(db.query(CheckoutIntent).count(),1)

    def test_skip_locked_claims_do_not_duplicate_a_job(self):
        with SessionLocal.begin() as db:
            db.add(WebhookInbox(provider='stripe',mode='test',event_id='evt_one',event_type='reconcile',
                payload={'_internal_event':{'subscription_id':'sub_1'}},status='pending',request_id='test'))
        results=self.concurrent(lambda _:claim())
        self.assertEqual(sum(result is not None for result in results),1)

    def test_provider_call_does_not_hold_a_user_row_or_sync_lease_transaction(self):
        self.intent()
        def snapshot(_):
            with SessionLocal.begin() as db:
                db.execute(select(User).where(User.id==1).with_for_update(nowait=True)).scalar_one()
                db.execute(select(SyncLease).with_for_update(nowait=True)).scalars().all()
            return self.snapshot()
        with self.app.app_context(),patch.object(self.stripe,'subscription',side_effect=snapshot):
            process_event('stripe',Event('evt_one','created','sub_1','opaque-reference'))
        with SessionLocal() as db:self.assertEqual(db.query(Subscription).count(),1)

    def test_replaced_lease_owner_cannot_commit_a_stale_snapshot(self):
        self.intent()
        def replace(_):
            with SessionLocal.begin() as db:
                db.query(SyncLease).update({'token':'new-owner'})
            return self.snapshot()
        with self.app.app_context(),patch.object(self.stripe,'subscription',side_effect=replace):
            with self.assertRaises(SyncBusy):process_event('stripe',Event('evt_one','created','sub_1','opaque-reference'))
        with SessionLocal() as db:self.assertEqual(db.query(Subscription).count(),0)


@unittest.skipUnless(os.getenv('TEST_REDIS_URL'), 'Set TEST_REDIS_URL for shared rate-limit tests')
class SharedRateLimitTests(unittest.TestCase):
    def test_separate_app_instances_share_limits(self):
        from auth.routes import limiter
        import redis
        prefix='saas-test-'+secrets.token_hex(12)
        configuration={'TESTING':True,'RATELIMIT_ENABLED':True,'BILLING_ENABLED':False,
                       'RATELIMIT_STORAGE_URI':os.environ['TEST_REDIS_URL'],'RATELIMIT_KEY_PREFIX':prefix}
        apps=[create_app(configuration) for _ in range(2)]
        for app in apps:
            @app.get('/shared-limit-test')
            @limiter.limit('2 per minute')
            def shared_limit_test():return {'ok':True}
        try:
            self.assertEqual(apps[0].test_client().get('/shared-limit-test').status_code,200)
            self.assertEqual(apps[1].test_client().get('/shared-limit-test').status_code,200)
            self.assertEqual(apps[0].test_client().get('/shared-limit-test').status_code,429)
        finally:
            client=redis.Redis.from_url(os.environ['TEST_REDIS_URL'])
            for key in client.scan_iter(match='*'+prefix+'*'):
                client.delete(key)
