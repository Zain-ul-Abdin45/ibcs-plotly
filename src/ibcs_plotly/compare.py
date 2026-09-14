"""Period-over-period comparison helpers.

IBCS reports almost always show a current period (AC) against a comparison
period (PY) rather than a value in isolation. These helpers do the split and
the variance math once, so chart and KPI code never touches "current - 1"
arithmetic directly.
"""

from __future__ import annotations

import pandas as pd


def compare_periods(
    df: pd.DataFrame,
    period_col: str,
    current,
    previous=None,
) -> tuple[pd.DataFrame, pd.DataFrame, object | None]:
    """Split df into (current_df, previous_df, previous_label).

    current: the period value to treat as AC (e.g. 2026, "2026-Q3"). Pass
        None to mean "no period filter" - previous-period comparison is
        then disabled and previous_df is empty.
    previous: the period value to treat as PY. If omitted and `current` is
        an int, defaults to current - 1.

    Returns (df_ac, df_py, previous_label). previous_label is None when
    there is no valid comparison period, in which case df_py is empty and
    callers should treat variance as not applicable.
    """
    if current is None:
        empty = df.iloc[0:0]
        return df, empty, None

    if previous is None:
        if isinstance(current, int):
            previous = current - 1
        else:
            empty = df.iloc[0:0]
            return df[df[period_col] == current].copy(), empty, None

    df_ac = df[df[period_col] == current].copy()
    df_py = df[df[period_col] == previous].copy()
    return df_ac, df_py, previous


def has_comparison(df_py: pd.DataFrame) -> bool:
    return len(df_py) > 0


def safe_delta_pct(current_val: float, previous_val: float | None) -> float | None:
    """Percent variance of current vs previous, or None when not computable."""
    if previous_val in (None, 0):
        return None
    return (current_val - previous_val) / previous_val * 100
