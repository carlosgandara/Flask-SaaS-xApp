"""Language selection and read-only lesson/vocabulary content."""
from copy import deepcopy
from pathlib import Path
import langcodes
import unicodedata
from flask import request, g, current_app, url_for, has_app_context
from ..storage.json_content import JsonContentStore
from .vocabulary_entries import vocabulary_entry
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
LESSONS_DIR = DATA_DIR / "languages" / "lessons"
DEFAULT_LANGUAGE = "tl"


def content_store():
    return current_app.config["LEARNING_CONTENT_STORE"] if has_app_context() else JsonContentStore(DATA_DIR)


def content_cache():
    return current_app.extensions["learning"]["content_cache"] if has_app_context() else {}


def languages():
    return current_app.extensions["learning"]["languages"] if has_app_context() else LANGUAGES


def discover_languages(store=None):
    return {code:langcodes.Language.get(code).display_name("en")
            for code in (store or JsonContentStore(DATA_DIR)).list_languages()}


def current_language():
    available = languages()
    fallback = DEFAULT_LANGUAGE if DEFAULT_LANGUAGE in available else next(iter(available))
    language = request.cookies.get("language", fallback)
    return language if language in available else fallback


def language_name():
    return languages()[current_language()]


def expand_steps(steps):
    """Expand level-specific teaching steps without using vocabulary files."""
    out = []
    for step in steps:
        if step.get("type") != "teach":
            out.append(step)
            continue
        words = step.get("new_words", [])
        definitions = step.get("word_definitions", {})
        out.append({
            "type": "preview",
            "phrase": step["phrase"],
            "translation": step["translation"],
            "words": words,
        })
        for key in words:
            out.append({
                "type": "word",
                "key": key,
                "phrase": step["phrase"],
                "translation": step["translation"],
                "english": definitions.get(key, ""),
            })
        out.append({
            "type": "preview",
            "phrase": step["phrase"],
            "translation": step["translation"],
            "words": words,
            "note": step.get("note", "Now you see the pieces."),
        })
    return out


def normalize_vocabulary(raw):
    """
    Accept either the old vocabulary format:
    {"words": {"kape": {"english": "coffee"}}}
    or language mappings:
    {"concept-0001": "kape"}
    Lesson vocabulary remains keyed by the actual
    words used in the lesson's new_words array.
    """
    if not isinstance(raw, dict):
        return {"words": {}}
    if "words" in raw:
        return raw
    return {"words": raw}


def load_language_data(language):
    """Load language levels without opening any vocabulary files."""
    if language not in languages():
        raise ValueError("Unsupported language")
    key = ("levels", language)
    cache = content_cache()
    if key in cache:
        return cache[key]
    levels = deepcopy(content_store().get_levels(language))
    if not isinstance(levels, dict) or not levels.get("levels"):
        raise FileNotFoundError(f"No lessons found for {language} in the configured content store")
    for level in levels["levels"]:
        level["steps"] = expand_steps(level["steps"])
    result = {"levels": levels}
    cache[key] = result
    return result


def load_vocabulary_data(language):
    """Load independent vocabulary concepts and translations."""
    if language not in languages():
        raise ValueError("Unsupported language")
    key = ("vocabulary", language)
    cache = content_cache()
    if key in cache:
        return cache[key]
    # 2. Load the language's vocabulary.
    translations = content_store().get_vocabulary(language)
    # 3. Load shared English concepts.
    concepts = content_store().get_concepts()
    # Support concepts stored as a dictionary or list.
    if isinstance(concepts, dict):
        concept_items = concepts.get("concepts", concepts)
    else:
        concept_items = concepts
    # Support concept-ID-to-word mappings.
    if isinstance(translations, dict):
        translation_items = translations.get(
            "translations", translations
        )
    else:
        translation_items = {}
    vocabulary = {"words": {}}
    if isinstance(concept_items, dict):
        concept_iter = []
        for concept_id, concept_data in concept_items.items():
            if isinstance(concept_data, dict):
                concept_iter.append({
                    "id": concept_id,
                    **concept_data
                })
    elif isinstance(concept_items, list):
        concept_iter = concept_items
    else:
        concept_iter = []
    for concept in concept_iter:
        if not isinstance(concept, dict):
            continue
        concept_id = concept.get("id")
        english = (
            concept.get("label")
            or concept.get("english")
        )
        translated = (
            translation_items.get(concept_id)
            if isinstance(translation_items, dict)
            else None
        )
        entry = vocabulary_entry(translated)
        if not (concept_id and english and entry):
            continue
        word = entry["translation"]
        vocabulary["words"][word] = {
            "english": english,
            "image": concept.get("image", ""),
            **entry,
        }
    # 4. Normalize vocabulary for the existing templates.
    vocabulary = normalize_vocabulary(vocabulary)
    cache[key] = vocabulary
    return vocabulary


def get_lesson_words(level):
    """Word-card data from one level's expanded teaching steps."""
    words = {}
    for step in level.get("steps", []):
        if step.get("type") != "word":
            continue
        key = step.get("key")
        if not key:
            continue
        english = step.get("english") or step.get("definition", "")
        if key not in words or (not words[key]["english"] and english):
            words[key] = {
                "english": english,
                "image": "",
                "phrase": step.get("phrase", ""),
                "translation": step.get("translation", ""),
            }
    return words


def get_all_lesson_words():
    """English definitions from ALL levels in the selected language.

    Used by lesson word cards and drills; independent vocabulary games
    continue using their separate concept-based vocabulary data.
    """
    words = {}
    for level in get_levels()["levels"]:
        for key, details in get_lesson_words(level).items():
            if key not in words or (not words[key]["english"] and details["english"]):
                words[key] = details
    return words


def get_levels():
    if not hasattr(g, "language_data"):
        g.language_data = load_language_data(
            current_language()
        )
    return g.language_data["levels"]


def get_vocabulary():
    return load_vocabulary_data(current_language())


def validate_levels(language):
    """Validate level structure only; no vocabulary-file dependency."""
    levels = load_language_data(language)["levels"]["levels"]
    warnings = []
    for level in levels:
        if not level.get("id"):
            warnings.append("Level has no id")
        if not isinstance(level.get("steps"), list) or not level["steps"]:
            warnings.append(f"{level.get('id', 'unknown')}: missing steps")
        for i, step in enumerate(level.get("steps", [])):
            if step.get("type") == "word" and not step.get("key"):
                warnings.append(f"{level.get('id')} step {i}: missing word key")
    if warnings:
        print(f"\n--- {language} level warnings ---")
        for warning in warnings:
            print(" ", warning)
        print(f"{len(warnings)} warning(s).")
    else:
        print(f"{languages()[language]} levels OK.")


def dose_catalog():
    """Read concepts in source order, with translations for this language."""
    raw = content_store().get_concepts()
    translations = content_store().get_vocabulary(current_language())
    if isinstance(raw, dict):
        raw = raw.get("concepts", raw)
    if isinstance(translations, dict):
        translations = translations.get("translations", translations)
    if not isinstance(translations, dict):
        return {}
    if isinstance(raw, dict):
        items = [dict(details, id=cid) for cid, details in raw.items()
                 if isinstance(details, dict)]
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    catalog = {}
    folder = current_app.config.get("VOCABULARY_IMAGE_FOLDER", "images/vocabulary")
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        english = item.get("label") or item.get("english")
        translated = translations.get(cid) if isinstance(cid, str) else None
        entry = vocabulary_entry(translated)
        if not (cid and english and entry):
            continue
        image = str(item.get("image") or "").strip()
        catalog[cid] = {
            "id": cid,
            "english": str(english),
            "tagalog": entry["translation"],
            **entry,
            "image_url": url_for("learning.static", filename=f"{folder}/{image}") if image else "",
        }
    return catalog


def dose_normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


LANGUAGES = discover_languages()
