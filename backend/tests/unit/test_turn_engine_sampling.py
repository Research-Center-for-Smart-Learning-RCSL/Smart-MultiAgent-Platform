"""R9.18 — `_sampling_payload` merges only the agent's set sampling controls.

The turn engine folds this fragment into both provider payloads (the tool-loop
request and the final request). Unset controls must be omitted so provider
defaults are preserved; a set control of `0.0` must be included (not dropped by
a truthiness check), since `temperature=0` is the reproducibility lever.
"""

from __future__ import annotations

from types import SimpleNamespace

from contexts.agents.application.runtime.turn_engine import _sampling_payload


def _agent(*, temperature=None, top_p=None, seed=None) -> SimpleNamespace:
    return SimpleNamespace(temperature=temperature, top_p=top_p, seed=seed)


def test_all_unset_yields_empty_fragment() -> None:
    assert _sampling_payload(_agent()) == {}


def test_zero_temperature_is_included_not_dropped() -> None:
    # temperature=0 is the low-variance setting; a truthiness check would lose it.
    assert _sampling_payload(_agent(temperature=0.0)) == {"temperature": 0.0}


def test_all_set_values_merged() -> None:
    assert _sampling_payload(_agent(temperature=0.0, top_p=1.0, seed=42)) == {
        "temperature": 0.0,
        "top_p": 1.0,
        "seed": 42,
    }


def test_partial_set_omits_unset_keys() -> None:
    assert _sampling_payload(_agent(top_p=0.9)) == {"top_p": 0.9}
    assert "temperature" not in _sampling_payload(_agent(seed=7))
    assert "top_p" not in _sampling_payload(_agent(seed=7))
