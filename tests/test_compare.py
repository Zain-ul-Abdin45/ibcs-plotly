import pandas as pd
import pytest

from ibcs_plotly import compare_periods, has_comparison, safe_delta_pct


@pytest.fixture
def df():
    return pd.DataFrame({
        "year": [2024, 2024, 2025, 2025, 2026],
        "value": [1, 2, 3, 4, 5],
    })


def test_compare_periods_defaults_previous_to_current_minus_one(df):
    ac, py, py_label = compare_periods(df, "year", 2025)
    assert list(ac["value"]) == [3, 4]
    assert list(py["value"]) == [1, 2]
    assert py_label == 2024


def test_compare_periods_explicit_previous(df):
    ac, py, py_label = compare_periods(df, "year", 2026, previous=2024)
    assert list(ac["value"]) == [5]
    assert list(py["value"]) == [1, 2]
    assert py_label == 2024


def test_compare_periods_none_current_disables_comparison(df):
    ac, py, py_label = compare_periods(df, "year", None)
    assert len(ac) == len(df)
    assert len(py) == 0
    assert py_label is None
    assert not has_comparison(py)


def test_safe_delta_pct_positive():
    assert safe_delta_pct(120, 100) == pytest.approx(20.0)


def test_safe_delta_pct_none_when_previous_is_zero():
    assert safe_delta_pct(10, 0) is None


def test_safe_delta_pct_none_when_previous_is_none():
    assert safe_delta_pct(10, None) is None
