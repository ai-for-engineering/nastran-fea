"""Tests for scripts/compare_rebuilt_vs_original.py's pure comparison
math (percent_difference, check_reaction_balance). summarize_rebuilt_model
itself needs real solved BDF/OP2 files (the actual rebuilt NASA CRM
wingbox, gitignored, ~110MB+ of derived output) -- not exercised here,
verified manually against the real files instead (see PR description)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_rebuilt_vs_original import (  # noqa: E402
    check_reaction_balance,
    percent_difference,
)


def test_percent_difference_positive_when_rebuilt_higher():
    assert percent_difference(120.0, 100.0) == pytest.approx(20.0)


def test_percent_difference_negative_when_rebuilt_lower():
    assert percent_difference(80.0, 100.0) == pytest.approx(-20.0)


def test_percent_difference_zero_when_equal():
    assert percent_difference(50.0, 50.0) == pytest.approx(0.0)


def test_percent_difference_raises_on_zero_original():
    with pytest.raises(ValueError, match="non-zero"):
        percent_difference(10.0, 0.0)


def test_check_reaction_balance_within_tolerance():
    assert check_reaction_balance(249777.6, 249777.6 * 1.005, tolerance_fraction=0.01)


def test_check_reaction_balance_outside_tolerance():
    assert not check_reaction_balance(249777.6, 249777.6 * 1.5, tolerance_fraction=0.01)


def test_check_reaction_balance_exact_match():
    assert check_reaction_balance(1000.0, 1000.0, tolerance_fraction=0.0)


def test_check_reaction_balance_raises_on_zero_applied():
    with pytest.raises(ValueError, match="non-zero"):
        check_reaction_balance(0.0, 100.0)
