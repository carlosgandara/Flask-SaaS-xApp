"""Lesson pages and answer submission."""
from flask import render_template, redirect, url_for, request, jsonify, current_app
from ..services.content import get_levels, get_all_lesson_words
from ..services.progress import default_progress, get_progress, save_progress, progress_lock, progress_transaction
from ..services.lesson_helpers import get_level, get_level_index, is_level_unlocked, pick_drill_words, find_first_step_for_word
WRONG_THRESHOLD = 3


def register_routes(bp):
    @bp.route("/level/<level_id>")
    @progress_transaction
    def level_view(level_id):
        level = get_level(level_id)
        if not level:
            return "Level not found", 404
        p = get_progress()
        completed = p.get(
            "completed_levels", []
        )
        if not is_level_unlocked(
            level_id,
            completed,
            progress=p,
        ):
            return redirect(url_for("learning.menu"))
        if request.args.get("restart") == "1":
            p["level_id"] = level_id
            p["step_index"] = 0
            save_progress(p)
            return redirect(
                url_for(
                    "learning.level_view",
                    level_id=level_id
                )
            )
        if p["level_id"] != level_id:
            p["level_id"] = level_id
            p["step_index"] = 0
            save_progress(p)
        steps = level["steps"]
        idx = min(
            p["step_index"],
            len(steps) - 1
        )
        step = dict(steps[idx])
        if step.get("type") == "drill":
            step["words"] = pick_drill_words(
                p,
                n=14,
                must_include=step.get(
                    "must_include"
                )
            )
        if step.get("type") == "win":
            record = p.setdefault("course", {}).setdefault(level_id, {})
            record["lesson_done"] = True
            save_progress(p)
            return redirect(url_for("learning.course.overview", level_id=level_id))
        return render_template(
            "learning/level.html",
            level=level,
            step=step,
            step_index=idx,
            total_steps=len(steps),
            vocab=get_all_lesson_words(),
            progress=p
        )


    @bp.route("/answer", methods=["POST"])
    def answer():
        data = request.get_json(silent=True) or {}
        word = data.get("word")
        correct = bool(
            data.get("correct", False)
        )
        advance = bool(
            data.get("advance", True)
        )
        with progress_lock:
            p = get_progress()
            p.setdefault("completed_levels", [])
            reload_client = False
            went_back = False
            if word:
                rec = p["words"].setdefault(
                    word,
                    {
                        "correct": 0,
                        "wrong": 0,
                        "wrong_streak": 0
                    }
                )
                if correct:
                    rec["correct"] += 1
                    rec["wrong_streak"] = 0
                else:
                    rec["wrong"] += 1
                    rec["wrong_streak"] += 1
                    if (
                        advance
                        and rec["wrong_streak"]
                        >= WRONG_THRESHOLD
                    ):
                        first = find_first_step_for_word(
                            word,
                            p["level_id"]
                        )
                        if first is not None:
                            p["step_index"] = first
                            rec["wrong_streak"] = 0
                            reload_client = True
                            went_back = True
            if advance and correct:
                p["step_index"] += 1
                reload_client = True
            save_progress(p)
        return jsonify({
            "ok": True,
            "reload": reload_client,
            "went_back": went_back
        })


    @bp.route("/next-level")
    @progress_transaction
    def next_level():
        p = get_progress()
        i = get_level_index(p["level_id"])
        levels = get_levels()["levels"]
        if i < 0 or i + 1 >= len(levels):
            return redirect(url_for("learning.menu"))
        if not p.get("course", {}).get(p["level_id"], {}).get("passed", False):
            return redirect(url_for("learning.course.overview", level_id=p["level_id"]))
        next_id = levels[i + 1]["id"]
        gate = current_app.config.get('LEARNING_LEVEL_GATE')
        if gate:
            response = gate(next_id)
            if response is not None:
                return response
        p["level_id"] = next_id
        p["step_index"] = 0
        save_progress(p)
        return redirect(
            url_for(
                "learning.level_view",
                level_id=next_id
            )
        )


    @bp.route("/reset")
    @progress_transaction
    def reset():
        p = get_progress()
        study = p["study"]
        fresh = default_progress()
        if "_storage_revision" in p:
            fresh["_storage_revision"] = p["_storage_revision"]
        fresh["course"] = p.get("course", {})
        fresh["study"] = study
        fresh["daily_dose"] = p.get("daily_dose", {})
        fresh["mastery"] = p.get("mastery", {})
        fresh["vocabulary_words"] = p.get("vocabulary_words", {})
        # Preserve existing game progress.
        fresh["games"] = p.get("games", {})
        save_progress(fresh)
        return redirect(url_for("learning.menu"))
