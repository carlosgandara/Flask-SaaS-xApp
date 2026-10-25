"""Language selection, menu and landing pages."""
from datetime import date
from flask import render_template, redirect, url_for, current_app
from ..services.content import current_language, language_name, languages, get_levels
from ..services.progress import get_progress
from ..services.lesson_helpers import level_status


def register_routes(bp, include_home=True):
    @bp.context_processor
    def inject_language():
        return {
            "current_language": current_language(),
            "language_name": language_name(),
            "available_languages": languages()
        }


    @bp.route("/language/<language>")
    def select_language(language):
        if language not in languages():
            return "Unsupported language", 404
        response = redirect(url_for("learning.menu"))
        response.set_cookie(
            "language",
            language,
            max_age=31536000,
            httponly=True,
            samesite="Lax"
        )
        return response


    def home():
        return redirect(url_for("learning.menu"))
    if include_home:
        bp.add_url_rule("/",view_func=home)


    @bp.route("/menu")
    def menu():
        p = get_progress()
        completed = p.get(
            "completed_levels", []
        )
        current_id = p["level_id"]
        levels_view = []
        levels = get_levels()["levels"]
        for i, level in enumerate(levels):
            status = level_status(
                level["id"],
                completed,
                progress=p,
            )
            if (
                level["id"] == current_id
                and status != "completed"
            ):
                progress_info = (
                    f"Step {p['step_index'] + 1} "
                    f"of {len(level['steps'])}"
                )
            elif status == "completed":
                progress_info = "Completed"
            elif status == "locked":
                previous = levels[i - 1]
                progress_info = (
                    f'Complete "{previous["title"]}" '
                    f"to unlock"
                )
            else:
                progress_info = "Start lesson"
            levels_view.append({
                "id": level["id"],
                "title": level["title"],
                "description": level["description"],
                "status": status,
                "progress_info": progress_info,
                "is_current": (
                    level["id"] == current_id
                )
            })
        for card in levels_view:
            state = p.get("course", {}).get(card["id"], {})
            if card["status"] != "locked":
                card["progress_info"] = ("Final test passed" if state.get("passed") else
                                         "Continue vocabulary / test" if state.get("lesson_done") else
                                         "Start / continue lesson")
        today = date.today().isoformat()
        study = p["study"]
        return render_template(
            "learning/menu.html",
            levels=levels_view,
            study=study,
            today_seconds=study["days"].get(
                today, 0
            )
        )


    @bp.route("/despacio")
    def despacio_home():
        return render_template("learning/despacio_home.html")
