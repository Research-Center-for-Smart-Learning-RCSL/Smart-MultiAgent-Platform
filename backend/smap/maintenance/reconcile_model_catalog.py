"""Reconcile the per-model capability table against one provider's live
`models` endpoint (Q-4, AC-11).

`docs/tasks/2026-08-27-provider-model-capability-table` replaced three
per-provider dictionaries and five per-model regexes with one capability
table (`contexts.agents.domain.model_specs`), each row carrying a source URL
and a verification date (Q-4's provenance requirement). A table-wide comment
claiming the whole block was verified once cannot say which row a later
reader should distrust -- and it went stale anyway (the pre-existing
`domain/models.py` comment claimed "2026-06" and had not been re-checked).

**Read-only and report-only, deliberately.** It never edits
`model_specs.py`: adding a newly-seen model still means authoring its
capability fields by hand (accepts_effort, accepts_sampling, ...), which this
command has no way to discover from a bare model-id list (Q-2's whole point
is that guessing capabilities from an id is the failure mode this table
exists to close). Run once per provider, read the report, edit
`model_specs.py` yourself, re-run to confirm.

**Never a request-path call** (Q-4 non-goal): this is an operator command
invoked by hand against the operator's own provider keys, never invoked from
turn_engine or any other request path.

Reads the provider key via the same envelope-decrypt path every adapter call
uses (`KeysFacade.unwrap_api_key_plaintext`) -- never accepted on the command
line, never logged. Output is model ids only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from contexts.agents.domain.model_specs import specs_for_provider
from contexts.keys.interfaces.facade import KeysFacade
from shared_kernel.db.session import get_sessionmaker

_MODELS_URL: dict[str, str] = {
    "claude": "https://api.anthropic.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}

_ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class ReconcileReport:
    """What one provider's live `models` list disagrees with the table on.

    ``stale`` is catalogued but no longer served -- every request against it
    now 400s or 404s, silently, until an operator reads this report.
    ``unseen`` is served but not catalogued -- Q-2's conservative floor
    already keeps a BYO-key user's choice of it from failing every turn; this
    is a "consider adding a row" list, not an urgent fix.
    """

    provider: str
    catalogued: frozenset[str]
    upstream: frozenset[str]
    stale: frozenset[str] = field(init=False)
    unseen: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stale", self.catalogued - self.upstream)
        object.__setattr__(self, "unseen", self.upstream - self.catalogued)


def diff_against_upstream(provider: str, upstream_ids: frozenset[str]) -> ReconcileReport:
    """Pure diff, unit-testable without a live key or network access."""
    catalogued = frozenset(spec.model_id for spec in specs_for_provider(provider))
    return ReconcileReport(provider=provider, catalogued=catalogued, upstream=upstream_ids)


def _headers(provider: str, secret: str) -> dict[str, str]:
    if provider == "claude":
        return {"x-api-key": secret, "anthropic-version": _ANTHROPIC_VERSION}
    if provider == "openai":
        return {"Authorization": f"Bearer {secret}"}
    if provider == "gemini":
        return {"x-goog-api-key": secret}
    raise ValueError(f"unknown provider {provider!r}")


def _parse_page(provider: str, body: dict[str, Any]) -> tuple[frozenset[str], str | None]:
    """One page of model ids, plus the cursor for the next page (``None`` when
    this is the last page). Only Anthropic and Gemini paginate; OpenAI's
    `/v1/models` returns the whole list in one response."""
    if provider == "gemini":
        # `models/gemini-x` -> `gemini-x`, matching the bare id every adapter
        # and the capability table use.
        ids = frozenset(str(m["name"]).removeprefix("models/") for m in body.get("models", []) if "name" in m)
        return ids, body.get("nextPageToken")
    if provider == "claude":
        ids = frozenset(str(m["id"]) for m in body.get("data", []) if "id" in m)
        return ids, (body.get("last_id") if body.get("has_more") else None)
    # OpenAI: {"data": [{"id": "..."}, ...]}, not paginated.
    return frozenset(str(m["id"]) for m in body.get("data", []) if "id" in m), None


async def _fetch_upstream_model_ids(provider: str, secret: str) -> frozenset[str]:
    ids: set[str] = set()
    cursor: str | None = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params = {}
            if cursor is not None:
                params = {"pageToken": cursor} if provider == "gemini" else {"after_id": cursor}
            resp = await client.get(_MODELS_URL[provider], headers=_headers(provider, secret), params=params)
            resp.raise_for_status()
            page_ids, cursor = _parse_page(provider, resp.json())
            ids |= page_ids
            if cursor is None:
                break
    return frozenset(ids)


async def run(*, provider: str, key_id: uuid.UUID) -> ReconcileReport:
    if provider not in _MODELS_URL:
        raise ValueError(f"unknown provider {provider!r}; expected one of {sorted(_MODELS_URL)}")

    maker = get_sessionmaker()
    async with maker() as db:
        facade = KeysFacade(db)
        key = await facade.get_key(key_id)
        if key is None:
            raise ValueError(f"no active api_keys row for {key_id}")
        if key.provider.value != provider:
            # A mistyped --key-id would otherwise decrypt whatever key that id
            # maps to and send it, as an Authorization header, to a DIFFERENT
            # provider's endpoint -- a real credential sent to the wrong
            # third party. Caught before the key is ever unwrapped.
            raise ValueError(
                f"key {key_id} belongs to provider {key.provider.value!r}, not {provider!r} -- refusing "
                "to send it to the wrong provider's endpoint"
            )
        plaintext = await facade.unwrap_api_key_plaintext(key_id)
    try:
        upstream = await _fetch_upstream_model_ids(provider, plaintext.decode())
    finally:
        # Caller responsibility per unwrap_api_key_plaintext's own contract.
        plaintext[:] = b"\x00" * len(plaintext)

    return diff_against_upstream(provider, upstream)


__all__ = ["ReconcileReport", "diff_against_upstream", "run"]
