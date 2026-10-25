import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from flask import Flask, abort

from learning import create_app, init_learning, PACKAGE_DIR
from learning.services import content
from learning.storage import JsonContentStore
from learning.storage.contracts import StorageConflictError
from learning.tests.test_course import MemoryStore


class LearningPackageTests(unittest.TestCase):
    def app(self, **config):
        # No file logs are needed to verify package composition.
        app=Flask("host_"+str(id(config)),static_folder=None)
        app.config.update(LEARNING_LEARNER_ID_RESOLVER=lambda:1,
                          LEARNING_PROGRESS_STORE=MemoryStore(), **config)
        init_learning(app)
        return app

    def test_registers_on_host_without_replacing_host_routes_or_logging(self):
        app=Flask("host",static_folder=None)
        @app.get("/dashboard")
        def dashboard():return "Host dashboard"
        @app.get("/")
        def host_home():return "Host home"
        app.config.update(LEARNING_LEARNER_ID_RESOLVER=lambda:1,LEARNING_PROGRESS_STORE=MemoryStore())
        first=init_learning(app)
        self.assertIs(init_learning(app),first)
        self.assertNotIn("diagnostics",app.extensions)
        client=app.test_client()
        self.assertEqual(client.get("/dashboard").data,b"Host dashboard")
        self.assertEqual(client.get("/").data,b"Host home")
        self.assertNotIn("learning.home",app.view_functions)
        for url in ["/menu","/word-match","/learn/level-1-cafe","/despacio","/level/level-1-cafe"]:
            response=client.get(url)
            self.assertEqual(response.status_code,200,url)
        response=client.get("/learning/static/style.css")
        self.assertEqual(response.status_code,200)
        response.close()
        self.assertIn('learning.menu',app.view_functions)
        self.assertIn('learning.course.overview',app.view_functions)
        self.assertIn('learning.word_match.page',app.view_functions)

    def test_restart_next_level_and_packaged_image_paths(self):
        app=self.app()
        client=app.test_client()
        self.assertEqual(client.get("/level/level-1-cafe?restart=1").status_code,302)
        with app.test_request_context():
            from learning.services import progress
            saved=progress.get_progress()
            saved["course"]={"level-1-cafe":{"passed":True}}
            progress.save_progress(saved)
            scene=content.get_levels()["levels"][0]["steps"][0]["image"]
        self.assertEqual(client.get("/next-level").status_code,302)
        response=client.get(scene)
        self.assertEqual(response.status_code,200)
        self.assertTrue(scene.startswith("/learning/static/"))
        response.close()

    def test_host_must_supply_account_identity(self):
        with self.assertRaisesRegex(RuntimeError,"LEARNING_LEARNER_ID_RESOLVER"):
            init_learning(Flask("missing_identity"))
        app=self.app()
        app.config["LEARNING_LEARNER_ID_RESOLVER"]=lambda:None
        self.assertEqual(app.test_client().get("/menu").status_code,401)

    def test_independent_apps_and_cache_use_injected_content(self):
        source=JsonContentStore(PACKAGE_DIR/"data")
        class CustomContent:
            def __init__(self,title):self.title=title
            def list_languages(self):return ["es"]
            def get_levels(self,language):
                value=source.get_levels(language)
                value["levels"][0]["title"]=self.title
                return value
            def get_concepts(self):return source.get_concepts()
            def get_vocabulary(self,language):return source.get_vocabulary(language)
            def get_curriculum(self):return source.get_curriculum()
        one=self.app(LEARNING_CONTENT_STORE=CustomContent("First host content"))
        two=self.app(LEARNING_CONTENT_STORE=CustomContent("Second host content"))
        self.assertIn(b"First host content",one.test_client().get("/menu").data)
        self.assertNotIn(b"Second host content",one.test_client().get("/menu").data)
        self.assertIn(b"Second host content",two.test_client().get("/menu").data)
        self.assertIsNot(one.extensions["learning"]["content_cache"],two.extensions["learning"]["content_cache"])
        with one.test_request_context():
            self.assertEqual(content.current_language(),"es")

    def test_access_and_mutation_hooks_are_applied(self):
        def deny_access():abort(401)
        app=self.app(LEARNING_ACCESS_CHECK=deny_access)
        self.assertEqual(app.test_client().get("/menu").status_code,401)
        response=app.test_client().get("/learning/static/style.css")
        self.assertEqual(response.status_code,200)
        response.close()
        app.config["LEARNING_ACCESS_CHECK"]=None
        app.config["LEARNING_MUTATION_CHECK"]=lambda:abort(403)
        self.assertEqual(app.test_client().post("/word-match/start").status_code,403)
        self.assertEqual(app.test_client().post("/answer",json={"correct":True}).status_code,403)
        self.assertEqual(app.test_client().post("/study-time",json={"seconds":10}).status_code,403)

    def test_progress_conflicts_are_visible_and_not_generic_server_errors(self):
        class ConflictingStore(MemoryStore):
            def save(self,*args):raise StorageConflictError("newer revision")
        app=self.app()
        app.config["LEARNING_PROGRESS_STORE"]=ConflictingStore()
        response=app.test_client().post("/word-match/start")
        self.assertEqual(response.status_code,409)
        self.assertIn("Reload",response.get_json()["error"])



if __name__=="__main__":unittest.main()
