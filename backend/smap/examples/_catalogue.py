"""Re-export shim: the catalogue now lives inside the activities context.

The parser and the shipped ``courses/*.json`` moved to
``contexts/activities/infrastructure/examples/`` so the HTTP layer can install an
example without ``app/`` importing ``smap`` — see that package's ``__init__``.

This module stays because the seeder CLI's import path is a contract of its own
(``python -m smap.examples`` is documented in ``docs/examples/``), and because
moving the *data* is the point; moving the import path as well would be churn for
every operator script that already reads from here.
"""

from __future__ import annotations

from contexts.activities.infrastructure.examples.catalogue import (
    COURSES_DIRNAME,
    CourseActivityType,
    CourseDefinition,
    CourseFileInvalid,
    available_courses,
    courses_root,
    load_course,
    parse_course,
)

__all__ = [
    "COURSES_DIRNAME",
    "CourseActivityType",
    "CourseDefinition",
    "CourseFileInvalid",
    "available_courses",
    "courses_root",
    "load_course",
    "parse_course",
]
