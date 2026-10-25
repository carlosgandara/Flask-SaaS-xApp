from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import select

import test_billing as fixtures
from auth.db import SessionLocal
from billing.errors import ProviderUnavailable, SyncBusy
from billing.inbox import claim, finish
from billing.leases import acquire, fence
from billing.models import OperatorAction, SyncLease, WebhookInbox, WebhookReceipt
from billing.service import has_membership
from billing.worker import process_one, purge_completed_payloads, queue_status, reconcile_all


class WorkerTests(unittest.TestCase):
    setUp = fixtures.BillingTests.setUp
    intent = fixtures.BillingTests.intent
    snapshot = fixtures.BillingTests.snapshot
    signed_stripe = fixtures.BillingTests.signed_stripe
    signed_lemon = fixtures.BillingTests.signed_lemon

    def deliver(self, payload=None):
        raw, headers = self.signed_stripe(obj=payload)
        self.assertEqual(self.client.post('/billing/webhooks/stripe',data=raw,headers=headers).status_code,200)

    def test_notification_is_saved_before_ack_without_provider_api_calls(self):
        with patch.object(self.stripe,'request') as api:
            self.deliver({'id':'sub_1','metadata':{'checkout_ref':'opaque-reference'},
                          'customer_email':'private@example.test','billing_address':{'street':'private'}})
            api.assert_not_called()
        with SessionLocal() as db:
            job=db.query(WebhookInbox).one()
            self.assertEqual(job.status,'pending')
            self.assertNotIn('private',str(job.payload))
            self.assertEqual(db.query(WebhookReceipt).count(),0)

    def test_one_job_is_claimed_once(self):
        self.deliver()
        with self.app.app_context():
            first=claim()
            self.assertIsNotNone(first)
            self.assertIsNone(claim())
            self.assertTrue(finish(first,'done'))
            self.assertFalse(finish(first,'done'))

    def test_expired_worker_claim_is_recovered_and_old_worker_cannot_finish(self):
        self.deliver()
        with self.app.app_context():
            abandoned=claim()
            with SessionLocal.begin() as db:
                db.query(WebhookInbox).update({'lease_until':datetime.utcnow()-timedelta(seconds=1)})
            replacement=claim()
            self.assertEqual(abandoned['id'],replacement['id'])
            self.assertNotEqual(abandoned['token'],replacement['token'])
            self.assertFalse(finish(abandoned,'done'))
            self.assertTrue(finish(replacement,'done'))

    def test_subscription_lease_fences_expired_owner(self):
        with self.app.app_context():
            first=acquire('stripe:test:sub_1')
            with self.assertRaises(SyncBusy): acquire('stripe:test:sub_1')
            with SessionLocal.begin() as db:
                db.query(SyncLease).update({'expires_at':datetime.utcnow()-timedelta(seconds=1)})
            second=acquire('stripe:test:sub_1')
            with SessionLocal.begin() as db:
                with self.assertRaises(SyncBusy): fence(db,'stripe:test:sub_1',first)
                fence(db,'stripe:test:sub_1',second)

    def test_maximum_attempts_move_failed_job_to_review(self):
        self.intent()
        self.deliver()
        with SessionLocal.begin() as db:
            db.query(WebhookInbox).update({'attempts':7})
        with self.app.app_context(),patch.object(self.stripe,'subscription',side_effect=ProviderUnavailable('outage')):
            process_one()
        with SessionLocal() as db:
            row=db.query(WebhookInbox).one()
            self.assertEqual(row.status,'needs_review')
            self.assertEqual(row.attempts,8)

    def test_operator_retry_is_audited_and_does_not_modify_membership_directly(self):
        self.deliver()
        with SessionLocal.begin() as db:
            job=db.query(WebhookInbox).one()
            job.status='needs_review'
            job_id=job.id
        result=self.app.test_cli_runner().invoke(args=['billing-retry',str(job_id),'--actor','operator','--reason','Provider verified; configuration corrected'])
        self.assertEqual(result.exit_code,0,result.output)
        with SessionLocal() as db:
            self.assertEqual(db.get(WebhookInbox,job_id).status,'pending')
            self.assertEqual(db.query(OperatorAction).one().actor,'operator')
        with self.app.app_context(): self.assertFalse(has_membership(1))

    def test_reconciliation_discovers_missing_purchase_as_review_without_email_linking(self):
        with self.app.app_context(),patch.object(self.stripe,'list_subscriptions',return_value=([],None)),patch.object(self.lemon,'list_subscriptions',return_value=([
                {'id':'99','product_id':'456','checkout_ref':None}],None)):
            self.assertEqual(reconcile_all(),1)
            self.assertEqual(reconcile_all(),0)
        with SessionLocal() as db:
            row=db.query(WebhookInbox).one()
            self.assertEqual(row.status,'needs_review')
            self.assertEqual(row.last_error,'unmatched_external_subscription')

    def test_discovered_stripe_purchase_with_recorded_checkout_can_recover(self):
        self.intent()
        with self.app.app_context(),patch.object(self.stripe,'list_subscriptions',return_value=([
                {'id':'sub_1','product_id':'price_monthly','checkout_ref':'opaque-reference'}],None)),patch.object(self.lemon,'list_subscriptions',return_value=([],None)):
            reconcile_all()
        with self.app.app_context(),patch.object(self.stripe,'subscription',return_value=self.snapshot()):
            process_one()
            self.assertTrue(has_membership(1))

    def test_completed_payload_retention_keeps_deduplication_identity(self):
        self.intent()
        self.deliver()
        with self.app.app_context(),patch.object(self.stripe,'subscription',return_value=self.snapshot()):process_one()
        with SessionLocal.begin() as db:
            db.query(WebhookInbox).update({'completed_at':datetime.utcnow()-timedelta(days=31)})
        with self.app.app_context():purge_completed_payloads()
        with SessionLocal() as db:
            self.assertIsNone(db.query(WebhookInbox).one().payload)
            self.assertEqual(db.query(WebhookReceipt).count(),1)
        self.deliver()
        with SessionLocal() as db:self.assertEqual(db.query(WebhookInbox).count(),1)

    def test_status_check_reports_missing_worker_and_old_backlog(self):
        self.deliver()
        with SessionLocal.begin() as db:
            db.query(WebhookInbox).update({'created_at':datetime.utcnow()-timedelta(minutes=10)})
        result=self.app.test_cli_runner().invoke(args=['billing-status','--check'])
        self.assertNotEqual(result.exit_code,0)
        self.assertIn('needs attention',result.output)

    def test_worker_once_runs_without_reconciliation_or_real_network(self):
        self.intent()
        self.deliver()
        with patch.object(self.stripe,'subscription',return_value=self.snapshot()),patch('billing.worker.reconcile_all') as reconciliation:
            result=self.app.test_cli_runner().invoke(args=['billing-worker','--once'])
            reconciliation.assert_not_called()
        self.assertEqual(result.exit_code,0,result.output)
        self.assertIn('Processed one job',result.output)

    def test_foreign_mode_jobs_are_not_claimed(self):
        self.deliver()
        with SessionLocal.begin() as db:db.query(WebhookInbox).update({'mode':'live'})
        with self.app.app_context():self.assertIsNone(claim())
