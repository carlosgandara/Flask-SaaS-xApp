"""Study time and daily goal endpoints."""
from datetime import date
from flask import request, jsonify
from ..services.progress import get_progress, save_progress, progress_lock


def register_routes(bp):
    @bp.route("/study-time", methods=["GET"])
    def study_time():
        with progress_lock:
            p = get_progress()
            study = p["study"]
            today = date.today().isoformat()
            today_seconds = study[
                "days"
            ].get(today, 0)
            goal_seconds = (
                study["daily_goal_minutes"] * 60
            )
        return jsonify({
            "today_seconds": today_seconds,
            "total_seconds": study["total_seconds"],
            "daily_goal_minutes": (
                study["daily_goal_minutes"]
            ),
            "goal_met": (
                today_seconds >= goal_seconds
            )
        })


    @bp.route("/study-time", methods=["POST"])
    def save_study_time():
        data = request.get_json(silent=True) or {}
        try:
            seconds = int(data.get("seconds", 0))
        except (TypeError, ValueError):
            seconds = 0
        seconds = max(0, min(seconds, 60))
        with progress_lock:
            p = get_progress()
            today = date.today().isoformat()
            study = p["study"]
            study["days"][today] = (
                study["days"].get(today, 0)
                + seconds
            )
            study["total_seconds"] += seconds
            save_progress(p)
            today_seconds = study["days"][today]
            total_seconds = study["total_seconds"]
        return jsonify({
            "ok": True,
            "today_seconds": today_seconds,
            "total_seconds": total_seconds
        })


    @bp.route("/study-goal", methods=["POST"])
    def study_goal():
        data = request.get_json(silent=True) or {}
        try:
            minutes = int(
                data.get("minutes", 120)
            )
        except (TypeError, ValueError):
            minutes = 120
        minutes = max(5, min(minutes, 480))
        with progress_lock:
            p = get_progress()
            p["study"][
                "daily_goal_minutes"
            ] = minutes
            save_progress(p)
        return jsonify({
            "ok": True,
            "daily_goal_minutes": minutes
        })
