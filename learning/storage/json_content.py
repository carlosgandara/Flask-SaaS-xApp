"""Read-only local content repository matching the future database interface."""
import json
from pathlib import Path
from .contracts import StorageAccessError


class JsonContentStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def read(self, relative, default):
        path = self.directory / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError, UnicodeError) as error:
            raise StorageAccessError("read content",path,error) from error

    def list_languages(self):
        lessons = self.directory / "languages" / "lessons"
        return sorted(folder.name for folder in lessons.iterdir()
                      if folder.is_dir() and (folder / "levels.json").is_file())

    @staticmethod
    def validate_language(language):
        if not isinstance(language,str) or not language or any(c not in "abcdefghijklmnopqrstuvwxyz-" for c in language):
            raise ValueError("Invalid language code")

    def get_concepts(self):
        return self.read("concepts.json", {})

    def get_vocabulary(self, language):
        self.validate_language(language)
        return self.read(Path("languages/vocabulary") / f"{language}.json", {})

    def get_levels(self, language):
        self.validate_language(language)
        return self.read(Path("languages/lessons") / language / "levels.json", {"levels":[]})

    def get_curriculum(self):
        return self.read("curriculum.json", {})
