"""Adaptive matching practice; recognition never grants Daily Dose mastery."""
import random
import time
from uuid import uuid4
from flask import Blueprint, abort, jsonify, render_template, request
from ..services.matching import select_words


def register_word_match(app, services):
    bp = Blueprint("word_match", __name__, url_prefix="/word-match")

    def level_and_progress():
        progress = services["get_progress"]()
        levels = services["get_levels"]()["levels"]
        allowed = [levels[0]]
        for previous, level in zip(levels, levels[1:]):
            if not progress.get("course", {}).get(previous["id"], {}).get("passed"):
                break
            allowed.append(level)
        level_id = request.args.get("level", progress.get("level_id"))
        level = next((item for item in allowed if item["id"] == level_id), allowed[-1])
        return level, progress

    def store_for(progress):
        return progress.setdefault("games", {}).setdefault("word_match", {"words": {}, "good_rounds": 0})

    def snapshot(board):
        visible = set(board["matched"] + board["open"])
        return {"id": board["id"], "attempts": board["attempts"], "pairs": len(board["matched"]) // 2,
                "done": len(board["matched"]) == 20,
                "cards": [{"id": c["id"], "text": c["text"] if c["id"] in visible else None,
                           "side": c["side"] if c["id"] in visible else None,
                           "matched": c["id"] in board["matched"]} for c in board["cards"]]}

    @bp.get("")
    @bp.get("/")
    def page():
        level, progress = level_and_progress()
        store = store_for(progress)
        board = store.get("board")
        return render_template("learning/word-match.html", level=level,
                               initial=snapshot(board) if board and board["level_id"] == level["id"] else None)

    @bp.post("/start")
    def start():
        with services["progress_lock"]:
            level, progress = level_and_progress()
            catalog = services["dose_catalog"]()
            curriculum = services["content_store"].get_curriculum()
            ids = curriculum.get(level["id"], [])
            if not ids:
                ids = [w["id"] for w in list(catalog.values())[:10]]
            try:
                words = select_words(catalog, progress, ids, services["dose_normalize"])
            except ValueError:
                abort(503, "Vocabulary is being prepared.")
            store = store_for(progress)
            previews = [{"word": w["tagalog"], "english": w["english"]} for w in words
                        if w["id"] not in ids and w["id"] not in store["words"]
                        and not progress.get("mastery", {}).get(w["id"], {}).get("mastered")]
            cards = [{"id": uuid4().hex, "concept_id": w["id"], "side": side, "text": w[key]}
                     for w in words for side, key in [("target", "tagalog"), ("english", "english")]]
            random.SystemRandom().shuffle(cards)
            board = {"id": uuid4().hex, "level_id": level["id"], "cards": cards,
                     "matched": [], "open": [], "attempts": 0, "mistakes": {},
                     "preview": previews, "preview_until": None if previews else 0,
                     "hide_at": 0}
            store["board"] = board
            services["save_progress"](progress)
            return jsonify(ok=True, board=snapshot(board), preview=previews)

    @bp.post("/flip")
    def flip():
        data = request.get_json(silent=True) or {}
        with services["progress_lock"]:
            level, progress = level_and_progress()
            store = store_for(progress)
            board = store.get("board")
            if not board or board["level_id"] != level["id"] or board["id"] != data.get("board_id"):
                return jsonify(ok=False, error="This board changed. Start a new round."), 409
            now = time.time()
            action = data.get("action", "flip")
            if action == "preview":
                if board["preview_until"] is None:
                    board["preview_until"] = now + 3
                services["save_progress"](progress)
                return jsonify(ok=True, remaining_ms=max(0, int((board["preview_until"] - now) * 1000)))
            if board["preview_until"] is None or now < board["preview_until"]:
                return jsonify(ok=False, error="Review the new words first."), 409
            if len(board["open"]) == 2:
                if now < board["hide_at"]:
                    return jsonify(ok=False, error="Look at both cards, then try again."), 409
                board["open"] = []
            if action == "sync":
                services["save_progress"](progress)
                return jsonify(ok=True, board=snapshot(board))
            card = next((c for c in board["cards"] if c["id"] == data.get("card_id")), None)
            if not card or card["id"] in board["matched"] or card["id"] in board["open"]:
                abort(400)
            board["open"].append(card["id"])
            matched = None
            if len(board["open"]) == 2:
                board["attempts"] += 1
                first = next(c for c in board["cards"] if c["id"] == board["open"][0])
                matched = first["concept_id"] == card["concept_id"] and first["side"] != card["side"]
                if matched:
                    board["matched"].extend(board["open"])
                    board["open"] = []
                    item = store["words"].setdefault(card["concept_id"], {"clean_matches": 0})
                    item["clean_matches"] = item.get("clean_matches", 0) + 1 if not board["mistakes"].get(card["concept_id"]) else 0
                    item["last_matched_at"] = now
                else:
                    for c in (first, card):
                        cid = c["concept_id"]
                        board["mistakes"][cid] = board["mistakes"].get(cid, 0) + 1
                        item = store["words"].setdefault(cid, {"clean_matches": 0})
                        item["wrong"] = item.get("wrong", 0) + 1
                        item["last_missed_at"] = now
                    board["hide_at"] = now + 1.2
                if len(board["matched"]) == 20:
                    store["good_rounds"] = store.get("good_rounds", 0) + 1 if board["attempts"] <= 14 else 0
                    store["completed_rounds"] = store.get("completed_rounds", 0) + 1
            services["save_progress"](progress)
            return jsonify(ok=True, board=snapshot(board), matched=matched)

    @bp.post("/resume")
    def resume():
        level, progress = level_and_progress()
        board = store_for(progress).get("board")
        if not board or board["level_id"] != level["id"]:
            abort(404)
        return jsonify(ok=True, board=snapshot(board),
                       preview=board["preview"] if board["preview_until"] is None or time.time() < board["preview_until"] else [])

    app.register_blueprint(bp)
