"""Temporary guest sessions; account progress stays in the shared SQL database."""
from collections import OrderedDict
from copy import deepcopy
import json
import threading
import time
from redis import Redis, WatchError
from .contracts import StorageConflictError


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
