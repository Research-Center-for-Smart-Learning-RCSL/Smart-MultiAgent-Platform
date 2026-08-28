"""`smap.maintenance.reconcile_model_catalog` diff logic (Q-4, AC-11).

The live HTTP half needs a real provider key and is out of scope for the unit
tier; only the pure diff is covered here.
"""

from __future__ import annotations

from smap.maintenance.reconcile_model_catalog import diff_against_upstream


def test_diff_reports_no_disagreement_when_upstream_matches_the_table() -> None:
    upstream = frozenset({"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"})
    report = diff_against_upstream("claude", upstream)
    assert report.stale == frozenset()
    assert report.unseen == frozenset()


def test_diff_reports_a_catalogued_model_no_longer_served_as_stale() -> None:
    upstream = frozenset({"gpt-5.5", "gpt-5.4-mini"})  # gpt-5.4 missing upstream
    report = diff_against_upstream("openai", upstream)
    assert report.stale == frozenset({"gpt-5.4"})
    assert report.unseen == frozenset()


def test_diff_reports_a_served_model_not_yet_catalogued_as_unseen() -> None:
    upstream = frozenset({"gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.7-flash"})
    report = diff_against_upstream("gemini", upstream)
    assert report.stale == frozenset()
    assert report.unseen == frozenset({"gemini-3.7-flash"})
