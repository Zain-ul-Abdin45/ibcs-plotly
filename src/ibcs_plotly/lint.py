"""IBCS compliance checker for Plotly figures.

`check_figure(fig)` inspects a figure - ours or anyone else's - and returns
findings for the rules that can be verified mechanically from the figure
alone. It cannot judge titles, units or message quality.

Rules:
    no-pie        pie/donut traces: use sorted bars for composition (error)
    no-3d         3D traces distort magnitude (error)
    zero-baseline bar axes must include zero (error)
    shared-scale  comparable panels must share one value scale (warning)
    semantic-color  traces named AC/PY/Delta must use the theme colors (warning)
    too-many-categories  more categories than a bar chart can label (warning)
    color-noise   many unrelated bar colors with no semantic meaning (warning)
    legend        several traces but no legend (warning)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import plotly.graph_objects as go

from .theme import DEFAULT_THEME, IBCSTheme

MAX_CATEGORIES = 30
MAX_PLAIN_COLORS = 4
_THREE_D = {"scatter3d", "surface", "mesh3d", "cone", "streamtube", "isosurface", "volume"}


@dataclass
class Finding:
    rule: str
    severity: str  # error | warning
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _axis_key(trace, axis: str) -> str:
    ref = getattr(trace, axis, None) or axis[0]
    return "yaxis" + ref[1:] if axis == "yaxis" else "xaxis" + ref[1:]


def _color(trace):
    marker = getattr(trace, "marker", None)
    color = getattr(marker, "color", None) if marker is not None else None
    if color is not None:
        return color
    line = getattr(trace, "line", None)
    return line.color if line is not None else None


def check_figure(fig: go.Figure, theme: IBCSTheme = DEFAULT_THEME) -> list[Finding]:
    out: list[Finding] = []
    traces = list(fig.data)

    for kind, rule, msg in (
        ("pie", "no-pie", "Pie/donut chart: IBCS prefers sorted bars so magnitude and rank "
                          "can be compared."),
    ):
        if any(t.type == kind for t in traces):
            out.append(Finding(rule, "error", msg))
    if any(t.type in _THREE_D for t in traces):
        out.append(Finding("no-3d", "error", "3D chart: depth distorts magnitude."))

    # zero baseline: bars only
    for t in traces:
        if t.type != "bar":
            continue
        axis = "xaxis" if t.orientation == "h" else "yaxis"
        layout_axis = fig.layout[_axis_key(t, axis)]
        rng = layout_axis.range
        if rng is not None and rng[0] is not None and rng[0] > 0:
            out.append(Finding("zero-baseline", "error",
                               f"Bar axis starts at {rng[0]}: bars must start at zero."))
            break

    # shared scale across comparable panels
    ranges = {}
    for t in traces:
        if t.type not in ("scatter", "bar"):
            continue
        ax = fig.layout[_axis_key(t, "yaxis")]
        if ax.ticksuffix == "%":
            continue  # variance pin has its own unit
        ranges[_axis_key(t, "yaxis")] = (
            tuple(ax.range) if ax.range is not None else None, ax.matches)
    if len(ranges) > 1:
        explicit = {r for r, _ in ranges.values() if r is not None}
        unlinked = [k for k, (r, m) in ranges.items() if r is None and m is None]
        if len(explicit) > 1 or (explicit and unlinked) or (not explicit and len(unlinked) > 1):
            out.append(Finding("shared-scale", "warning",
                               "Panels do not share one value scale; similar shapes may hide "
                               "different magnitudes."))

    # semantic colors
    expected = {"ac": theme.ac, "py": theme.py}
    for t in traces:
        want = expected.get((t.name or "").strip().lower())
        got = _color(t)
        if want and isinstance(got, str) and got.lower() != want.lower():
            out.append(Finding("semantic-color", "warning",
                               f"Trace '{t.name}' uses {got}; AC/PY must use {want}."))
        if (t.name or "").lower().startswith("delta") and isinstance(_color(t), (list, tuple)):
            bad = {c for c in _color(t) if c not in (theme.var_pos, theme.var_neg)}
            if bad:
                out.append(Finding("semantic-color", "warning",
                                   "Delta bars should only use the positive/negative variance "
                                   "colors."))

    # category count
    for t in traces:
        if t.type == "bar":
            cats = t.y if t.orientation == "h" else t.x
            if cats is not None and len(cats) > MAX_CATEGORIES:
                out.append(Finding("too-many-categories", "warning",
                                   f"{len(cats)} bars: show the top {MAX_CATEGORIES} and group "
                                   "the rest as 'Other'."))
                break

    # colour noise
    plain = {c for t in traces if t.type == "bar"
             for c in [_color(t)] if isinstance(c, str)
             and c.lower() not in {x.lower() for x in (theme.ac, theme.py, theme.var_pos,
                                                       theme.var_neg)}}
    if len(plain) > MAX_PLAIN_COLORS:
        out.append(Finding("color-noise", "warning",
                           f"{len(plain)} different bar colors: color should carry meaning "
                           "(AC/PY/variance), not decoration."))

    if len(traces) > 1 and fig.layout.showlegend is False:
        out.append(Finding("legend", "warning", "Several series but the legend is hidden."))
    return out


def is_compliant(fig: go.Figure, theme: IBCSTheme = DEFAULT_THEME) -> bool:
    return not any(f.severity == "error" for f in check_figure(fig, theme))
