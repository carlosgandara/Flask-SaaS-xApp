"""Pure course state and mastery rules shared by routes and support activities."""
from uuid import uuid4


def record(progress, level_id):
    return progress.setdefault("course", {}).setdefault(level_id, {})


def stats(state, cid):
    return state.setdefault("words", {}).setdefault(cid, {
        "correct": 0, "wrong": 0, "recall_streak": 0, "mastered": False,
        "misses": 0, "support_correct": 0,
    })


def ready(state, words):
    cycle = state.get("dose_cycle", {})
    return (bool(words) and state.get("lesson_done", False)
            and not state.get("required_support")
            and cycle.get("index", 0) >= len(cycle.get("word_ids", []))
            and all(stats(state, w["id"])["mastered"] for w in words))


def difficult(state, words):
    return [w for w in words if w["id"] in state.get("required_support", [])]


def new_cycle(state, ids):
    state["dose_cycle"] = {"id": uuid4().hex, "word_ids": ids, "index": 0, "missed": []}
    return state["dose_cycle"]


def dashboard(state, words, number):
    mastered = sum(stats(state, w["id"])["mastered"] for w in words)
    card = {"number": number, "total": 10, "mastered": mastered,
            "complete": mastered == 10, "unlocked": True}
    return {"levels": [card], "total_levels": 1, "total_words": 10,
            "mastered_words": mastered, "learning_words": 10 - mastered,
            "completed_levels": int(mastered == 10), "overall_percent": mastered * 10}
