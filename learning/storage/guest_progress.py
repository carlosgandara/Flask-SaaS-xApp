"""Temporary guest sessions; account progress stays in the shared SQL database."""
from collections import OrderedDict
from copy import deepcopy
import json
import threading
import time
from datetime import datetime, timedelta
from redis import Redis, WatchError
from sqlalchemy import Table, Column, String, Integer, DateTime, JSON, select, delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from .contracts import StorageConflictError
from .sqlalchemy_store import SqlAlchemyProgressStore
from .json_progress import ProgressStorageError


def guest_expiry():
    return datetime.utcnow() + timedelta(hours=6)


def define_guest_table(metadata):
    if 'learning_guest_progress' in metadata.tables:
        return metadata.tables['learning_guest_progress']
    return Table('learning_guest_progress', metadata,
                 Column('user_id', String(64), primary_key=True),
                 Column('language', String(16), primary_key=True),
                 Column('payload', JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
                 Column('revision', Integer, nullable=False),
                 Column('expires_at', DateTime, nullable=False, default=guest_expiry,
                        onupdate=guest_expiry, index=True))


class DatabaseGuestProgressStore(SqlAlchemyProgressStore):
    """Shared temporary rounds when Redis isn't configured, including multiple workers."""
    def key(self, learner_id, language):
        if not isinstance(learner_id, str) or not learner_id.startswith('guest-') or len(learner_id) > 64:
            raise ValueError('A server-issued guest session is required')
        if not isinstance(language, str) or not language or len(language) > 16:
            raise ValueError('Invalid language code')
        return self.table.c.user_id == learner_id, self.table.c.language == language

    def load(self, learner_id, language, default):
        key = self.key(learner_id, language)
        try:
            with self.engine.begin() as db:
                row = db.execute(select(self.table).where(*key)).mappings().one_or_none()
                if row and row['expires_at'] <= datetime.utcnow():
                    db.execute(delete(self.table).where(*key, self.table.c.expires_at <= datetime.utcnow()))
                    row = db.execute(select(self.table).where(*key)).mappings().one_or_none()
            result = deepcopy(row['payload'] if row else default)
            result['_storage_revision'] = row['revision'] if row else 0
            return result
        except SQLAlchemyError as error:
            raise ProgressStorageError('guest read', 'database:learning_guest_progress', error) from error

    def save(self, learner_id, language, progress):
        first = progress.get('_storage_revision', 0) == 0
        try:
            super().save(learner_id, language, progress)
        except ProgressStorageError as error:
            cause = error.__cause__
            if isinstance(cause, IntegrityError) and self.engine.dialect.name == 'sqlite' and 'UNIQUE constraint failed: learning_guest_progress.' in str(cause.orig):
                raise StorageConflictError('Guest progress changed') from error
            raise
        if first:
            # Bound cleanup work while retiring expired anonymous rounds automatically.
            try:
                with self.engine.begin() as db:
                    expired = select(self.table.c.user_id, self.table.c.language).where(
                        self.table.c.expires_at <= datetime.utcnow()).limit(100)
                    for user, lang in db.execute(expired).all():
                        db.execute(delete(self.table).where(self.table.c.user_id == user,
                            self.table.c.language == lang, self.table.c.expires_at <= datetime.utcnow()))
            except SQLAlchemyError:
                # The game save already committed. A cleanup failure must not invite a replay.
                pass


class GuestProgressStore:
    def __init__(self, redis_url=None, ttl=21600, capacity=2000):
        self.redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
        self.ttl, self.capacity = ttl, capacity
        self.rows = OrderedDict()
        self.lock = threading.RLock()

    def key(self, learner, language):
        if not isinstance(learner, str) or not learner.startswith("guest-"):
            raise ValueError("A server-issued guest session is required")
        return f"despacio:guest:{learner}:{language}"

    def load(self, learner, language, default):
        key = self.key(learner, language)
        if self.redis:
            raw = self.redis.get(key)
            return json.loads(raw) if raw else dict(deepcopy(default), _storage_revision=0)
        with self.lock:
            row = self.rows.get(key)
            if row and row[0] > time.monotonic():
                self.rows.move_to_end(key)
                return deepcopy(row[1])
            self.rows.pop(key, None)
            return dict(deepcopy(default), _storage_revision=0)

    def save(self, learner, language, progress):
        key = self.key(learner, language)
        revision = progress.get("_storage_revision", 0)
        payload = dict(deepcopy(progress), _storage_revision=revision + 1)
        if self.redis:
            try:
                with self.redis.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    current = json.loads(raw).get("_storage_revision", 0) if raw else 0
                    if current != revision:
                        raise StorageConflictError("Guest progress changed")
                    pipe.multi()
                    pipe.set(key, json.dumps(payload), ex=self.ttl)
                    pipe.execute()
            except WatchError as error:
                raise StorageConflictError("Guest progress changed") from error
        else:
            with self.lock:
                row = self.rows.get(key)
                current = row[1].get("_storage_revision", 0) if row and row[0] > time.monotonic() else 0
                if current != revision:
                    raise StorageConflictError("Guest progress changed")
                self.rows[key] = (time.monotonic() + self.ttl, payload)
                self.rows.move_to_end(key)
                while len(self.rows) > self.capacity:
                    self.rows.popitem(last=False)
        progress["_storage_revision"] = revision + 1
