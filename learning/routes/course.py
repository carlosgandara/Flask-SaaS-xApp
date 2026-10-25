"""Level learning flow. Storage and learner identity are supplied by the host app."""
import random
import time
from uuid import uuid4
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from ..services.course_rules import record, stats, ready, difficult, new_cycle, dashboard


def register_course(app, services):
    bp = Blueprint("course", __name__, url_prefix="/learn")
    load = services["get_progress"]
    save = services["save_progress"]
    lock = services["progress_lock"]
    normalize = services["dose_normalize"]


    def context(level_id):
        level = services["get_level"](level_id)
        if not level:
            abort(404)
        progress = load()
        levels = services["get_levels"]()["levels"]
        index = next(i for i, item in enumerate(levels) if item["id"] == level_id)
        if index and not record(progress, levels[index - 1]["id"]).get("passed"):
            abort(403, "Pass the previous level's final test first.")
        return level, progress, record(progress, level_id), index + 1

    def vocabulary(level_id):
        mapping = services["content_store"].get_curriculum()
        catalog = services["dose_catalog"]()
        ids = mapping.get(level_id, [])
        if len(ids) != 10 or any(cid not in catalog for cid in ids):
            abort(503, "This level needs ten translated vocabulary words.")
        return [catalog[cid] for cid in ids]


    @bp.get("/<level_id>")
    def overview(level_id):
        level, progress, state, number = context(level_id)
        configured = level_id in services["content_store"].get_curriculum()
        words = vocabulary(level_id) if configured else []
        if state.get("required_support"):
            return redirect(url_for("learning.course.shooter", level_id=level_id))
        return render_template("learning/course.html", level=level, state=state, words=words,
                               mastered=sum(stats(state, w["id"])["mastered"] for w in words),
                               support=bool(difficult(state, words)), ready=ready(state, words),
                               number=number, configured=configured)

    @bp.get("/<level_id>/daily-dose")
    def dose(level_id):
        level, progress, state, number = context(level_id)
        if not state.get("lesson_done"):
            return redirect(url_for("learning.level_view", level_id=level_id))
        words = vocabulary(level_id)
        if state.get("required_support"):
            return redirect(url_for("learning.course.shooter", level_id=level_id))
        with lock:
            # Reload under the lock so refreshes and answers cannot replace each other's cycle.
            level, progress, state, number = context(level_id)
            if state.get("required_support"):
                return redirect(url_for("learning.course.shooter", level_id=level_id))
            cycle = state.get("dose_cycle")
            if not cycle or cycle["index"] >= len(cycle["word_ids"]):
                ids = [w["id"] for w in words if not stats(state, w["id"])["mastered"]]
                cycle = new_cycle(state, ids or [w["id"] for w in words])
            save(progress)
        config = {"dashboard": dashboard(state, words, number), "level": number,
                  "words": words, "progress": {w["id"]: stats(state, w["id"]) for w in words},
                  "settings": {"masteryCorrect": 3}, "course": True,
                  "cycle_id": cycle["id"], "next_word_id": cycle["word_ids"][cycle["index"]],
                  "cycle_position": cycle["index"] + 1, "cycle_total": len(cycle["word_ids"]),
                  "urls": {"answer": url_for("learning.course.dose_answer", level_id=level_id),
                           "dashboard": url_for("learning.course.overview", level_id=level_id),
                           "menu": url_for("learning.menu"),
                           "continue": url_for("learning.course.overview", level_id=level_id)}}
        return render_template("learning/daily-dose.html", daily_dose_config=config)

    @bp.get("/<level_id>/next")
    def next_course(level_id):
        level, progress, state, number = context(level_id)
        if not state.get("passed"):
            return redirect(url_for("learning.course.overview", level_id=level_id))
        levels = services["get_levels"]()["levels"]
        if number >= len(levels):
            return redirect(url_for("learning.menu"))
        return redirect(url_for("learning.course.overview", level_id=levels[number]["id"]))

    @bp.post("/<level_id>/daily-dose/answer")
    def dose_answer(level_id):
        data = request.get_json(silent=True) or {}
        with lock:
            level, progress, state, number = context(level_id)
            words = vocabulary(level_id)
            if not state.get("lesson_done"):
                abort(403)
            if state.get("required_support"):
                return jsonify(ok=False, error="Finish required Word Shooter practice first.",
                               next_url=url_for("learning.course.shooter", level_id=level_id)), 409
            cycle = state.get("dose_cycle")
            if (not cycle or data.get("cycle_id") != cycle["id"]
                    or cycle["index"] >= len(cycle["word_ids"])
                    or data.get("word_id") != cycle["word_ids"][cycle["index"]]):
                return jsonify(ok=False, error="This cycle has changed. Reload Daily Dose."), 409
            word = next((w for w in words if w["id"] == data.get("word_id")), None)
            answer = data.get("answer")
            if not word or not isinstance(answer, str) or not 0 < len(answer) <= 120:
                abort(400)
            item = stats(state, word["id"])
            correct = normalize(answer) in {normalize(value) for value in word.get("accepted_answers", [word["tagalog"]])}
            item["correct" if correct else "wrong"] += 1
            if correct:
                item["recall_streak"] = min(3, item["recall_streak"] + 1)
                if item["recall_streak"] >= 3:
                    item["mastered"] = True
                    item["misses"] = 0
            else:
                item["recall_streak"] = 0
                item["misses"] += 1
                item["last_missed_at"] = time.time()
                item["mastered"] = False
                cycle["missed"].append(word["id"])
            cycle["index"] += 1
            next_url = None
            if cycle["index"] == len(cycle["word_ids"]):
                if cycle["missed"]:
                    state["required_support"] = list(cycle["missed"])
                    for cid in state["required_support"]:
                        stats(state, cid)["support_correct"] = 0
                    next_url = url_for("learning.course.shooter", level_id=level_id)
                else:
                    ids = [w["id"] for w in words if not stats(state, w["id"])["mastered"]]
                    if ids:
                        cycle = new_cycle(state, ids)
                    else:
                        next_url = url_for("learning.course.overview", level_id=level_id)
            # Keep the previous vocabulary record useful without accepting shooter mastery.
            progress.setdefault("mastery", {})[word["id"]] = dict(item)
            save(progress)
            return jsonify(ok=True, correct=correct, answer=word["tagalog"], stats=item,
                           word_mastered=item["mastered"], level_complete=ready(state, words),
                           dashboard=dashboard(state, words, number),
                           cycle_id=cycle["id"], next_url=next_url,
                           cycle_position=cycle["index"] + 1, cycle_total=len(cycle["word_ids"]),
                           next_word_id=cycle["word_ids"][cycle["index"]]
                           if cycle["index"] < len(cycle["word_ids"]) else None)


    def questions(level, words):
        rng = random.SystemRandom()
        result = []
        for word in words:
            options = rng.sample(list({w["tagalog"] for w in words if w["tagalog"] != word["tagalog"]}), 2)
            options.append(word["tagalog"])
            rng.shuffle(options)
            result.append({"prompt": f'How do you say “{word["english"]}”?',
                           "options": options, "answer": word["tagalog"], "kind": "vocabulary"})
        phrases = list({(step["phrase"], step["translation"]) for step in level["steps"]
                        if step.get("type") == "preview" and step.get("phrase") and step.get("translation")})
        if len(phrases) < 3:
            abort(503, "This lesson needs at least three translated phrases for testing.")
        for i in range(10):
            phrase, translation = phrases[i % len(phrases)]
            reverse = bool(i % 2)
            answer = translation if reverse else phrase
            pool = list({p[1] if reverse else p[0] for p in phrases} - {answer})
            options = rng.sample(pool, 2) + [answer]
            rng.shuffle(options)
            prompt = f'What does “{phrase}” mean?' if reverse else f'How do you say “{translation}”?'
            result.append({"prompt": prompt, "options": options, "answer": answer, "kind": "lesson"})
        rng.shuffle(result)
        return result

    @bp.get("/<level_id>/test")
    def test(level_id):
        level, progress, state, number = context(level_id)
        if state.get("required_support"):
            return redirect(url_for("learning.course.shooter", level_id=level_id))
        if not ready(state, vocabulary(level_id)):
            return redirect(url_for("learning.course.overview", level_id=level_id))
        return render_template("learning/course-test.html", level=level,
                               api=url_for("learning.course.test_api", level_id=level_id),
                               return_url=url_for("learning.course.overview", level_id=level_id),
                               practice_done=state.get("practice_done", False),
                               has_attempt=bool(state.get("attempt")))

    @bp.post("/<level_id>/test")
    def test_api(level_id):
        data = request.get_json(silent=True) or {}
        with lock:
            level, progress, state, number = context(level_id)
            words = vocabulary(level_id)
            if not ready(state, words):
                abort(403)
            action = data.get("action")
            if action == "start":
                mode = data.get("mode")
                if mode not in ("practice", "final"):
                    abort(400)
                if mode == "final" and not state.get("practice_done"):
                    abort(403, "Finish the untimed practice first.")
                attempt = {"id": uuid4().hex, "mode": mode, "index": 0,
                           "questions": questions(level, words), "answers": [], "started": None}
                state["attempt"] = attempt
            else:
                attempt = state.get("attempt")
                if not attempt or (action != "current" and data.get("attempt") != attempt["id"]):
                    abort(409, "Start or resume the current test.")
                if action == "ready":
                    if data.get("index") != attempt["index"] or attempt["index"] >= 20:
                        abort(409)
                    if attempt["started"] is None:
                        attempt["started"] = time.time()
                elif action == "answer":
                    if data.get("index") != attempt["index"] or attempt["index"] >= 20 or attempt["started"] is None:
                        abort(409)
                    question = attempt["questions"][attempt["index"]]
                    choice = data.get("choice")
                    if choice is not None and (type(choice) is not int or choice not in range(3)):
                        abort(400)
                    expired = attempt["mode"] == "final" and time.time() - attempt["started"] > 4
                    correct = choice is not None and not expired and question["options"][choice] == question["answer"]
                    attempt["answers"].append({"prompt": question["prompt"], "answer": question["answer"],
                                               "correct": correct, "expired": expired})
                    attempt["index"] += 1
                    attempt["started"] = None
                    if attempt["index"] == 20:
                        score = sum(a["correct"] for a in attempt["answers"])
                        if attempt["mode"] == "practice":
                            state["practice_done"] = True
                        else:
                            state["passed"] = state.get("passed", False) or score >= 16
                            state.setdefault("results", []).append({"id": attempt["id"], "score": score,
                                                                    "at": time.time()})
                            if score >= 16 and level_id not in progress["completed_levels"]:
                                progress["completed_levels"].append(level_id)
                elif action not in ("resume", "current"):
                    abort(400)
            save(progress)
            index = attempt["index"]
            result = {"ok": True, "attempt": attempt["id"], "mode": attempt["mode"], "index": index,
                      "done": index == 20, "practice_done": state.get("practice_done", False)}
            if index < 20:
                q = attempt["questions"][index]
                result["question"] = {"prompt": q["prompt"], "options": q["options"], "kind": q["kind"]}
                result["remaining_ms"] = max(0, round((4 - (time.time() - attempt["started"])) * 1000)) if attempt["started"] else 4000
            if action == "answer" and attempt["mode"] == "practice":
                result["feedback"] = attempt["answers"][-1]
            if index == 20:
                result["score"] = sum(a["correct"] for a in attempt["answers"])
                result["passed"] = attempt["mode"] == "final" and result["score"] >= 16
                result["review"] = [a for a in attempt["answers"] if not a["correct"]]
                levels = services["get_levels"]()["levels"]
                result["next_url"] = url_for("learning.course.overview", level_id=levels[number]["id"]) if result["passed"] and number < len(levels) else None
            return jsonify(result)

    from .word_shooter import register_shooter
    register_shooter(bp, {"context":context, "vocabulary":vocabulary, "difficult":difficult,
                          "stats":stats, "new_cycle":new_cycle, "normalize":normalize,
                          "lock":lock, "save":save})
    app.register_blueprint(bp)
