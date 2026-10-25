import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from learning import create_app
app = create_app()
from learning.services import content, progress
from learning.storage.json_progress import JsonProgressStore
from learning.services.vocabulary_entries import vocabulary_entry


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def load(self, learner, language, default):
        return deepcopy(self.rows.get((learner, language), default))

    def save(self, learner, language, progress):
        self.rows[(learner, language)] = deepcopy(progress)


class CourseTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.old_store = app.config["LEARNING_PROGRESS_STORE"]
        self.old_resolver = app.config["LEARNING_LEARNER_ID_RESOLVER"]
        app.config.update(TESTING=True, LEARNING_PROGRESS_STORE=self.store,
                                   LEARNING_LEARNER_ID_RESOLVER=lambda: "test-user")
        self.client = app.test_client()
        self.url = "/learn/level-1-cafe"
        self.client.get(self.url)
        with app.test_request_context():
            self.progress = progress.default_progress()
            self.words = content.dose_catalog()
        self.state = self.progress.setdefault("course", {}).setdefault("level-1-cafe", {})
        self.persist()

    def tearDown(self):
        app.config.update(LEARNING_PROGRESS_STORE=self.old_store, LEARNING_LEARNER_ID_RESOLVER=self.old_resolver)

    def persist(self):
        self.store.save("test-user", "tl", self.progress)

    def current(self):
        return self.store.rows[("test-user", "tl")]["course"]["level-1-cafe"]

    def prepare_test(self):
        self.state.update(lesson_done=True)
        mapping = json.loads((content.DATA_DIR / "curriculum.json").read_text())["level-1-cafe"]
        self.state["words"] = {cid: {"correct":3,"wrong":0,"recall_streak":3,
                                     "mastered":True,"misses":0,"support_correct":0} for cid in mapping}
        self.persist()

    def post(self, **data):
        return self.client.post(self.url + "/test", json=data)

    def finish(self, mode, correct_count):
        response = self.post(action="start", mode=mode)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        questions = self.current()["attempt"]["questions"]
        self.assertEqual(sum(q["kind"] == "vocabulary" for q in questions), 10)
        self.assertEqual(sum(q["kind"] == "lesson" for q in questions), 10)
        self.assertNotIn("answer", result["question"])
        for index in range(20):
            kwargs = {"attempt":result["attempt"],"index":index}
            self.assertEqual(self.post(action="ready", **kwargs).status_code, 200)
            question = questions[index]
            self.assertEqual(len(set(question["options"])), 3)
            choice = question["options"].index(question["answer"])
            if index >= correct_count:
                choice = (choice + 1) % 3
            result = self.post(action="answer", choice=choice, **kwargs).get_json()
        return result

    def test_lesson_alone_does_not_unlock_level_two(self):
        self.assertEqual(self.client.get("/learn/level-2-restaurant").status_code, 403)
        self.progress["step_index"] = len(content.load_language_data("tl")["levels"]["levels"][0]["steps"]) - 1
        self.persist()
        self.assertEqual(self.client.get("/level/level-1-cafe").status_code, 302)
        self.assertTrue(self.current()["lesson_done"])
        self.assertEqual(self.client.get("/learn/level-2-restaurant").status_code, 403)
        self.assertEqual(self.client.get("/next-level").status_code, 302)
        self.assertEqual(self.client.get("/level/level-2-restaurant").status_code, 302)

    def answer_dose(self, cid, answer):
        cycle = self.current()["dose_cycle"]
        return self.client.post(self.url + "/daily-dose/answer", json={
            "word_id": cid, "answer": answer, "cycle_id": cycle["id"]})

    def test_missed_words_require_shooter_after_cycle_then_return_to_recall(self):
        self.state["lesson_done"] = True
        self.persist()
        self.client.get(self.url + "/daily-dose")
        ids = list(self.current()["dose_cycle"]["word_ids"])
        missed = [ids[0], ids[2]]
        for i, cid in enumerate(ids):
            result = self.answer_dose(cid, "wrong" if cid in missed else self.words[cid]["tagalog"]).get_json()
            if i < 9:
                self.assertIsNone(result["next_url"])
                self.assertFalse(self.current().get("required_support"))
                self.assertEqual(self.client.get(self.url + "/shooter").status_code, 302)
        self.assertEqual(result["next_url"], self.url + "/shooter")
        self.assertEqual(self.current()["required_support"], missed)
        for route in [self.url, self.url + "/daily-dose", self.url + "/test"]:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.location.endswith("/shooter"))
        self.assertEqual(self.answer_dose(ids[0],"kape").status_code,409)
        self.assertEqual(self.post(action="start",mode="practice").status_code,403)
        response = self.client.get(self.url + "/shooter")
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b"Return to Daily Dose</a>",response.data)
        for cid in missed:
            for _ in range(3):
                response = self.client.post(self.url + "/shooter/answer", json={
                    "word_id":cid,"answer":self.words[cid]["tagalog"]})
                self.assertEqual(response.status_code,200)
        self.assertTrue(response.get_json()["level_complete"])
        self.assertFalse(self.current()["required_support"])
        self.assertEqual(self.current()["dose_cycle"]["word_ids"],missed)
        self.assertTrue(all(not self.current()["words"][cid]["mastered"] for cid in missed))
        self.client.get(self.url + "/daily-dose")
        self.assertEqual(self.current()["dose_cycle"]["word_ids"],missed)
        self.answer_dose(missed[0],"wrong again")
        response=self.answer_dose(missed[1],self.words[missed[1]]["tagalog"])
        self.assertEqual(response.get_json()["next_url"],self.url + "/shooter")
        self.assertEqual(self.current()["required_support"],[missed[0]])

    def test_cycle_refresh_duplicates_and_clean_mastery(self):
        self.state["lesson_done"] = True
        self.persist()
        self.client.get(self.url + "/daily-dose")
        cycle=deepcopy(self.current()["dose_cycle"])
        cid=cycle["word_ids"][0]
        self.answer_dose(cid,self.words[cid]["tagalog"])
        self.client.get(self.url + "/daily-dose")
        self.assertEqual(self.current()["dose_cycle"]["id"],cycle["id"])
        self.assertEqual(self.current()["dose_cycle"]["index"],1)
        self.assertEqual(self.answer_dose(cid,self.words[cid]["tagalog"]).status_code,409)
        for _ in range(29):
            cycle=self.current()["dose_cycle"]
            cid=cycle["word_ids"][cycle["index"]]
            response=self.answer_dose(cid,self.words[cid]["tagalog"])
            self.assertEqual(response.status_code,200)
        self.assertTrue(response.get_json()["level_complete"])
        self.assertEqual(response.get_json()["next_url"],self.url)
        self.assertFalse(self.current().get("required_support"))
        self.assertEqual(self.client.get(self.url + "/test").status_code,200)

    def test_prerequisites_and_passing_boundary(self):
        self.assertEqual(self.post(action="start",mode="practice").status_code,403)
        self.prepare_test()
        self.assertEqual(self.post(action="start",mode="final").status_code,403)
        practice = self.finish("practice", 10)
        self.assertTrue(practice["practice_done"])
        self.assertFalse(practice["passed"])
        failed = self.finish("final", 15)
        self.assertFalse(failed["passed"])
        self.assertEqual(len(failed["review"]),5)
        self.assertEqual(self.client.get("/learn/level-2-restaurant").status_code,403)
        passed = self.finish("final",16)
        self.assertTrue(passed["passed"])
        self.assertEqual(self.client.get("/learn/level-2-restaurant").status_code,200)

    def test_timer_resume_and_duplicate_answers(self):
        self.prepare_test()
        self.store.rows[("test-user","tl")]["course"]["level-1-cafe"]["practice_done"] = True
        start = self.post(action="start",mode="final").get_json()
        kwargs = {"attempt":start["attempt"],"index":0}
        self.assertEqual(self.post(action="answer",choice=0,**kwargs).status_code,409)
        with patch("learning.routes.course.time.time",return_value=100):
            self.post(action="ready",**kwargs)
        with patch("learning.routes.course.time.time",return_value=102):
            resumed = self.post(action="ready",**kwargs).get_json()
            self.assertEqual(resumed["remaining_ms"],2000)
        question = self.current()["attempt"]["questions"][0]
        with patch("learning.routes.course.time.time",return_value=105):
            self.post(action="answer",choice=question["options"].index(question["answer"]),**kwargs)
        self.assertFalse(self.current()["attempt"]["answers"][0]["correct"])
        self.assertTrue(self.current()["attempt"]["answers"][0]["expired"])
        self.assertEqual(self.post(action="answer",choice=0,**kwargs).status_code,409)
        self.assertEqual(self.post(action="current").get_json()["index"],1)

    def test_curriculum_and_pages_in_all_languages(self):
        curriculum = json.loads((content.DATA_DIR / "curriculum.json").read_text())
        for language in content.LANGUAGES:
            self.client.set_cookie("language",language)
            with app.test_request_context(headers={"Cookie":"language="+language}):
                catalog = content.dose_catalog()
                for ids in curriculum.values():
                    self.assertEqual(len(set(ids)),10)
                    self.assertTrue(all(cid in catalog for cid in ids))
            self.assertEqual(self.client.get(self.url).status_code,200)
            self.assertEqual(self.client.get("/menu").status_code,200)

    def test_learner_and_language_isolation(self):
        self.prepare_test()
        app.config["LEARNING_LEARNER_ID_RESOLVER"] = lambda: "another-user"
        self.assertEqual(self.post(action="start",mode="practice").status_code,403)
        app.config["LEARNING_LEARNER_ID_RESOLVER"] = lambda: "test-user"
        self.client.set_cookie("language","es")
        self.assertEqual(self.post(action="start",mode="practice").status_code,403)

    def test_json_store_round_trip_and_isolation(self):
        with TemporaryDirectory() as folder:
            store = JsonProgressStore(folder)
            store.save("user/../one","tl",{"course":{"cafe":{"passed":True}}})
            self.assertTrue(store.load("user/../one","tl",{})["course"]["cafe"]["passed"])
            self.assertEqual(store.load("two","tl",{}),{})
            self.assertEqual(store.load("user/../one","es",{}),{})

    def test_rich_vocabulary_records_and_legacy_compatibility(self):
        self.assertEqual(vocabulary_entry("kape"), {
            "translation":"kape", "accepted_answers":["kape"], "example":None,
            "part_of_speech":None, "pronunciation":None, "audio":None,
            "usage_notes":[], "forms":[]})
        self.assertEqual(vocabulary_entry({"translation":" kape ","accepted_answers":["kape", "alternate", "", None]})["accepted_answers"], ["kape", "alternate"])
        for language, count in [("tl",260),("es",260),("fr",260)]:
            records=json.loads((content.DATA_DIR / f"languages/vocabulary/{language}.json").read_text(encoding="utf-8"))
            self.assertEqual(len(records),count)
            shared=json.loads((content.DATA_DIR / "concepts.json").read_text(encoding="utf-8"))
            self.assertEqual(set(records),set(shared),"Every language must cover exactly the shared concept IDs")
            for item in records.values():
                self.assertEqual(set(item),{"translation","accepted_answers","example",
                                            "part_of_speech","pronunciation","audio","usage_notes","forms"})
                self.assertIn(item["translation"], item["accepted_answers"])
                if item['example'] is not None:
                    self.assertEqual(set(item["example"]),{"text","translation"})
                    self.assertTrue(item["example"]["text"].strip())
                    self.assertTrue(item["example"]["translation"].strip())
                if item['audio'] is not None:
                    self.assertTrue(item["audio"].startswith(f"audio/vocabulary/{language}/"))
                    self.assertTrue(item["audio"].endswith(".mp3"))
                self.assertIn(item["part_of_speech"],{"noun","verb","adjective","number","phrase","adverb","connector"})
                self.assertIsInstance(item["usage_notes"],list)
                self.assertIsInstance(item["forms"],list)
            self.assertIsNotNone(records["concept-0011"]["example"])
            with app.test_request_context(headers={"Cookie":"language="+language}):
                loaded=content.dose_catalog()
                self.assertEqual(len(loaded),count)
                self.assertEqual(loaded["concept-0002"]["audio"],records["concept-0002"]["audio"])
                self.assertEqual(loaded["concept-0002"]["part_of_speech"],"noun")
                self.assertTrue(content.load_vocabulary_data(language)["words"])

    def test_daily_dose_accepts_configured_alternative_answers(self):
        self.state["lesson_done"]=True
        self.persist()
        catalog=deepcopy(self.words)
        catalog["concept-0011"]["accepted_answers"]=["kape","test alternate"]
        with patch.object(content,"dose_catalog",return_value=catalog):
            self.client.get(self.url + "/daily-dose")
            response=self.answer_dose("concept-0011","  TEST ALTERNATE  ")
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.get_json()["correct"])


if __name__ == "__main__":
    unittest.main()
