"""Lesson lookup, unlocking and drill selection."""
from .content import get_levels
from .progress import get_progress


def get_level(level_id):
    for level in get_levels()["levels"]:
        if level["id"] == level_id:
            return level
    return None


def get_level_index(level_id):
    for i, level in enumerate(
        get_levels()["levels"]
    ):
        if level["id"] == level_id:
            return i
    return -1


def is_level_unlocked(
    level_id,
    completed_levels,
    progress=None,
):
    i = get_level_index(level_id)
    if i < 0:
        return False
    if i == 0:
        return True
    previous_id = get_levels()[
        "levels"
    ][i - 1]["id"]
    progress = get_progress() if progress is None else progress
    return progress.get("course", {}).get(previous_id, {}).get("passed", False)


def level_status(
    level_id,
    completed_levels,
    progress=None,
):
    progress = get_progress() if progress is None else progress
    if progress.get("course", {}).get(level_id, {}).get("passed", False):
        return "completed"
    if is_level_unlocked(
        level_id,
        completed_levels,
        progress=progress,
    ):
        return "unlocked"
    return "locked"


def find_first_step_for_word(
    word_key,
    level_id
):
    level = get_level(level_id)
    if not level:
        return None
    first_preview = None
    first_word_step = None
    for i, step in enumerate(level["steps"]):
        if (
            step["type"] == "preview"
            and word_key in step.get("words", [])
        ):
            if first_preview is None:
                first_preview = i
        if (
            step["type"] == "word"
            and step.get("key") == word_key
        ):
            if first_word_step is None:
                first_word_step = i
    if first_preview is not None:
        return first_preview
    return first_word_step


def pick_drill_words(
    progress,
    n=14,
    must_include=None
):
    must_include = must_include or []
    rows = []
    for key, rec in progress["words"].items():
        if key in must_include:
            continue
        correct = rec.get("correct", 0)
        wrong = rec.get("wrong", 0)
        if correct + wrong == 0:
            continue
        score = (wrong * 2) - correct
        rows.append((score, key))
    rows.sort(reverse=True)
    return (
        list(must_include)
        + [
            key for _, key in rows[
                :max(0, n - len(must_include))
            ]
        ]
    )
