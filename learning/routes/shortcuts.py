"""Compatibility URLs for existing Daily Dose and Word Shooter links."""
from flask import redirect, url_for, jsonify
from ..services.progress import get_progress


def register_routes(bp):
    @bp.route("/daily-dose")
    def daily_dose_page():
        return redirect(url_for("learning.course.overview", level_id=get_progress()["level_id"]))


    @bp.route("/daily-dose/answer", methods=["POST"])
    def daily_dose_answer():
        return jsonify(ok=False, error="Use the level Daily Dose activity"), 410


    @bp.route("/word-shooter")
    def word_shooter():
        return redirect(url_for("learning.course.shooter", level_id=get_progress()["level_id"]))


    @bp.route("/word-shooter-answer", methods=["POST"])
    def word_shooter_answer():
        return jsonify(ok=False, error="Use the level practice activity"), 410
