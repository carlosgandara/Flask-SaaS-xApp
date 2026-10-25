"""Read language-specific vocabulary records, including legacy string values."""


def vocabulary_entry(value):
    if isinstance(value, str):
        value = {"translation": value}
    if not isinstance(value, dict):
        return None
    translation = value.get("translation")
    if not isinstance(translation, str) or not translation.strip():
        return None
    translation = translation.strip()
    answers = value.get("accepted_answers", [])
    if not isinstance(answers, list):
        answers = []
    answers = list(dict.fromkeys([translation] + [answer.strip() for answer in answers
                                                if isinstance(answer, str) and answer.strip()]))
    return {"translation": translation, "accepted_answers": answers,
            "example": value.get("example"),
            "part_of_speech": value.get("part_of_speech"),
            "pronunciation": value.get("pronunciation"),
            "audio": value.get("audio"),
            "usage_notes": value.get("usage_notes", []),
            "forms": value.get("forms", [])}
