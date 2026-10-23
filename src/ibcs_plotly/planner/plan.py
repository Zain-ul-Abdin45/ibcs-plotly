"""Turn a dataset profile into a development plan: what is calculable, what to chart.

The plan is plain JSON-serialisable data. It is meant to be handed to a
developer (or an AI agent) who then builds the chart; `render.render_spec`
can also execute any `ready` chart spec directly.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from .exposure import Tier
from .profile import (
    ColumnProfile,
    DatasetProfile,
    aggregate_labels,
    coerce_types,
    core_date_bounds,
    profile_dataframe,
)

MAX_BAR_CATEGORIES = 30
MAX_PANELS = 12
DAILY_MAX_DAYS = 120  # shorter spans are charted per day; a monthly line would be 1-4 points
DAILY_ROLLING_WINDOW = 7
ROLLING_WINDOW = 3


@dataclass
class Calculation:
    id: str
    name: str
    formula: str
    inputs: list[str]
    feasible: bool
    note: str = ""


CHART_KINDS = ("grouped_column", "horizontal_bar", "variance_waterfall", "small_multiples",
               "time_series")


@dataclass
class ChartSpec:
    kind: str  # one of CHART_KINDS
    title: str = ""
    params: dict = field(default_factory=dict)
    rationale: str = ""
    status: str = "ready"  # ready | needs_work
    issues: list[str] = field(default_factory=list)


@dataclass
class Plan:
    tier: str
    summary: dict
    calculations: list[Calculation]
    charts: list[ChartSpec]
    warnings: list[str]
    next_steps: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = ["# Visualization plan", ""]
        s = self.summary
        lines += [f"{s['n_rows']} rows, exposure tier **{self.tier}**.", ""]
        lines += ["## Columns", "", "| column | role | dtype |", "|---|---|---|"]
        lines += [f"| {c['name']} | {c['role']} | {c['dtype']} |" for c in s["columns"]]
        lines += ["", "## Calculations", ""]
        for c in self.calculations:
            mark = "yes" if c.feasible else "no"
            note = f" - {c.note}" if c.note else ""
            lines.append(f"- **{c.name}** ({mark}): `{c.formula}`{note}")
        lines += ["", "## Charts", ""]
        for ch in self.charts:
            lines.append(f"- **{ch.title}** [{ch.kind}, {ch.status}] - {ch.rationale}")
            for i in ch.issues:
                lines.append(f"  - issue: {i}")
        if self.warnings:
            lines += ["", "## Warnings", ""] + [f"- {w}" for w in self.warnings]
        lines += ["", "## Next steps", ""] + [f"{i}. {s}" for i, s in enumerate(self.next_steps, 1)]
        return "\n".join(lines)


def _pick_measure(profile: DatasetProfile, goal: str | None):
    measures = profile.by_role("measure")
    if goal:
        for m in measures:
            if m.name.lower() in goal.lower():
                return m
    return measures[0] if measures else None


def _dim_rank(d, n_rows: int) -> tuple:
    """Best chart dimension first: ~2-30 readable categories, ID-like text last."""
    n = d.n_unique
    if n > 0.3 * max(n_rows, 1):
        return (3, n)  # looks like a key (invoice, case number)
    if _CODE_LABEL_NAME.search(d.name):
        return (2.5, n)  # iso_code, symbol_code: readable names beat codes
    if n < 2:
        return (2, n)
    if n <= MAX_BAR_CATEGORIES:
        return (0, abs(n - 8))  # closest to a comfortable 8 bars
    return (1, n)


def _time_grain(col: pd.Series) -> tuple[str, int, int]:
    """(grain, span_days, n_months) from the central 98% of dates, so stray dates don't count."""
    lo, hi = core_date_bounds(col)
    core = col[(col >= lo) & (col <= hi)]
    span = max(int((core.max() - core.min()).days), 0)
    grain = "day" if span <= DAILY_MAX_DAYS else "month"
    return grain, span, int(core.dt.to_period("M").nunique())


_CODE_LABEL_NAME = re.compile(r"(iso|code|abbr|abbrev|short_?name)", re.I)


def _note_key(note: str) -> str:
    return re.sub(r"[\d.]+% missing", "N% missing", note)


_SECONDARY_TIME = re.compile(
    r"(updated|modified|closed|resolved|due|dropoff|drop_off|end|expire|deleted|processed)", re.I)


def _time_cols(profile: DatasetProfile) -> list:
    """Event-date columns first; 'updated/closed/due' style timestamps last (and never used)."""
    cols = profile.by_role("time")
    primary = [c for c in cols if not _SECONDARY_TIME.search(c.name)]
    return primary or cols


MIN_PERIOD_SHARE = 0.10  # a period holding <10% of the biggest period's rows is stray data
MIN_LIKE_FOR_LIKE = 0.05  # PY rows inside AC's date window, relative to AC rows


def _eligible(counts: pd.Series, notes: list[str], col: str) -> list:
    """Periods with enough rows to compare; drop stray dates (e.g. 3 rows from 2002)."""
    keep = sorted(counts[counts >= MIN_PERIOD_SHARE * counts.max()].index)
    dropped = sorted(set(counts.index) - set(keep))
    if dropped:
        notes.append(f"'{col}': periods {[_py(v) for v in dropped][:5]} hold under "
                     f"{MIN_PERIOD_SHARE:.0%} of the rows of the largest period and were ignored.")
    return keep


def _pick_period(profile: DatasetProfile, df: pd.DataFrame, notes: list[str]):
    """Return (period_col, category_col, n_periods, latest, previous) or None."""
    for p in profile.by_role("period"):
        years = _eligible(df[p.name].dropna().value_counts(), notes, p.name)
        if len(years) >= 2:
            return p.name, None, len(years), years[-1], years[-2]
    for t in _time_cols(profile)[:1]:  # only the primary event date defines periods
        col = df[t.name].dropna()
        if col.empty:
            continue
        years = _eligible(col.dt.year.value_counts(), notes, t.name)
        if len(years) < 2:
            continue
        cur = years[-1]
        last = col[col.dt.year == cur].max()
        n_cur = int((col.dt.year == cur).sum())
        md = col.dt.month * 100 + col.dt.day
        for prev in reversed(years[:-1]):
            n_prev = int(((col.dt.year == prev) & (md <= last.month * 100 + last.day)).sum())
            if n_prev >= MIN_LIKE_FOR_LIKE * n_cur:
                return (f"{t.name}__ibcs_year", f"{t.name}__ibcs_month", len(years),
                        _py(cur), _py(prev))
        notes.append(f"'{t.name}': no earlier year has data for the same dates as {_py(cur)}; "
                     "comparison charts skipped.")
    return None


def build_plan(
    df: pd.DataFrame,
    tier: Tier | str = Tier.SCHEMA,
    goal: str | None = None,
    *,
    profile: DatasetProfile | None = None,
    pii_columns: tuple[str, ...] = (),
) -> Plan:
    tier = Tier.parse(tier)
    df = coerce_types(df)
    profile = profile or profile_dataframe(df, pii_columns)
    measure = _pick_measure(profile, goal)
    dims = [d for d in profile.by_role("dimension") if not d.pii]
    warnings: list[str] = []
    calcs: list[Calculation] = []
    charts: list[ChartSpec] = []

    grouped_notes: dict[str, list] = {}
    reveal = tier >= Tier.STATS  # value-derived detail (counts, ranges, %) needs STATS
    for c in profile.columns:
        if c.pii:
            warnings.append(
                f"'{c.name}' looks like PII: masked in SAMPLE/FULL, excluded from charts.")
        if reveal:
            for note in c.notes:
                grouped_notes.setdefault(_note_key(note), []).append(c)
    for note, cols in grouped_notes.items():
        if len(cols) <= 3:
            warnings += [f"'{c.name}': {n}" for c in cols for n in c.notes
                         if _note_key(n) == note]
        elif note.startswith("N% missing"):
            worst = sorted(cols, key=lambda c: -c.null_pct)[:5]
            warnings.append(f"{len(cols)} columns are over 20% missing, e.g. "
                            + ", ".join(f"'{c.name}' ({c.null_pct:g}%)" for c in worst) + ".")
        else:
            warnings.append(f"{len(cols)} columns: {note} (e.g. "
                            + ", ".join(f"'{c.name}'" for c in cols[:4]) + ").")
    agg_dims: dict[str, list[str]] = {}
    if reveal:
        warnings += profile.notes
        for d in dims:
            labels = aggregate_labels(df[d.name])
            if labels:
                agg_dims[d.name] = labels
                warnings.append(
                    f"'{d.name}' mixes totals/regions ({labels[:3]}) with detail rows: charts "
                    "exclude them so totals are not double counted.")
        for d in dims:
            raw = df[d.name].dropna().astype(str)
            if 1 < d.n_unique <= 500:
                norm = raw.str.strip().str.casefold().nunique()
                if norm < raw.nunique():
                    warnings.append(
                        f"'{d.name}': {raw.nunique()} labels collapse to {norm} after trimming "
                        "and ignoring case; clean them before charting.")

    dims = sorted(dims, key=lambda d: _dim_rank(d, profile.n_rows))
    if measure is None and (dims or _time_cols(profile)):
        warnings.append(
            "No numeric measure found: charting row counts (events per category/period).")
        measure = ColumnProfile(name="*", dtype="rows", role="measure", n_unique=0, null_pct=0.0)
    if measure is None:
        warnings.append("No numeric measure found: nothing to chart as a business metric.")
        return _finish(profile, tier, calcs, charts, warnings)

    m = measure.name
    ml = "rows" if m == "*" else m
    # Ratios/rates must be averaged, never summed. The name is public; a value-based
    # guess (0..1 range, "%" text) counts as data and is only applied from STATS up.
    is_ratio = measure.ratio == "name" or (measure.ratio is not None and reveal)
    agg = "count" if m == "*" else ("mean" if is_ratio else "sum")
    if is_ratio and reveal:
        warnings.append(
            f"'{ml}' looks like a ratio/rate: charts average it. For a weighted rate, "
            "compute numerator / denominator per group instead.")
    period_notes: list[str] = []
    period = _pick_period(profile, df, period_notes)
    if reveal:
        warnings += period_notes
    cur, prev = ("latest", "previous") if not reveal or period is None else (
        _py(period[3]), _py(period[4]))

    calcs.append(Calculation("total", f"{agg.title()} {ml}", f"{agg}({ml})", [m], True))
    has_neg = measure.has_negatives and reveal  # sign is a value: only report from STATS up
    if is_ratio:
        calcs.append(Calculation("share", f"Share of total {ml}", "n/a", [m], False,
                                 "shares of a ratio are meaningless; compare averages instead"))
    elif has_neg:
        calcs.append(Calculation("share", f"Share of total {ml}", f"{ml} / sum({ml})", [m], False,
                                 "negatives make shares misleading; use ranking instead"))
    elif dims:
        calcs.append(Calculation("share", f"Share of total {ml}", f"sum({ml}) by dim / sum({ml})",
                                 [m, dims[0].name], True,
                                 "" if reveal else "verify values are non-negative"))
        calcs.append(Calculation("rank", f"Rank by {ml}", f"sort({agg}({ml}) by dim, desc)",
                                 [m, dims[0].name], True))

    if period:
        pcol = period[0]
        calcs += [
            Calculation("delta", f"Variance {ml} AC-PY", f"{agg}({ml})[AC] - {agg}({ml})[PY]",
                        [m, pcol], True),
            Calculation("delta_pct", f"Variance % {ml}", "(AC - PY) / PY * 100", [m, pcol], True,
                        "undefined where PY is 0; the library returns None/0 there"),
        ]
    else:
        calcs.append(Calculation("delta", f"Variance {ml} AC-PY", "needs two periods", [m], False,
                                 "no year/date column with at least 2 distinct years"))

    time_cols = _time_cols(profile)
    if time_cols:
        t = time_cols[0].name
        grain, span, n_months = _time_grain(df[t].dropna())
        grain = grain if reveal else "month"  # span is a value: SCHEMA plans stay generic
        unit, win = ("day", DAILY_ROLLING_WINDOW) if grain == "day" else ("month", ROLLING_WINDOW)
        enough = (span >= 2 * win) if grain == "day" else n_months >= win
        enough = enough or not reveal
        calcs += [
            Calculation("trend", f"{ml} by {unit}", f"{agg}({ml}) grouped by {unit}({t})",
                        [m, t], True),
            Calculation("rolling", f"{win}-{unit} rolling {ml}",
                        f"rolling_mean({unit}ly {agg}({ml}), window={win})", [m, t],
                        enough,
                        "" if enough else f"only {n_months} months / {span} days of data"
                        if reveal else "needs enough history; verify at STATS"),
            Calculation("ytd", f"{ml} year-to-date", "cumsum within year", [m, t], not is_ratio,
                        "cumulating a ratio is meaningless" if is_ratio else ""),
        ]

    # 1. AC vs PY columns over time / category
    if period:
        pcol, ccol, _, _, _ = period
        cat = ccol or (dims[0].name if dims else None)
        if cat:
            cat_label = cat.split("__ibcs_")[-1]
            charts.append(ChartSpec(
                "grouped_column", f"{ml}: AC vs PY by {cat_label}",
                dict(measure=m, period_col=pcol, category=cat, current=cur, previous=prev,
                     agg=agg, **({"exclude": agg_dims[cat]} if cat in agg_dims else {})),
                "Two periods are present, so IBCS AC/PY columns with a variance pin apply. "
                "If AC is a part-period, PY is cut to the same dates (like-for-like).",
            ))
    # 2. ranked composition
    if dims:
        d = dims[0]
        issues = []
        if d.n_unique > MAX_BAR_CATEGORIES:
            issues.append(
                f"{d.n_unique} categories: cap at top {MAX_BAR_CATEGORIES} + 'Other'"
                if reveal else f"many categories: cap at top {MAX_BAR_CATEGORIES} + 'Other'")
        params = dict(measure=m, category=d.name, agg=agg, top_n=MAX_BAR_CATEGORIES)
        if d.name in agg_dims:
            params["exclude"] = agg_dims[d.name]
        if period:
            params.update(period_col=period[0], current=cur, previous=prev)
        charts.append(ChartSpec(
            "horizontal_bar", f"{ml} by {d.name}", params,
            "Composition as sorted bars (IBCS discourages pies).",
            "needs_work" if issues else "ready", issues,
        ))
        # 3. variance waterfall
        if period:
            wf_issues = (["negative values: check sign convention"] if has_neg else [])
            charts.append(ChartSpec(
                "variance_waterfall", f"{ml} variance AC-PY by {d.name}",
                dict(measure=m, category=d.name, period_col=period[0], current=cur,
                     previous=prev, agg=agg, top_n=MAX_BAR_CATEGORIES,
                     **({"exclude": agg_dims[d.name]} if d.name in agg_dims else {})),
                "Shows which categories drive the net change.",
                "needs_work" if wf_issues else "ready", wf_issues,
            ))
    # 4. small multiples
    if time_cols and dims and 2 <= dims[0].n_unique <= MAX_PANELS:
        charts.append(ChartSpec(
            "small_multiples", f"{ml} trend per {dims[0].name}",
            dict(measure=m, time_col=time_cols[0].name, panel=dims[0].name, agg=agg,
                 **({"grain": grain} if reveal else {}),
                 **({"exclude": agg_dims[dims[0].name]} if dims[0].name in agg_dims else {})),
            "Comparable trends on one shared scale (IBCS CHECK rule).",
        ))

    # 5. time-series calculations
    if time_cols:
        t = time_cols[0].name
        base = dict(measure=m, time_col=t, agg=agg)
        if period:
            base.update(period_col=period[0], current=cur, previous=prev)
        if reveal:
            base["grain"] = grain
        label = "over time" if not reveal else f"by {unit}"
        charts.append(ChartSpec(
            "time_series", f"{ml} {label}", dict(base, calc="trend"),
            f"{unit.title()}ly trend; AC against PY when two years exist."))
        if not is_ratio:
            charts.append(ChartSpec(
                "time_series", f"{ml} year-to-date", dict(base, calc="ytd"),
                "Cumulative total within each year, AC against PY."))
        if enough:
            charts.append(ChartSpec(
                "time_series", f"{ml} {win}-{unit if reveal else 'period'} rolling",
                dict(base, calc="rolling", window=win),
                f"Smooths noise; the raw {unit}ly series is shown behind it."))

    if not charts:
        warnings.append("No IBCS chart fits: need a dimension or two comparable periods.")
    return _finish(profile, tier, calcs, charts, warnings)


def _py(v):
    return v.item() if hasattr(v, "item") else v


def _finish(profile, tier, calcs, charts, warnings) -> Plan:
    steps = [
        "Confirm the chosen measure and aggregation (sum/mean) with the business owner.",
        "Resolve every chart with status 'needs_work' (see issues).",
        "Render ready specs with ibcs_plotly.planner.render_spec(df, spec) and review.",
        "Wire the figures into the host app (Streamlit, Dash, Jupyter, or a web API).",
    ]
    return Plan(
        tier=tier.name,
        summary={"n_rows": profile.n_rows, "columns": [
            {"name": c.name, "role": c.role, "dtype": c.dtype}
            for c in profile.columns if not c.pii
        ]},
        calculations=calcs, charts=charts, warnings=sorted(set(warnings)), next_steps=steps,
    )
