import plotly.graph_objects as go

from ibcs_plotly import (
    grouped_column,
    horizontal_bar,
    small_multiples,
    variance_waterfall,
)


def test_grouped_column_returns_figure():
    fig = grouped_column([10, 20, 30], [8, 22, 25], ["Jan", "Feb", "Mar"])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 3  # PY, AC, variance pin


def test_grouped_column_without_py_omits_variance_trace():
    fig = grouped_column([10, 20, 30], [0, 0, 0], ["Jan", "Feb", "Mar"], show_py=False)
    assert len(fig.data) == 1  # AC only


def test_horizontal_bar_sorts_by_ac_descending():
    fig = horizontal_bar(["A", "B", "C"], [5, 20, 10], [3, 15, 8])
    ac_trace = next(t for t in fig.data if t.name == "AC")
    assert list(ac_trace.y) == ["B", "C", "A"]


def test_horizontal_bar_without_py():
    fig = horizontal_bar(["A", "B"], [5, 20], show_py=False)
    assert len(fig.data) == 1


def test_variance_waterfall_total_matches_sum():
    fig = variance_waterfall(["Web", "Referral"], [40, 10], [30, 15])
    total_trace = fig.data[0]
    # last y value is the total bar
    assert total_trace.y[-1] == (40 - 30) + (10 - 15)


def test_small_multiples_shares_y_scale_across_panels():
    fig = small_multiples({
        "North": (["Jan", "Feb"], [10, 50]),
        "South": (["Jan", "Feb"], [5, 8]),
    })
    y_ranges = [
        fig.layout[k].range for k in fig.layout
        if k.startswith("yaxis")
    ]
    assert len(set(tuple(r) for r in y_ranges)) == 1
