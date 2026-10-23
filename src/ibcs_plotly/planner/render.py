"""Execute a ChartSpec from the planner against a DataFrame."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from ..charts import (
    grouped_column,
    horizontal_bar,
    line_comparison,
    small_multiples,
    variance_waterfall,
)
from ..compare import compare_periods
from ..theme import DEFAULT_THEME, IBCSTheme
from .plan import ChartSpec
from .profile import coerce_types, core_date_bounds, with_time_parts

ALLOWED_AGGS = ("sum", "mean", "median", "min", "max", "count", "nunique")
ALLOWED_CALCS = ("trend", "ytd", "rolling")
MAX_TOP_N = 200
MAX_WINDOW = 36
MAX_EXCLUDE = 500


def _validate(p: dict) -> None:
    """Specs may come from an AI: reject anything outside the supported vocabulary."""
    if p.get("agg", "sum") not in ALLOWED_AGGS:
        raise ValueError(f"agg must be one of {ALLOWED_AGGS}")
    if "calc" in p and p["calc"] not in ALLOWED_CALCS:
        raise ValueError(f"calc must be one of {ALLOWED_CALCS}")
    if not 1 <= int(p.get("top_n", 30)) <= MAX_TOP_N:
        raise ValueError(f"top_n must be between 1 and {MAX_TOP_N}")
    if not 1 <= int(p.get("window", 3)) <= MAX_WINDOW:
        raise ValueError(f"window must be between 1 and {MAX_WINDOW}")
    if p.get("grain", "month") not in ("day", "month"):
        raise ValueError("grain must be 'day' or 'month'")
    excl = p.get("exclude", [])
    if not isinstance(excl, list) or len(excl) > MAX_EXCLUDE or \
            not all(isinstance(v, str) for v in excl):
        raise ValueError(f"exclude must be a list of at most {MAX_EXCLUDE} strings")


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = coerce_types(df)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df = with_time_parts(df, c)
    return df


def _resolve(df: pd.DataFrame, col: str, current, previous):
    vals = sorted(df[col].dropna().unique())
    if current in (None, "latest"):
        current = vals[-1]
    if previous in (None, "previous"):
        previous = vals[vals.index(current) - 1] if vals.index(current) > 0 else None
    return current, previous


def _like_for_like(df: pd.DataFrame, period_col: str, cur, df_py: pd.DataFrame) -> pd.DataFrame:
    """Cut PY to the dates AC covers, so a part-year AC is not compared with a full-year PY."""
    if not period_col.endswith("__ibcs_year") or df_py.empty:
        return df_py
    tcol = period_col[: -len("__ibcs_year")]
    last = df.loc[df[period_col] == cur, tcol].max()
    if pd.isna(last):
        return df_py
    md = df_py[tcol].dt.month * 100 + df_py[tcol].dt.day
    return df_py[md <= last.month * 100 + last.day]


_MONTH_ORDER = {m.lower(): i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def _chronological(index: pd.Index) -> pd.Index:
    """Month-name categories (Jan, February...) sort by calendar, not alphabet."""
    keys = [_MONTH_ORDER.get(str(v).strip().lower()[:3]) for v in index]
    if index.size and all(k is not None for k in keys):
        return pd.Index([v for _, v in sorted(zip(keys, index))])
    return index


def _grouped(df, by, measure: str, how: str) -> pd.Series:
    """Aggregate `measure` by `by`; measure "*" counts rows."""
    g = df.groupby(by)
    return g.size() if measure == "*" else g[measure].agg(how)


def _agg(df: pd.DataFrame, cat: str, measure: str, how: str) -> pd.Series:
    return _grouped(df, cat, measure, how)


def _bucketed(df: pd.DataFrame, tcol: str, grain: str | None, measure: str, how: str):
    """Rows within the bulk of the dates, with a `_t` bucket per day or month.

    The grain defaults to day for spans up to 120 days. Stray dates far from the bulk and
    incomplete edge days are dropped, so one panel's axis is not stretched by a few rows.
    """
    lo, hi = core_date_bounds(df[tcol].dropna())
    core = df[(df[tcol] >= lo) & (df[tcol] <= hi)]
    grain = grain or ("day" if (core[tcol].max() - core[tcol].min()).days <= 120 else "month")
    if grain == "month":
        return core.assign(_t=core[tcol].dt.to_period("M").astype(str))
    core = core.assign(_t=core[tcol].dt.normalize())
    rows = core.groupby("_t").size()
    full = (rows >= 0.1 * rows.median()).to_numpy().nonzero()[0]
    if len(full):
        core = core[core["_t"].between(rows.index[full[0]], rows.index[full[-1]])]
    return core.assign(_t=core["_t"].dt.strftime("%Y-%m-%d"))


def _time_series(df: pd.DataFrame, p: dict, theme: IBCSTheme) -> go.Figure:
    m, how, tcol, calc = p["measure"], p.get("agg", "sum"), p["time_col"], p["calc"]
    lo, hi = core_date_bounds(df[tcol].dropna())
    core = df[(df[tcol] >= lo) & (df[tcol] <= hi)]
    grain = p.get("grain") or (
        "day" if (core[tcol].max() - core[tcol].min()).days <= 120 else "month")
    if grain == "day":
        # short span: one point per day, ignoring stray dates far from the bulk
        core = core.assign(_d=core[tcol].dt.normalize())
        daily = _grouped(core, "_d", m, how)
        # drop incomplete edge days (a handful of rows spilling over midnight)
        rows = core.groupby("_d").size().reindex(daily.index)
        full = (rows >= 0.1 * rows.median()).to_numpy().nonzero()[0]
        if len(full):
            daily = daily.iloc[full[0]: full[-1] + 1]
        labels = [str(i.date()) for i in daily.index]
        if calc == "rolling":
            window = p.get("window", 7)
            return line_comparison(labels, daily.rolling(window).mean().values, daily.values,
                                   ac_name=f"{window}-day rolling", py_name="Daily", theme=theme)
        series = daily.groupby(daily.index.year).cumsum() if calc == "ytd" else daily
        return line_comparison(labels, series.values, ac_name="AC", theme=theme)

    d = df.assign(_ym=df[tcol].dt.to_period("M"), _y=df[tcol].dt.year, _m=df[tcol].dt.month)
    monthly = _grouped(d, "_ym", m, how)
    if calc == "rolling":
        window = p.get("window", 3)
        return line_comparison(
            monthly.index.astype(str), monthly.rolling(window).mean().values, monthly.values,
            ac_name=f"{window}-month rolling", py_name="Monthly", theme=theme)

    by = _grouped(d, ["_y", "_m"], m, how).unstack(0).reindex(range(1, 13))
    by = by.dropna(how="all")
    if calc == "ytd":
        by = by.cumsum()
    years = list(by.columns)
    cur, prev = (years[-1], years[-2]) if len(years) > 1 else (years[-1], None)
    if p.get("period_col", "").endswith("__ibcs_year") or p.get("current") not in (None, "latest"):
        c = p.get("current")
        if c in years:
            cur = c
            prev = p.get("previous") if p.get("previous") in years else None
    labels = [f"{i:02d}" for i in by.index]
    return line_comparison(
        labels, by[cur].values, by[prev].values if prev is not None else None,
        ac_name=str(cur), py_name=str(prev), theme=theme)


def render_spec(
    df: pd.DataFrame, spec: ChartSpec | dict, theme: IBCSTheme = DEFAULT_THEME
) -> go.Figure:
    if isinstance(spec, dict):
        spec = ChartSpec(**spec)
    p = spec.params
    _validate(p)
    df = _prepare(df)
    measure, how = p.get("measure"), p.get("agg", "sum")
    drop = set(p.get("exclude", []))
    for col in (p.get("category"), p.get("panel")):
        if drop and col in df.columns:
            df = df[~df[col].astype(str).isin(drop)]

    if spec.kind == "small_multiples":
        d = _bucketed(df, p["time_col"], p.get("grain"), measure, how)
        panels = {
            str(k): (list(_grouped(g, "_t", measure, how).index),
                     list(_grouped(g, "_t", measure, how).values))
            for k, g in d.groupby(p["panel"])
        }
        return small_multiples(panels, theme=theme)

    if spec.kind == "time_series":
        return _time_series(df, p, theme)

    cat = p["category"]
    cur = prev = None
    df_ac, df_py = df, df.iloc[0:0]
    if p.get("period_col"):
        cur, prev = _resolve(df, p["period_col"], p.get("current"), p.get("previous"))
        df_ac, df_py, prev = compare_periods(df, p["period_col"], cur, prev)
        if p.get("like_for_like", True):
            df_py = _like_for_like(df, p["period_col"], cur, df_py)

    ac = _agg(df_ac, cat, measure, how)
    py = _agg(df_py, cat, measure, how) if len(df_py) else pd.Series(dtype=float)
    idx = _chronological(ac.index.union(py.index) if len(py) else ac.index)
    ac, py = ac.reindex(idx).fillna(0), py.reindex(idx).fillna(0)
    has_py = len(df_py) > 0

    if spec.kind == "grouped_column":
        return grouped_column(ac.values, py.values, [str(i) for i in idx],
                              show_py=has_py, theme=theme)

    order = ac.sort_values(ascending=False).index
    if spec.kind == "variance_waterfall":
        if not has_py:
            raise ValueError("variance_waterfall needs a comparison period with data")
        order = (ac - py).abs().sort_values(ascending=False).index
    order = order[: p.get("top_n", 30)]
    ac, py = ac.reindex(order), py.reindex(order)

    if spec.kind == "horizontal_bar":
        return horizontal_bar([str(i) for i in order], list(ac.values),
                              list(py.values) if has_py else None, show_py=has_py, theme=theme)
    if spec.kind == "variance_waterfall":
        return variance_waterfall([str(i) for i in order], ac.values, py.values, theme=theme)
    raise ValueError(f"unknown chart kind {spec.kind!r}")
