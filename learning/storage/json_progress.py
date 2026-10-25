"""Progress persistence boundary; keys always include learner and language."""
import json
import os
import hashlib
import logging
import time
from pathlib import Path
from uuid import uuid4
from .contracts import StorageAccessError


class ProgressStorageError(StorageAccessError):
    """Keep storage context and the original exception without losing learner data."""


class JsonProgressStore:
    def __init__(self, directory, retry_delays=(0.05, 0.15, 0.3)):
        self.directory = Path(directory)
        self.retry_delays = retry_delays

    def retry(self, action):
        # Windows replacement/read sharing conflicts can be transient. Real ACL
        # failures still surface after a short bounded wait; never retry forever.
        for attempt in range(len(self.retry_delays) + 1):
            try:
                return action()
            except PermissionError:
                if attempt == len(self.retry_delays):
                    raise
                time.sleep(self.retry_delays[attempt])

    def path(self, learner_id, language):
        learner = "local-user" if learner_id == "local-user" else hashlib.sha256(str(learner_id).encode()).hexdigest()
        if language not in {"tl", "es", "fr"}:
            raise ValueError("Unsupported language")
        return self.directory / learner / f"{language}.json"

    def load(self, learner_id, language, default):
        path = self.path(learner_id, language)
        try:
            raw = self.retry(lambda: path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except UnicodeError as error:
            raise ProgressStorageError("decode", path, error) from error
        except OSError as error:
            raise ProgressStorageError("read", path, error) from error
        try:
            progress = json.loads(raw)
            if not isinstance(progress, dict):
                raise ValueError("Progress must be a JSON object")
            return progress
        except (ValueError, UnicodeError) as error:
            raise ProgressStorageError("decode", path, error) from error

    def save(self, learner_id, language, progress):
        path = self.path(learner_id, language)
        temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        operation = "prepare directory"
        try:
            self.retry(lambda: path.parent.mkdir(parents=True, exist_ok=True))
            operation = "write temporary file"
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(progress, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            operation = "replace"
            self.retry(lambda: os.replace(temp, path))
        except OSError as error:
            raise ProgressStorageError(operation, path, error) from error
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not replace the real write failure in the traceback.
                logging.getLogger(__name__).warning("Progress temporary file cleanup failed: %s", temp, exc_info=True)

    def check_access(self, learner_id, language):
        """Read real progress and replace a disposable probe; never rewrite progress."""
        path = self.path(learner_id, language)
        self.load(learner_id, language, {})
        probe = path.with_name(".access-check-" + uuid4().hex)
        replacement = probe.with_suffix(".replacement")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("probe", encoding="utf-8")
            replacement.write_text("replacement", encoding="utf-8")
            self.retry(lambda: os.replace(replacement, probe))
        except OSError as error:
            raise ProgressStorageError("access check", path, error) from error
        finally:
            for candidate in (probe, replacement):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).warning("Storage probe cleanup failed: %s", candidate, exc_info=True)
        return str(path)
