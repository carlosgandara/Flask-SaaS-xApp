"""Storage interfaces independent of Flask, authentication and database engines."""
from typing import Protocol


class ContentStore(Protocol):
    def list_languages(self) -> list[str]: ...
    def get_concepts(self) -> dict: ...
    def get_vocabulary(self, language: str) -> dict: ...
    def get_levels(self, language: str) -> dict: ...
    def get_curriculum(self) -> dict: ...


class ProgressStore(Protocol):
    def load(self, learner_id, language: str, default: dict) -> dict: ...
    def save(self, learner_id, language: str, progress: dict) -> None: ...


class StorageConflictError(RuntimeError):
    """A stale write was rejected; reload the latest state rather than overwrite it."""


class StorageAccessError(RuntimeError):
    def __init__(self, operation, path, cause):
        self.operation = operation
        self.path = str(path)
        self.errno = getattr(cause, "errno", None)
        self.winerror = getattr(cause, "winerror", None)
        super().__init__(f"Storage {operation} failed for {path}: {type(cause).__name__}")
