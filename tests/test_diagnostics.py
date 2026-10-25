import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy.exc import OperationalError

import test_billing as fixtures
from app import create_app
from configuration import configure_app
from billing.errors import CheckoutNotCreated
from billing.inbox import minimize
from billing.models import CheckoutIntent
from auth.db import SessionLocal


class DiagnosticsTests(unittest.TestCase):
    setUp = fixtures.BillingTests.setUp

    def test_app_check_is_read_only_and_reports_missing_schema(self):
        with patch('diagnostics.schema_problems',return_value=['auth_sessions']):
            result=self.app.test_cli_runner().invoke(args=['app-check'])
        self.assertNotEqual(result.exit_code,0)
        self.assertIn('db-upgrade',result.output)
        with patch('diagnostics.schema_problems',return_value=[]):
            result=self.app.test_cli_runner().invoke(args=['app-check'])
        self.assertEqual(result.exit_code,0,result.output)

    def test_schema_guard_returns_actionable_error_without_private_data(self):
        self.app.config['SCHEMA_CHECK']=True
        with patch('diagnostics.schema_problems',return_value=['auth_sessions']):
            response=self.client.get('/login')
        self.assertEqual(response.status_code,503)
        self.assertIn('db-upgrade',response.json['error'])
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['X-Request-ID'],response.json['reference'])

    def test_database_and_unexpected_errors_do_not_expose_parameters(self):
        @self.app.get('/simulated-failure/<kind>')
        def failure(kind):
            if kind=='database':raise OperationalError('private SQL',{'password':'private-password'},Exception('private'))
            raise RuntimeError('private-password')
        for kind,status in [('database',503),('unexpected',500)]:
            with self.assertLogs(self.app.logger,level='ERROR') as logs:
                response=self.client.get('/simulated-failure/'+kind)
            self.assertEqual(response.status_code,status)
            self.assertNotIn('private-password',response.get_data(as_text=True))
            self.assertNotIn('private-password',' '.join(logs.output))
            self.assertTrue(response.json['reference'])
        self.assertEqual(self.client.get('/nonexistent').status_code,404)

    def test_billing_check_never_creates_checkout(self):
        with patch.object(self.stripe,'validate_plan') as stripe,patch.object(self.lemon,'validate_plan') as lemon,patch.object(self.lemon,'check_store'),patch.object(self.stripe,'checkout') as checkout:
            result=self.app.test_cli_runner().invoke(args=['billing-check'])
        self.assertEqual(result.exit_code,0,result.output)
        stripe.assert_called_once_with('price_monthly')
        lemon.assert_called_once_with('456')
        checkout.assert_not_called()

    def test_failed_preflight_allows_safe_retry_before_any_payment_post(self):
        with patch.object(self.stripe,'checkout',side_effect=CheckoutNotCreated('Invalid recurring price')):
            response=self.client.post('/billing/checkout',json={'plan':'monthly'},headers=self.headers)
        self.assertEqual(response.status_code,503)
        with SessionLocal() as db:self.assertEqual(db.query(CheckoutIntent).one().state,'failed')
        with patch.object(self.stripe,'checkout',return_value='https://checkout.stripe.com/retry') as checkout:
            response=self.client.post('/billing/checkout',json={'plan':'monthly'},headers=self.headers)
        self.assertEqual(response.status_code,200)
        checkout.assert_called_once()

    def test_expanded_payment_resources_are_minimized_to_identifiers(self):
        _,_,clean=minimize('stripe',{'id':'evt_1','type':'charge.refunded','data':{'object':{
            'id':'ch_1','invoice':{'id':'in_1','email':'private-email','address':'private-address'},
            'payment_intent':{'id':'pi_1','client_secret':'private-secret'}}}})
        self.assertNotIn('private',str(clean))
        self.assertEqual(clean['data']['object']['invoice'],'in_1')

    def test_production_configuration_rejects_unsafe_transport_and_storage(self):
        def app(**overrides):
            item=Flask(__name__)
            item.config.update(APP_ENV='production',APP_BASE_URL='https://example.test',SESSION_COOKIE_SECURE=True,
                               RATELIMIT_STORAGE_URI='redis://localhost:6379/0',**overrides)
            return item
        with patch.dict('os.environ',{'DATABASE_URL':'postgresql://test:test@localhost/test','TRUSTED_PROXY_HOPS':'0'}):
            configure_app(app())
            insecure=app();insecure.config['APP_BASE_URL']='http://example.test'
            with self.assertRaisesRegex(ValueError,'HTTPS'):configure_app(insecure)
            memory=app();memory.config['RATELIMIT_STORAGE_URI']='memory://'
            with self.assertRaisesRegex(ValueError,'Redis'):configure_app(memory)
            bad=app();bad.config['APP_BASE_URL']='https://example.test/path'
            with self.assertRaisesRegex(ValueError,'origin'):configure_app(bad)
