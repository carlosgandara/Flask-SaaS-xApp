# Learning package

The repository's `app.py` combines this package with authentication, billing and
Neon account progress through `integration.py`. One server runs the whole app.

| Location | Responsibility |
| --- | --- |
| `__init__.py` | Factory, blueprint registration, identity and request-policy hooks |
| `routes/menu.py` | Language selection, menu and landing page |
| `routes/lessons.py` | Lesson pages, answers and progression |
| `routes/course.py` | Daily Dose, practice and final tests |
| `routes/word_shooter.py` | Required missed-word support |
| `routes/arcade.py` | Free shooter, independent of lesson completion |
| `routes/word_match.py` | Matching boards and flips |
| `routes/study.py` | Study time and daily goals |
| `routes/shortcuts.py` | Existing activity shortcut URLs |
| `services/` | Content, progress, mastery rules and adaptive word selection |
| `storage/` | Contracts, JSON repositories and database adapters |
| `data/` | Concepts, translations, lessons and curriculum |
| `templates/learning/` | Templates namespaced to coexist with host templates |
| `static/` | Scripts, styles and images served at /learning/static/ |
| `tests/` | Feature, repository, package and browser-client checks |
| `diagnostics.py` | Diagnostics helpers used by isolated package tests |
| `docs/` | Integration and debugging guides |

Page URLs remain the same; Flask endpoint names begin with learning.
Feature routes read content through repositories, not directly from JSON paths.
See [integration](docs/INTEGRATION.md) and [debugging](docs/DEBUGGING.md).

The public learning map shows guest levels 1–3, free-account levels 4–6, and
membership levels 7–40. All 40 vocabulary sets are shared across es/fr/tl.
