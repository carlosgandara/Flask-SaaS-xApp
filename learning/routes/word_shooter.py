"""Required Word Shooter practice, separate from the course and test flow."""
from flask import request, redirect, url_for, render_template, jsonify, abort


def register_shooter(bp, flow):
    context, vocabulary, difficult = flow["context"], flow["vocabulary"], flow["difficult"]
    stats, new_cycle = flow["stats"], flow["new_cycle"]
    normalize, lock, save = flow["normalize"], flow["lock"], flow["save"]

    @bp.get("/<level_id>/shooter")
    def shooter(level_id):
        level, progress, state, number = context(level_id)
        words = difficult(state, vocabulary(level_id))
        if not state.get("lesson_done") or not words:
            return redirect(url_for("learning.course.overview", level_id=level_id))
        game_words = [{"id": w["id"], "answer": w["tagalog"], "english": w["english"],
                       "correct": 0, "wrong": 0, "recall_streak": min(3, stats(state, w["id"])["support_correct"]),
                       "mastered": stats(state, w["id"])["support_correct"] >= 3}
                      for w in words]
        # The original arcade UI uses numbered cards; only the selected card is displayed.
        cards = [{"mastered": 0, "total": len(words)} for _ in range(number)]
        return render_template("learning/word-shooter.html", game_words=game_words,
                               shooter_levels=cards, shooter_level=number, shooter_mastery=3,
                               course_return=url_for("learning.course.dose", level_id=level_id),
                               course_answer=url_for("learning.course.shooter_answer", level_id=level_id))


    @bp.post("/<level_id>/shooter/answer")
    def shooter_answer(level_id):
        data = request.get_json(silent=True) or {}
        with lock:
            level, progress, state, number = context(level_id)
            words = difficult(state, vocabulary(level_id))
            word = next((w for w in words if w["id"] == data.get("word_id")), None)
            answer = data.get("answer")
            if not state.get("lesson_done") or not word:
                abort(403)
            if not isinstance(answer, str) or len(answer) > 120:
                abort(400)
            item = stats(state, word["id"])
            correct = normalize(answer) == normalize(word["tagalog"])
            if correct:
                item["support_correct"] += 1
            complete = all(stats(state, w["id"])["support_correct"] >= 3 for w in words)
            if complete:
                for w in words:
                    stats(state, w["id"])["misses"] = 0
                    stats(state, w["id"])["support_correct"] = 0
                state["required_support"] = []
                new_cycle(state, [w["id"] for w in words])
            save(progress)
            # Recognition ends this short support round; Daily Dose still checks recall.
            return jsonify(ok=True, correct=correct, answer=word["tagalog"],
                           stats={"mastered": item["support_correct"] >= 3 or complete,
                                  "recall_streak": min(3, item["support_correct"])},
                           word_mastered=item["support_correct"] >= 3 or complete,
                           level_complete=complete)
