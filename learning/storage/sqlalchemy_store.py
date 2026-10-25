"""Optional Neon/PostgreSQL adapters using a host-owned SQLAlchemy engine.

Importing/constructing repositories never opens a connection, creates tables or
imports content. The host must apply a migration explicitly before using them.
"""
from copy import deepcopy
from sqlalchemy import Column, ForeignKey, Integer, JSON, String, Table, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from .contracts import StorageAccessError, StorageConflictError
from .json_progress import ProgressStorageError


def define_tables(metadata):
    """Attach definitions to host metadata, which must also contain users.id."""
    if "learning_content" in metadata.tables and "learning_progress" in metadata.tables:
        return metadata.tables["learning_content"],metadata.tables["learning_progress"]
    payload = JSON().with_variant(JSONB(),"postgresql")
    content = Table("learning_content",metadata,
                    Column("kind",String(24),primary_key=True),
                    Column("language",String(16),primary_key=True),
                    Column("payload",payload,nullable=False))
    progress = Table("learning_progress",metadata,
                     Column("user_id",Integer,ForeignKey("users.id"),primary_key=True),
                     Column("language",String(16),primary_key=True),
                     Column("payload",payload,nullable=False),
                     Column("revision",Integer,nullable=False))
    return content,progress


class SqlAlchemyContentStore:
    def __init__(self, engine, table):
        self.engine,self.table = engine,table

    def read(self, kind, language="", default=None):
        try:
            with self.engine.connect() as connection:
                value = connection.execute(select(self.table.c.payload).where(
                    self.table.c.kind==kind,self.table.c.language==language)).scalar_one_or_none()
            return deepcopy(default if value is None else value)
        except SQLAlchemyError as error:
            raise StorageAccessError("read content","database:learning_content",error) from error

    def list_languages(self):
        try:
            with self.engine.connect() as connection:
                return list(connection.execute(select(self.table.c.language).where(
                    self.table.c.kind=="levels").order_by(self.table.c.language)).scalars())
        except SQLAlchemyError as error:
            raise StorageAccessError("list languages","database:learning_content",error) from error

    def get_concepts(self): return self.read("concepts",default={})
    def get_vocabulary(self, language): return self.read("vocabulary",language,{})
    def get_levels(self, language): return self.read("levels",language,{"levels":[]})
    def get_curriculum(self): return self.read("curriculum",default={})


class SqlAlchemyProgressStore:
    def __init__(self, engine, table):
        self.engine,self.table = engine,table

    def key(self, learner_id, language):
        if type(learner_id) is not int or learner_id <= 0:
            raise ValueError("Database progress requires an authenticated integer user ID")
        if not isinstance(language,str) or not language or len(language)>16:
            raise ValueError("Invalid language code")
        return (self.table.c.user_id==learner_id,self.table.c.language==language)

    def load(self, learner_id, language, default):
        key = self.key(learner_id,language)
        try:
            with self.engine.connect() as connection:
                row = connection.execute(select(self.table.c.payload,self.table.c.revision).where(*key)).mappings().one_or_none()
            result = deepcopy(row["payload"] if row else default)
            if not isinstance(result,dict):
                raise ValueError("Progress payload must be an object")
            result["_storage_revision"] = row["revision"] if row else 0
            return result
        except (SQLAlchemyError,ValueError) as error:
            raise ProgressStorageError("database read","database:learning_progress",error) from error

    def save(self, learner_id, language, progress):
        key = self.key(learner_id,language)
        revision = progress.get("_storage_revision",0)
        if type(revision) is not int or revision < 0:
            raise ValueError("Invalid progress revision")
        payload = deepcopy({k:v for k,v in progress.items() if k!="_storage_revision"})
        try:
            with self.engine.begin() as connection:
                if revision == 0:
                    connection.execute(self.table.insert().values(user_id=learner_id,language=language,
                                                                  payload=payload,revision=1))
                else:
                    result = connection.execute(self.table.update().where(*key,self.table.c.revision==revision)
                                                .values(payload=payload,revision=revision+1))
                    if result.rowcount != 1:
                        raise StorageConflictError("A newer progress version exists")
        except IntegrityError as error:
            code = getattr(error.orig,"sqlstate",None) or getattr(error.orig,"pgcode",None)
            name = getattr(error.orig,"sqlite_errorname","")
            sqlite_unique = self.engine.dialect.name == "sqlite" and str(error.orig).startswith("UNIQUE constraint failed: learning_progress.")
            if revision == 0 and (code=="23505" or sqlite_unique or name in {"SQLITE_CONSTRAINT_PRIMARYKEY","SQLITE_CONSTRAINT_UNIQUE"}):
                raise StorageConflictError("Progress was created by another request") from error
            raise ProgressStorageError("database write","database:learning_progress",error) from error
        except SQLAlchemyError as error:
            raise ProgressStorageError("database write","database:learning_progress",error) from error
        progress["_storage_revision"] = revision+1


def import_content(engine, table, source):
    """Explicit one-time import into an empty content table; transaction rolls back on conflict."""
    documents = [{"kind":"concepts","language":"","payload":source.get_concepts()},
                 {"kind":"curriculum","language":"","payload":source.get_curriculum()}]
    for language in source.list_languages():
        documents.extend([{"kind":"levels","language":language,"payload":source.get_levels(language)},
                          {"kind":"vocabulary","language":language,"payload":source.get_vocabulary(language)}])
    with engine.begin() as connection:
        connection.execute(table.insert(),documents)
