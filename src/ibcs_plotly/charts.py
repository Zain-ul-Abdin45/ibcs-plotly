"""IBCS-notation chart primitives built on Plotly.

Every function returns a plain plotly.graph_objects.Figure - no Streamlit
dependency here, so these work in Jupyter, Dash, or a plain script too.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .theme import DEFAULT_THEME, IBCSTheme


def grouped_column(
    ac,
    py,
    labels,
    *,
    show_py: bool = True,
    theme: IBCSTheme = DEFAULT_THEME,
    height: int = 320,
) -> go.Figure:
    """AC/PY grouped columns with a variance-% pin chart underneath.

    ac, py: sequences of values, same length as labels.
    show_py: when False, only AC is drawn and the variance pin is omitted -
        use this when there is no valid comparison period.
    """
    ac = np.asarray(ac, dtype=float)
    py = np.asarray(py, dtype=float)

    var_pct = (
        np.where(py > 0, (ac - py) / py * 100, 0) if show_py else np.zeros(len(ac))
    )

    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.72, 0.28],
        shared_xaxes=True, vertical_spacing=0.04,
    )
    if show_py:
        fig.add_trace(go.Bar(
            x=list(labels), y=py, name="PY", marker_color=theme.py,
            width=0.7, offsetgroup=0,
        ), row=1, col=1)

    fig.add_trace(go.Bar(
        x=list(labels), y=ac, name="AC", marker_color=theme.ac,
        width=0.42 if show_py else 0.6, offsetgroup=0,
    ), row=1, col=1)

    if show_py:
        colors = [theme.var_pos if v >= 0 else theme.var_neg for v in var_pct]
        fig.add_trace(go.Bar(
            x=list(labels), y=var_pct, name="Delta vs PY %",
            marker_color=colors, width=0.12,
        ), row=2, col=1)
        fig.add_hline(y=0, line_width=0.5, line_color=theme.border, row=2, col=1)

    fig.update_layout(
        **theme.plotly_layout(), height=height, barmode="overlay",
        yaxis=dict(gridcolor=theme.border, zeroline=True,
                   zerolinecolor=theme.border, zerolinewidth=0.5),
        yaxis2=dict(gridcolor=theme.border, showgrid=False, zeroline=True,
                    zerolinecolor=theme.border, zerolinewidth=0.5,
                    tickformat="+.0f", ticksuffix="%"),
    )
    return fig


def horizontal_bar(
    labels,
    ac,
    py=None,
    *,
    show_py: bool = True,
    theme: IBCSTheme = DEFAULT_THEME,
    height: int | None = None,
) -> go.Figure:
    """AC overlapping PY horizontal bars, sorted by AC descending.

    IBCS discourages pie charts for composition - a sorted bar reads
    magnitude and rank in one shot, which a pie cannot do reliably.
    """
    labels = list(labels)
    ac = list(ac)
    py = list(py) if py is not None else [0] * len(labels)

    idx = np.argsort(ac)[::-1]
    labels = [labels[i] for i in idx]
    ac = [ac[i] for i in idx]
    py = [py[i] for i in idx]
    h = height or max(280, len(labels) * 38)

    fig = go.Figure()
    if show_py:
        fig.add_trace(go.Bar(
            y=labels, x=py, name="PY", marker_color=theme.py,
            orientation="h", width=0.62,
        ))
    fig.add_trace(go.Bar(
        y=labels, x=ac, name="AC", marker_color=theme.ac,
        orientation="h", width=0.38 if show_py else 0.6,
    ))
    fig.update_layout(
        **theme.plotly_layout(), height=h, barmode="overlay",
        xaxis=dict(gridcolor=theme.border),
        yaxis=dict(autorange="reversed"),
    )
    return fig


def variance_waterfall(
    labels,
    ac,
    py,
    *,
    theme: IBCSTheme = DEFAULT_THEME,
    height: int = 300,
    total_label: str = "Net Delta",
) -> go.Figure:
    """Waterfall of AC-PY variance per category, with a total bar."""
    variances = [int(a) - int(p) for a, p in zip(ac, py)]
    x_labels = list(labels) + [total_label]
    y_vals = variances + [sum(variances)]

    fig = go.Figure(go.Waterfall(
        x=x_labels, y=y_vals,
        measure=["relative"] * len(variances) + ["total"],
        connector=dict(line=dict(color=theme.border, width=0.5, dash="dot")),
        increasing=dict(marker_color=theme.var_pos),
        decreasing=dict(marker_color=theme.var_neg),
        totals=dict(marker_color=theme.ac),
        text=[f"{v:+d}" for v in variances] + [f"{sum(variances):+d}"],
        textposition="outside", textfont=dict(size=11),
    ))
    layout = theme.plotly_layout()
    layout.update(
        height=height, showlegend=False,
        yaxis=dict(gridcolor=theme.border, zeroline=True, zerolinecolor=theme.border),
    )
    fig.update_layout(**layout)
    return fig


def small_multiples(
    series: dict[str, tuple[list, list]],
    *,
    theme: IBCSTheme = DEFAULT_THEME,
    cols: int = 2,
    height: int = 310,
) -> go.Figure:
    """Trend small multiples sharing one y-scale.

    series: {panel_title: (x_labels, y_values)}. IBCS's CHECK rule requires
    comparable small multiples to share a scale - otherwise similar-looking
    lines can hide very different magnitudes. This enforces that.
    """
    titles = list(series.keys())
    rows = -(-len(titles) // cols)  # ceil division
    padded_titles = titles + [""] * (rows * cols - len(titles))
    fig = make_subplots(
        rows=rows, cols=cols, shared_yaxes=True,
        subplot_titles=padded_titles,
        vertical_spacing=0.18, horizontal_spacing=0.08,
    )

    max_val = 0.0
    for i, title in enumerate(titles):
        xl, yl = series[title]
        max_val = max(max_val, max(yl) if len(yl) else 0)
        r, c = divmod(i, cols)
        fig.add_trace(go.Scatter(
            x=list(xl), y=list(yl), mode="lines",
            line=dict(color=theme.ac, width=2.5),
            name=title, showlegend=False,
        ), row=r + 1, col=c + 1)

    y_axis_keys = ["yaxis"] + [f"yaxis{i}" for i in range(2, rows * cols + 1)]
    for key in y_axis_keys:
        fig.update_layout(**{key: dict(
            range=[0, max_val * 1.15], gridcolor=theme.border, gridwidth=0.5,
        )})
    fig.update_layout(**theme.plotly_layout(), height=height)
    for ann in fig.layout.annotations:
        ann.font.size = 11
    return fig
