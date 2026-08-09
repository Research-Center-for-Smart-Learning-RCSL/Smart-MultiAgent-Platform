"""Shipped worked-example courses, as an adapter over packaged data.

``infrastructure/`` because reading a packaged resource is an adapter over an
external store, which is what this layer means here. Not ``domain/`` — that is the
mypy-strict, import-linter-guarded zone, and a JSON parser reading package data
belongs in neither. Not ``application/`` for the same reason the repositories are
not there.

The catalogue lives inside the context rather than in the ``smap`` CLI package so
the HTTP layer can install an example without ``app/`` importing ``smap`` (see
``backend/smap/__init__.py``: nothing in that namespace is served over HTTP).
``smap.examples._catalogue`` remains as a re-export so the seeder CLI is unchanged.
"""

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
