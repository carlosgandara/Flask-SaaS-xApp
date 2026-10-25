"""Short transactions acquire expiring ownership; fencing prevents stale commits."""

from datetime import datetime, timedelta
import secrets

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from auth.db import SessionLocal
from .errors import SyncBusy
from .models import SyncLease


def insert_for(db, model):
    return (pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert)(model)


def acquire(key, seconds=120):
    token = secrets.token_urlsafe(24)
    now = datetime.utcnow()
    with SessionLocal.begin() as db:
        db.execute(insert_for(db, SyncLease).values(key=key, token="", expires_at=datetime(1970, 1, 1))
                   .on_conflict_do_nothing(index_elements=["key"]))
        changed = db.execute(update(SyncLease).where(SyncLease.key == key, SyncLease.expires_at <= now)
                             .values(token=token, expires_at=now + timedelta(seconds=seconds)))
        if changed.rowcount != 1:
            raise SyncBusy("Subscription synchronization is already running")
    return token


def fence(db, key, token):
    row = db.execute(select(SyncLease).where(SyncLease.key == key).with_for_update()).scalar_one()
    if row.token != token or row.expires_at <= datetime.utcnow():
        raise SyncBusy("Subscription synchronization lease expired")


def release(key, token):
    with SessionLocal.begin() as db:
        db.execute(update(SyncLease).where(SyncLease.key == key, SyncLease.token == token)
                   .values(expires_at=datetime(1970, 1, 1)))
