"""Adaptive vocabulary selection without Flask or storage dependencies."""
import random


def select_words(catalog, progress, current_ids, normalize):
    store = progress.get("games", {}).get("word_match", {})
    history = store.get("words", {})
    missed = {}
    for stats in [history, progress.get("mastery", {}), *[s.get("words", {}) for s in progress.get("course", {}).values()]]:
        for cid, item in stats.items():
            if cid not in catalog or not isinstance(item, dict):
                continue
            previous = history.get(cid, {})
            when = item.get("last_missed_at", 0)
            if (item.get("misses", item.get("wrong", 0)) > 0
                    and (when > previous.get("last_matched_at", 0)
                         or previous.get("clean_matches", 0) < 2)):
                missed[cid] = max(missed.get(cid, (0, 0)), (when, item.get("wrong", 0)))
    selected, seen_words, seen_english = [], set(), set()

    def add(cid):
        if cid not in catalog or len(selected) == 10:
            return False
        word = catalog[cid]
        translated, english = normalize(word["tagalog"]), normalize(word["english"])
        if translated in seen_words or english in seen_english:
            return False
        selected.append(word)
        seen_words.add(translated)
        seen_english.add(english)
        return True

    for cid in sorted(missed, key=lambda cid: missed[cid], reverse=True):
        add(cid)
    outside = min(3, store.get("good_rounds", 0) // 2, 10 - len(selected))
    for cid in current_ids:
        if len(selected) >= 10 - outside:
            break
        add(cid)
    extras = [cid for cid in catalog if cid not in current_ids]
    random.SystemRandom().shuffle(extras)
    for cid in extras + list(current_ids):
        add(cid)
    if len(selected) < 10:
        raise ValueError("Ten distinct translated words are required")
    return selected
