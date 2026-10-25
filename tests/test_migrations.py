import unittest

from sqlalchemy import Column, MetaData, Table, create_engine, inspect, text

import test_billing as fixtures
from database_migrations import upgrade_database
from migrations.schema_v1 import metadata


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite:///:memory:')

    def tearDown(self):
        self.engine.dispose()

    def test_new_database_upgrade_is_idempotent(self):
        upgrade_database(self.engine)
        upgrade_database(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT version_num FROM alembic_version')).scalar(),'0002_learning')
        for name in ('auth_sessions','auth_action_tokens','billing_webhook_inbox','billing_sync_leases','learning_content','learning_progress'):
            self.assertTrue(inspect(self.engine).has_table(name))

    def legacy_database(self, duplicate=False):
        old=MetaData()
        for name in ('users','refresh_tokens'):
            original=metadata.tables[name]
            Table(name,old,*[Column(column.name,column.type,primary_key=column.primary_key,nullable=column.nullable)
                             for column in original.columns if column.name!='session_id'])
        old.create_all(self.engine)
        with self.engine.begin() as db:
            db.execute(text("INSERT INTO users (id,email,password,verified) VALUES (1,' First@Example.TEST ','existing-hash',1)"))
            if duplicate:
                db.execute(text("INSERT INTO users (id,email,password,verified) VALUES (2,'first@example.test','other-hash',1)"))

    def test_existing_accounts_survive_and_new_columns_are_added(self):
        self.legacy_database()
        upgrade_database(self.engine)
        with self.engine.connect() as db:
            row=db.execute(text('SELECT email,password,verified FROM users WHERE id=1')).one()
            self.assertEqual(row.email,'first@example.test')
            self.assertEqual(row.password,'existing-hash')
            self.assertEqual(row.verified,1)
        self.assertIn('session_id',{c['name'] for c in inspect(self.engine).get_columns('refresh_tokens')})

    def test_conflicting_email_identities_are_not_silently_merged(self):
        self.legacy_database(duplicate=True)
        with self.assertRaisesRegex(ValueError,'Duplicate normalized email'):
            upgrade_database(self.engine)
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text('SELECT count(*) FROM users')).scalar(),2)
