"""`merge_json_config` — additive writes for free-form JSONB columns."""

from __future__ import annotations

from shared_kernel.json_merge import merge_json_config


def test_unmentioned_keys_survive() -> None:
    stored = {"a": 1, "b": 2}
    assert merge_json_config(stored, {"a": 9}) == {"a": 9, "b": 2}


def test_nested_dicts_recurse() -> None:
    stored = {"triggers": {"every_n": {"enabled": True, "n": 3}, "silence": {"t": 2}}}
    patch = {"triggers": {"every_n": {"n": 8}}}
    assert merge_json_config(stored, patch) == {
        "triggers": {"every_n": {"enabled": True, "n": 8}, "silence": {"t": 2}}
    }


def test_scalar_replaces_dict_and_dict_replaces_scalar() -> None:
    assert merge_json_config({"a": {"b": 1}}, {"a": 5}) == {"a": 5}
    assert merge_json_config({"a": 5}, {"a": {"b": 1}}) == {"a": {"b": 1}}


def test_lists_replace_wholesale() -> None:
    assert merge_json_config({"a": [1, 2, 3]}, {"a": [9]}) == {"a": [9]}


def test_null_deletes_at_root() -> None:
    assert merge_json_config({"a": 1, "b": 2}, {"a": None}) == {"b": 2}


def test_null_deletes_nested() -> None:
    stored = {"outer": {"keep": 1, "drop": 2}}
    assert merge_json_config(stored, {"outer": {"drop": None}}) == {"outer": {"keep": 1}}


def test_null_for_an_absent_key_is_not_an_error() -> None:
    assert merge_json_config({"a": 1}, {"missing": None}) == {"a": 1}


def test_empty_patch_is_identity() -> None:
    assert merge_json_config({"a": {"b": 1}}, {}) == {"a": {"b": 1}}


def test_empty_stored_returns_the_patch() -> None:
    assert merge_json_config({}, {"a": 1}) == {"a": 1}


def test_stored_is_not_mutated() -> None:
    """The caller's dict is a loaded ORM value; mutating it in place would make
    the pre-merge state unavailable to anything else holding the same object."""
    stored = {"outer": {"keep": 1}}
    merged = merge_json_config(stored, {"outer": {"added": 2}, "root": 3})

    assert stored == {"outer": {"keep": 1}}
    assert merged == {"outer": {"keep": 1, "added": 2}, "root": 3}
    assert merged["outer"] is not stored["outer"]
