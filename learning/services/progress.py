"""Learner progress services; the host app supplies identity and persistence."""
import threading
from functools import wraps
from flask import current_app, abort
from .content import current_language, get_levels
progress_lock = threading.RLock()


def progress_transaction(view):
    """Keep a view's complete read/modify/save sequence together."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        with progress_lock:
            return view(*args, **kwargs)
    return wrapped


def learner_id():
    identity = current_app.config["LEARNING_LEARNER_ID_RESOLVER"]()
    if identity is None or identity == "":
        abort(401)
    return identity


def default_progress():
    levels = get_levels()["levels"]
    return {
        "schema_version": 2,
        "course": {},
        "daily_dose": {"number": 0, "word_ids": [], "completed": False},
        "mastery": {},
        "level_id": (
            levels[0]["id"]
            if levels else None
        ),
        "step_index": 0,
        "words": {},
        "vocabulary_words": {},
        "games": {},
        "completed_levels": [],
        "study": {
            "daily_goal_minutes": 120,
            "total_seconds": 0,
            "days": {}
        }
    }


def get_progress():
    p = current_app.config["LEARNING_PROGRESS_STORE"].load(
        learner_id(), current_language(), default_progress()
    )
    defaults = default_progress()
    for key, value in defaults.items():
        if key not in p:
            p[key] = value
    for key, value in defaults["study"].items():
        p["study"].setdefault(key, value)
    return p


def save_progress(p):
    with progress_lock:
        current_app.config["LEARNING_PROGRESS_STORE"].save(
            learner_id(), current_language(), p
        )
