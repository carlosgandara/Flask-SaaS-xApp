import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from learning import create_app
app = create_app()
from learning.services import content, progress
from learning.tests.test_course import MemoryStore
from learning.routes.word_match import select_words


class WordMatchTests(unittest.TestCase):
    def setUp(self):
        self.old_store = app.config["LEARNING_PROGRESS_STORE"]
        self.old_resolver = app.config["LEARNING_LEARNER_ID_RESOLVER"]
        self.store = MemoryStore()
        app.config.update(TESTING=True, LEARNING_PROGRESS_STORE=self.store,
                                   LEARNING_LEARNER_ID_RESOLVER=lambda: "match-user")
        self.client = app.test_client()
        with app.test_request_context():
            self.progress = progress.default_progress()
            self.catalog = content.dose_catalog()
        self.ids = json.loads((content.DATA_DIR / "curriculum.json").read_text())["level-1-cafe"]

    def tearDown(self):
        app.config.update(LEARNING_PROGRESS_STORE=self.old_store, LEARNING_LEARNER_ID_RESOLVER=self.old_resolver)

    def board(self):
        return self.store.rows[("match-user", "tl")]["games"]["word_match"]["board"]

    def flip(self, card, **kwargs):
        return self.client.post("/word-match/flip", json={"board_id":self.board()["id"],"card_id":card,**kwargs})

    def test_first_board_current_words_and_menu_replacement(self):
        menu = self.client.get("/menu").get_data(as_text=True)
        self.assertIn("Word Match", menu)
        self.assertEqual(self.client.get("/word-match").status_code,200)
        result = self.client.post("/word-match/start").get_json()
        self.assertEqual(len(result["board"]["cards"]),20)
        self.assertTrue(all(c["text"] is None for c in result["board"]["cards"]))
        self.assertEqual({c["concept_id"] for c in self.board()["cards"]},set(self.ids))
        self.assertEqual(result["preview"],[])

    def test_matching_mismatch_pause_and_refresh(self):
        self.client.post("/word-match/start")
        cards = self.board()["cards"]
        first = cards[0]
        other = next(c for c in cards if c["concept_id"] != first["concept_id"])
        with patch("learning.routes.word_match.time.time",return_value=100):
            self.assertIsNotNone(self.flip(first["id"]).get_json()["board"]["cards"][0]["text"])
            response = self.flip(other["id"]).get_json()
            self.assertFalse(response["matched"])
            self.assertEqual(sum(bool(c["text"]) for c in response["board"]["cards"]),2)
            third = next(c for c in cards if c["id"] not in [first["id"],other["id"]])
            self.assertEqual(self.flip(third["id"]).status_code,409)
        with patch("learning.routes.word_match.time.time",return_value=102):
            response = self.flip(None,action="sync").get_json()
            self.assertTrue(all(c["text"] is None for c in response["board"]["cards"]))
            self.flip(first["id"])
            partner = next(c for c in cards if c["concept_id"]==first["concept_id"] and c["id"]!=first["id"])
            response=self.flip(partner["id"]).get_json()
            self.assertTrue(response["matched"])
            self.assertEqual(response["board"]["pairs"],1)
        resumed=self.client.post("/word-match/resume").get_json()["board"]
        self.assertEqual(sum(c["matched"] for c in resumed["cards"]),2)
        self.assertEqual(self.flip(first["id"]).status_code,400)

    def test_missed_words_first_and_gradual_new_words(self):
        self.progress["mastery"]={"concept-0001":{"wrong":3,"last_missed_at":200}}
        selected=select_words(self.catalog,self.progress,self.ids,content.dose_normalize)
        self.assertEqual(selected[0]["id"],"concept-0001")
        self.assertEqual(sum(w["id"] in self.ids for w in selected),9)
        self.progress["games"]={"word_match":{"good_rounds":2,"words":{}}}
        selected=select_words(self.catalog,self.progress,self.ids,content.dose_normalize)
        self.assertEqual(selected[0]["id"],"concept-0001")
        self.assertEqual(sum(w["id"] in self.ids for w in selected),8)
        # Two clean recognitions retire an old error; a newer miss restores priority.
        self.progress["games"]["word_match"]["words"]["concept-0001"]={"clean_matches":2,"last_matched_at":300}
        self.progress["games"]["word_match"]["good_rounds"]=0
        self.assertEqual({w["id"] for w in select_words(self.catalog,self.progress,self.ids,content.dose_normalize)},set(self.ids))
        self.progress["mastery"]["concept-0001"]["last_missed_at"]=400
        self.assertEqual(select_words(self.catalog,self.progress,self.ids,content.dose_normalize)[0]["id"],"concept-0001")

    def test_new_word_preview_blocks_flips_and_survives_refresh(self):
        self.progress["games"]={"word_match":{"good_rounds":2,"words":{}}}
        self.store.save("match-user","tl",self.progress)
        result=self.client.post("/word-match/start").get_json()
        self.assertEqual(len(result["preview"]),1)
        card=self.board()["cards"][0]["id"]
        self.assertEqual(self.flip(card).status_code,409)
        with patch("learning.routes.word_match.time.time",return_value=100):
            response=self.flip(None,action="preview").get_json()
            self.assertEqual(response["remaining_ms"],3000)
        with patch("learning.routes.word_match.time.time",return_value=101):
            self.assertEqual(self.flip(None,action="preview").get_json()["remaining_ms"],2000)
            self.assertEqual(len(self.client.post("/word-match/resume").get_json()["preview"]),1)
        with patch("learning.routes.word_match.time.time",return_value=104):
            self.assertEqual(self.flip(card).status_code,200)

    def test_full_round_does_not_grant_daily_dose_mastery(self):
        self.client.post("/word-match/start")
        cards=deepcopy(self.board()["cards"])
        for cid in self.ids:
            pair=[c for c in cards if c["concept_id"]==cid]
            self.flip(pair[0]["id"])
            response=self.flip(pair[1]["id"]).get_json()
        self.assertTrue(response["board"]["done"])
        self.assertEqual(response["board"]["attempts"],10)
        saved=self.store.rows[("match-user","tl")]
        self.assertFalse(saved["mastery"])
        self.assertFalse(saved["course"])
        self.assertEqual(saved["games"]["word_match"]["good_rounds"],1)
        self.assertEqual(self.client.post("/word-match/flip",json={"board_id":"old","card_id":"anything"}).status_code,409)

    def test_languages_and_learners_are_isolated(self):
        self.client.post("/word-match/start")
        old_id=self.board()["id"]
        for lang in ["es","fr"]:
            self.client.set_cookie("language",lang)
            response=self.client.post("/word-match/start")
            self.assertEqual(response.status_code,200)
            self.assertNotEqual(response.get_json()["board"]["id"],old_id)
            self.assertEqual(self.client.get("/word-match").status_code,200)
        app.config["LEARNING_LEARNER_ID_RESOLVER"]=lambda:"another-user"
        self.assertEqual(self.client.post("/word-match/resume").status_code,404)


if __name__ == "__main__":
    unittest.main()
