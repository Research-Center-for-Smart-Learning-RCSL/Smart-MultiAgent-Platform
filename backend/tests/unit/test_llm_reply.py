"""Lenient JSON-object read of a model reply (shared by the judge and the design run)."""

from __future__ import annotations

import time

import pytest

from shared_kernel.llm_reply import ReplyNotJsonObject, load_json_object


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Here you go: {"a": 1} hope it helps',
        '  {"a": 1}  ',
    ],
)
def test_reads_the_object_through_fences_and_prose(text: str) -> None:
    assert load_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json", "}{", "[1, 2]", "{not json}", '"str"'])
def test_refuses_anything_but_an_object(text: str) -> None:
    with pytest.raises(ReplyNotJsonObject):
        load_json_object(text)


def test_unclosed_braces_are_refused_in_linear_time() -> None:
    # A provider behind a custom base_url controls the reply; the old regex took
    # about 12 s on this input and blocked the worker's event loop.
    started = time.perf_counter()
    with pytest.raises(ReplyNotJsonObject):
        load_json_object("{" * 76_000)
    assert time.perf_counter() - started < 0.5
