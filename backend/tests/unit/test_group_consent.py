"""The consent fraction and what it requires — AC-4, and AC-3's arithmetic half.

Table-driven across group sizes 1 to 10 for `1/1`, `2/3` and `1/2`, per the
dossier's §12. The expectations are written out literally rather than computed
from the same `ceil` the implementation uses: a test that reimplements its
subject proves only that the expression was typed twice.
"""

from __future__ import annotations

import pytest

from contexts.activities.domain.group_consent import (
    MAX_DENOMINATOR,
    is_unreachable,
    parse_group_config,
    required_approvals,
    validate_group_config,
)

# group_size -> required approvals, for each shipped-relevant fraction.
_UNANIMOUS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10}
_TWO_THIRDS = {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 4, 7: 5, 8: 6, 9: 6, 10: 7}
_HALF = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 5, 10: 5}


@pytest.mark.parametrize(
    ("numerator", "denominator", "table"),
    [(1, 1, _UNANIMOUS), (2, 3, _TWO_THIRDS), (1, 2, _HALF)],
    ids=["1/1", "2/3", "1/2"],
)
def test_required_approvals_matches_the_declared_fraction(
    numerator: int, denominator: int, table: dict[int, int]
) -> None:
    for group_size, expected in table.items():
        assert (
            required_approvals(numerator=numerator, denominator=denominator, group_size=group_size)
            == expected
        ), f"{numerator}/{denominator} over {group_size}"


def test_unanimity_is_expressible_and_means_everyone() -> None:
    """`1/1` is one fraction among many, not a special case in the code."""
    for group_size in range(1, 11):
        assert required_approvals(numerator=1, denominator=1, group_size=group_size) == group_size


def test_the_bar_is_never_zero_and_never_more_than_the_group() -> None:
    """The clamp is the rule, not defensive tidying.

    Without the floor, a fine fraction over a small group would require zero
    approvals and a proposal would accept itself. Without the ceiling, a
    fraction of 1 expressed as 100/100 would demand more people than exist for
    any size the denominator does not divide.
    """
    assert required_approvals(numerator=1, denominator=100, group_size=2) == 1
    assert required_approvals(numerator=100, denominator=100, group_size=7) == 7


def test_a_non_positive_group_is_refused_rather_than_answered() -> None:
    with pytest.raises(ValueError, match="group_size"):
        required_approvals(numerator=2, denominator=3, group_size=0)


class TestReachability:
    """AC-8: rejection is "the threshold can no longer be reached", not "someone
    said no"."""

    def test_one_rejection_does_not_end_a_two_thirds_vote(self) -> None:
        # Three voters, bar of 2, one rejection: one approval and one undecided
        # can still carry it.
        assert not is_unreachable(approvals=1, undecided=1, required=2)

    def test_the_second_rejection_does(self) -> None:
        assert is_unreachable(approvals=1, undecided=0, required=2)

    def test_under_unanimity_the_first_rejection_is_the_same_thing(self) -> None:
        # Three voters, bar of 3, one rejection leaves 1 + 1 < 3.
        assert is_unreachable(approvals=1, undecided=1, required=3)

    def test_a_met_threshold_is_never_unreachable(self) -> None:
        assert not is_unreachable(approvals=2, undecided=0, required=2)


class TestValidation:
    def test_the_shipped_example_fraction_parses(self) -> None:
        assert parse_group_config({"consent": {"numerator": 2, "denominator": 3}}) == (2, 3)

    def test_null_means_individual_only(self) -> None:
        assert parse_group_config(None) is None

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("2/3", id="not-an-object"),
            pytest.param({}, id="no-consent"),
            pytest.param({"consent": {"numerator": 2}}, id="no-denominator"),
            pytest.param({"consent": {"denominator": 3}}, id="no-numerator"),
            pytest.param({"consent": {"numerator": 0, "denominator": 3}}, id="zero-numerator"),
            pytest.param({"consent": {"numerator": -1, "denominator": 3}}, id="negative"),
            pytest.param({"consent": {"numerator": 4, "denominator": 3}}, id="above-one"),
            pytest.param(
                {"consent": {"numerator": 1, "denominator": MAX_DENOMINATOR + 1}},
                id="denominator-too-large",
            ),
            pytest.param({"consent": {"numerator": 2.0, "denominator": 3}}, id="float"),
            pytest.param({"consent": {"numerator": "2", "denominator": 3}}, id="string"),
            pytest.param({"consent": {"numerator": 2, "denominator": 3}, "x": 1}, id="unknown-key"),
            pytest.param(
                {"consent": {"numerator": 2, "denominator": 3, "pct": 66}},
                id="unknown-consent-key",
            ),
        ],
    )
    def test_a_malformed_fraction_is_refused(self, raw: object) -> None:
        with pytest.raises(ValueError, match="group_config"):
            validate_group_config(raw)

    def test_true_is_not_the_fraction_one_over_n(self) -> None:
        """`bool` is an `int` in Python; a consent rule must not read `true` as 1."""
        with pytest.raises(ValueError, match="numerator"):
            validate_group_config({"consent": {"numerator": True, "denominator": 3}})
