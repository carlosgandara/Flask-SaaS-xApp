"""Temporary play stays isolated, expires and rejects stale writes."""
import unittest
from unittest.mock import patch
from learning.storage.guest_progress import GuestProgressStore
from learning.storage.contracts import StorageConflictError


class GuestProgressTests(unittest.TestCase):
    def test_identity_language_and_revision_isolation(self):
        store=GuestProgressStore()
        progress=store.load('guest-a','es',{'score':0})
        progress['score']=8
        stale=dict(progress)
        store.save('guest-a','es',progress)
        self.assertEqual(store.load('guest-a','es',{})['score'],8)
        self.assertEqual(store.load('guest-b','es',{'score':0})['score'],0)
        self.assertEqual(store.load('guest-a','fr',{'score':0})['score'],0)
        with self.assertRaises(StorageConflictError):store.save('guest-a','es',stale)
        with self.assertRaises(ValueError):store.load('account-1','es',{})

    def test_expiry_and_bounded_memory(self):
        store=GuestProgressStore(ttl=60,capacity=2)
        with patch('learning.storage.guest_progress.time.monotonic',return_value=100):
            for guest in ['guest-a','guest-b','guest-c']:
                row=store.load(guest,'tl',{'score':1});store.save(guest,'tl',row)
            self.assertEqual(len(store.rows),2)
            self.assertEqual(store.load('guest-a','tl',{'score':0})['score'],0)
        with patch('learning.storage.guest_progress.time.monotonic',return_value=161):
            self.assertEqual(store.load('guest-c','tl',{'score':0})['score'],0)
