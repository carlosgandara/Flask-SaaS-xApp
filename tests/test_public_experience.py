"""Public pages, three/six/member boundaries and isolated guest game sessions."""
import unittest
from unittest.mock import patch
from sqlalchemy import select, func
import test_billing as fixtures
from auth.db import engine
from integration import progress_table
from learning.services.content import get_levels
from learning.services.progress import default_progress


class PublicExperienceTests(unittest.TestCase):
    setUp = fixtures.BillingTests.setUp

    def seed(self, user_id, completed):
        with self.app.test_request_context():
            progress = default_progress()
            progress['course'] = {level['id']:{'passed':True} for level in get_levels()['levels'][:completed]}
        store = self.app.config['LEARNING_PROGRESS_STORE'].accounts
        existing = store.load(user_id, 'tl', progress)
        progress['_storage_revision'] = existing['_storage_revision']
        store.save(user_id, 'tl', progress)

    def test_home_and_public_navigation(self):
        self.client.delete_cookie('access_token')
        for path in ['/', '/index.html', '/about', '/pricing', '/games']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                for target in ['/menu', '/games', '/pricing', '/about', '/login']:
                    self.assertIn(('href="'+target+'"').encode(), response.data)
        response = self.client.get('/')
        self.assertIn(b'indexmainpage.png', response.data)
        self.assertIn(b'Start your adventure', response.data)
        self.assertIn(b'Play free games', response.data)
        games=self.client.get('/games').get_data(as_text=True)
        self.assertEqual(games.count('<option value='),80)

    def test_guest_can_play_but_level_four_is_gated_in_all_languages(self):
        self.client.delete_cookie('access_token')
        for lang in ['es','fr','tl']:
            self.client.set_cookie('language',lang)
            self.assertEqual(self.client.get('/menu').status_code,200)
            self.assertEqual(self.client.get('/learn/level-1-cafe').status_code,200)
            self.assertEqual(self.client.get('/learn/level-4-meeting').status_code,403)
            self.assertEqual(self.client.post('/learn/level-4-meeting/test',json={'action':'start'},headers=self.headers).status_code,401)
            self.assertEqual(self.client.get('/games/word-shooter?level=40').status_code,200)
            self.assertEqual(self.client.get('/word-match?level=level-40-real-conversation').status_code,200)
        with engine.connect() as db:
            self.assertEqual(db.execute(select(func.count()).select_from(progress_table)).scalar(),0)

    def test_free_account_can_finish_six_but_not_start_seven(self):
        self.seed(1,6)
        for path in ['/learn/level-4-meeting','/learn/level-5-family','/learn/level-6-numbers']:
            self.assertEqual(self.client.get(path).status_code,200,path)
        self.assertEqual(self.client.get('/learn/level-7-directions').status_code,403)
        self.assertIn(b'Membership',self.client.get('/menu').data)
        self.assertEqual(self.client.get('/games/word-shooter?level=40').status_code,200)
        with patch('integration.has_membership',return_value=True):
            self.assertEqual(self.client.get('/learn/level-7-directions').status_code,200)
        self.client.set_cookie('access_token',fixtures.generate_jwt('unverified@example.test'))
        self.assertEqual(self.client.get('/learn/level-4-meeting').status_code,403)

    def test_guest_state_is_isolated_and_carries_into_a_new_account(self):
        self.client.delete_cookie('access_token')
        self.assertEqual(self.client.post('/study-time',json={'seconds':17},headers=self.headers).status_code,200)
        visitor=self.app.test_client()
        self.assertEqual(visitor.get('/study-time').json['total_seconds'],0)
        self.client.set_cookie('access_token',fixtures.generate_jwt('one@example.test'))
        self.assertEqual(self.client.get('/study-time').json['total_seconds'],17)
        with engine.connect() as db:
            row=db.execute(select(progress_table)).mappings().one()
        self.assertEqual(row['user_id'],1)
        self.client.set_cookie('access_token',fixtures.generate_jwt('two@example.test'))
        # Existing account data must never be replaced by trial history.
        self.seed(2,0)
        self.assertEqual(self.client.get('/study-time').json['total_seconds'],0)

    def test_arcade_posts_require_csrf_and_do_not_unlock_lessons(self):
        self.client.delete_cookie('access_token')
        self.assertEqual(self.client.post('/word-match/start').status_code,403)
        self.assertEqual(self.client.post('/word-match/start',headers=self.headers).status_code,200)
        response=self.client.post('/games/word-shooter/answer',json={'level':1,'word_id':'concept-0011','answer':'kape'},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['correct'])
        with self.client.session_transaction() as state:
            guest=state['learning_guest_id']
        store=self.app.config['LEARNING_PROGRESS_STORE'].guests
        progress=store.load(guest,'tl',{})
        self.assertEqual(progress['course'],{})
        self.assertIn('arcade_shooter',progress['games'])
        self.assertEqual(self.client.post('/games/word-shooter/answer',json={'level':999,'word_id':'concept-0011','answer':'kape'},headers=self.headers).status_code,404)
