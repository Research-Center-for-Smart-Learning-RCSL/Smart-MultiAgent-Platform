"""Every mapped Capability must be enforced somewhere.

The systemic guard. F-2 (chat export) was a capability that was declared,
mapped into the matrix, and unit-tested, yet consulted by no route or service:
dead authorization. Nothing would have caught it, because nothing asserted that
a mapped capability is actually read at a decision point. This test is that
assertion.

A capability counts as enforced when its enum member is referenced (by symbol)
anywhere under `app/` or `contexts/` outside the matrix module itself, OR when
it appears in `_ENFORCED_BY_OTHER_MEANS` with a cited reason. The exemption set
is a hard-coded frozenset so that adding a new capability without wiring it
fails here rather than silently joining the exemptions.
"""

from __future__ import annotations

from pathlib import Path

from shared_kernel.auth.permissions import Capability

# Repo layout: this file is backend/tests/unit/, the scanned trees are
# backend/app and backend/contexts.
_BACKEND = Path(__file__).resolve().parents[2]
_SCANNED_TREES = (_BACKEND / "app", _BACKEND / "contexts")
# The matrix declares the capabilities; a reference there is not enforcement.
_MATRIX_MODULE = _BACKEND / "shared_kernel" / "auth" / "permissions.py"


# Capabilities enforced by a mechanism other than a symbol reference at a
# decision point. Each entry states why, with the §6 sibling-sweep evidence, so
# an exemption is a documented decision, not a silent hole.
_ENFORCED_BY_OTHER_MEANS: dict[Capability, str] = {
    # Row 1: universal deny, short-circuited ahead of the admin bypass in
    # decide() and enforced by the absence of any plaintext-key endpoint. "No
    # one, ever" is correctly implemented as no code path at all.
    Capability.KEY_VIEW_PLAINTEXT: "universal deny; enforced by having no endpoint",
    # Rows 21-24: empty matrix rows (deny for every non-admin role) plus a
    # dedicated require_admin dependency on the admin routers. An empty row and
    # an admin-only dependency are equivalent enforcement, not a gap.
    Capability.AUDIT_VIEW: "admin-only via require_admin on the audit router",
    Capability.USER_BAN: "admin-only via require_admin on admin_users/admin_ip_bans",
    Capability.USER_DELETE_ANY: "admin-only via require_admin on admin_users",
    Capability.USER_READ_ANY: "admin-only via require_admin on admin_users",
    # Row 20: the matrix logic is re-implemented inline at
    # app/api/v1/messages.py against RoomAccess, deliberately (the comment there
    # says so), because decide() cannot resolve the guest tier. Behaviourally
    # correct; structurally a second copy of the rule, tracked as FU-1. The
    # symbol is therefore not referenced, so it is exempted here with the
    # enforcing site named rather than being silently absent.
    Capability.MESSAGE_DELETE: "inline against RoomAccess at app/api/v1/messages.py (FU-1)",
}


def _symbol_referenced(cap: Capability) -> bool:
    needle = f"Capability.{cap.name}"
    for tree in _SCANNED_TREES:
        for path in tree.rglob("*.py"):
            if path == _MATRIX_MODULE:
                continue
            if needle in path.read_text(encoding="utf-8"):
                return True
    return False


def test_every_mapped_capability_is_enforced_somewhere() -> None:
    unwired: list[str] = []
    for cap in Capability:
        if cap in _ENFORCED_BY_OTHER_MEANS:
            continue
        if not _symbol_referenced(cap):
            unwired.append(cap.value)
    assert not unwired, (
        "These capabilities are mapped but referenced by no route or service, "
        "so their authorization rule is dead code (this is the F-2 defect "
        f"class): {unwired}. Wire the check, or add the capability to "
        "_ENFORCED_BY_OTHER_MEANS with the reason it is enforced elsewhere."
    )


def test_exemptions_are_still_capabilities() -> None:
    """A renamed or removed capability must not leave a stale exemption behind,
    which would silently re-open enforcement for whatever inherits the name."""
    for cap in _ENFORCED_BY_OTHER_MEANS:
        assert cap in set(Capability)


def test_chat_export_is_wired() -> None:
    """Regression pin for F-2 specifically: this is the assertion that would
    have caught the disclosure the moment it was introduced."""
    assert Capability.CHAT_EXPORT not in _ENFORCED_BY_OTHER_MEANS
    assert _symbol_referenced(Capability.CHAT_EXPORT)
