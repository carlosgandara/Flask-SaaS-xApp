"""Explicit dependencies shared by the course and matching activities."""
from . import content, progress, lesson_helpers


class LearningServices:
    sources = {
        "get_progress": progress, "save_progress": progress, "progress_lock": progress,
        "get_levels": content,
        "content_store": content, "dose_catalog": content, "dose_normalize": content,
        "get_level": lesson_helpers,
    }

    def __getitem__(self, name):
        value = getattr(self.sources[name], name)
        return value() if name == "content_store" else value
