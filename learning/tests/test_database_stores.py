"""Adapter behavior on disposable SQLite; no Neon credentials or services used."""
import unittest
from copy import deepcopy
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy.exc import IntegrityError
from learning import create_app, PACKAGE_DIR
from learning.storage import JsonContentStore
from learning.storage.contracts import StorageConflictError, StorageAccessError
from learning.storage.sqlalchemy_store import define_tables, SqlAlchemyContentStore, SqlAlchemyProgressStore, import_content
from learning.storage.json_progress import ProgressStorageError


class DatabaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite://",hide_parameters=True)
        @event.listens_for(self.engine,"connect")
        def foreign_keys(connection,record):connection.execute("PRAGMA foreign_keys=ON")
        self.metadata=MetaData()
        self.users=Table("users",self.metadata,Column("id",Integer,primary_key=True))
        self.content_table,self.progress_table=define_tables(self.metadata)
        self.content=SqlAlchemyContentStore(self.engine,self.content_table)
        self.progress=SqlAlchemyProgressStore(self.engine,self.progress_table)

    def tearDown(self):self.engine.dispose()

    def prepare(self):
        self.metadata.create_all(self.engine)
        with self.engine.begin() as connection:connection.execute(self.users.insert(),[{"id":1},{"id":2}])

    def test_no_implicit_schema_or_network_side_effects(self):
        self.assertEqual(inspect(self.engine).get_table_names(),[])
        ddl=str(CreateTable(self.progress_table).compile(dialect=postgresql.dialect()))
        self.assertIn("JSONB",ddl)
        self.assertIn("FOREIGN KEY(user_id) REFERENCES users (id)",ddl)

    def test_content_import_round_trip_and_rollback(self):
        self.prepare()
        source=JsonContentStore(PACKAGE_DIR/"data")
        import_content(self.engine,self.content_table,source)
        self.assertEqual(self.content.list_languages(),source.list_languages())
        self.assertEqual(self.content.get_concepts(),source.get_concepts())
        self.assertEqual(self.content.get_curriculum(),source.get_curriculum())
        for language in source.list_languages():
            self.assertEqual(self.content.get_vocabulary(language),source.get_vocabulary(language))
            self.assertEqual(self.content.get_levels(language),source.get_levels(language))
        with self.assertRaises(IntegrityError):import_content(self.engine,self.content_table,source)
        self.assertEqual(self.content.get_concepts(),source.get_concepts())

    def test_progress_isolation_revision_and_stale_save(self):
        self.prepare()
        original=self.progress.load(1,"tl",{"score":0})
        stale=deepcopy(original)
        original["score"]=16
        self.progress.save(1,"tl",original)
        self.assertEqual(original["_storage_revision"],1)
        with self.assertRaises(StorageConflictError):self.progress.save(1,"tl",stale)
        first=self.progress.load(1,"tl",{})
        second=self.progress.load(1,"tl",{})
        first["score"]=20
        self.progress.save(1,"tl",first)
        with self.assertRaises(StorageConflictError):self.progress.save(1,"tl",second)
        self.assertEqual(self.progress.load(1,"tl",{})["score"],20)
        self.assertEqual(self.progress.load(2,"tl",{})["_storage_revision"],0)
        self.assertEqual(self.progress.load(1,"es",{})["_storage_revision"],0)
        with self.engine.connect() as connection:
            payload=connection.execute(select(self.progress_table.c.payload)).scalar_one()
            self.assertNotIn("_storage_revision",payload)

    def test_requires_valid_user_and_reports_missing_schema(self):
        with self.assertRaises(ValueError):self.progress.load("local-user","tl",{})
        with self.assertRaises(ProgressStorageError):self.progress.load(1,"tl",{})
        with self.assertRaises(StorageAccessError):self.content.get_concepts()
        self.prepare()
        with self.assertRaises(ProgressStorageError):self.progress.save(999,"tl",{})

    def test_app_runs_with_database_content_and_progress(self):
        self.prepare()
        import_content(self.engine,self.content_table,JsonContentStore(PACKAGE_DIR/"data"))
        app=create_app({"TESTING":True,"LEARNING_CONTENT_STORE":self.content,
                        "LEARNING_PROGRESS_STORE":self.progress,"LEARNING_LEARNER_ID_RESOLVER":lambda:1})
        client=app.test_client()
        self.assertEqual(client.get("/menu").status_code,200)
        self.assertEqual(client.post("/word-match/start").status_code,200)
        self.assertIn("word_match",self.progress.load(1,"tl",{})["games"])
        self.assertEqual(client.get("/learn/level-1-cafe").status_code,200)


if __name__=="__main__":unittest.main()
