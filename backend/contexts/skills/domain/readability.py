"""When a skill may be served to a model (AC-34 / [R31.20]).

A pure rule over domain objects, in `domain/` rather than beside the file service,
because it has two callers on opposite sides of a context boundary: the file service, and
the agents runtime's `read_skill`. The runtime may not import `skills.application`
(backend/CLAUDE.md), and a facade passthrough for a function with no session and no query
would be ceremony around a predicate. `domain` imports nothing, so this edge cannot cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

from contexts.skills.domain.errors import SkillUnreadable
from contexts.skills.domain.models import Skill, SkillFile, SkillScanStatus


def assert_readable(skill: Skill, files: Sequence[SkillFile]) -> None:
    """Raise `SkillUnreadable` unless every one of `files` is scan-clean.

    **Fail-closed, and knowingly stricter than the RAG precedent**, which treats
    `skipped` as usable and serves the document anyway. The asymmetry is the premise of
    §8: a RAG chunk is data the model reads, a skill is instructions it executes, and
    [R31.20] says every bundled file passes the malware scan *before the skill becomes
    readable*. `skipped` did not pass. So `clean` is the only status that serves, and
    `pending`, `skipped`, and `quarantined` all refuse.

    **Whole-skill, not per-file**, per Q-18's reasoning: a `SKILL.md` referencing a file
    the model cannot fetch induces confabulation. Refusing the skill outright is the
    honest failure; serving a body whose references are missing is the dishonest one.
    """
    for f in files:
        if f.scan_status is not SkillScanStatus.CLEAN:
            raise SkillUnreadable(skill.name, path=f.path)


__all__ = ["assert_readable"]
