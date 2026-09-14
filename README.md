# ibcs-plotly

Interactive [IBCS](https://www.ibcs.com/)-compliant business charts on Plotly, with optional Streamlit components.

IBCS (International Business Communication Standards) fixes a small set of
rules for business reports: current-period and comparison-period values get
consistent colors, variance gets its own consistent colors, composition
charts are sorted bars instead of pies, and comparable small multiples share
one scale. This library gives you those chart types as plain functions that
return a `plotly.graph_objects.Figure`, plus a thin Streamlit layer for KPI
cards and the AC/PY/variance legend.

There's an existing IBCS-inspired Python package,
[clean_business_chart](https://github.com/MarcelW1323/clean_business_chart),
built on matplotlib. This one targets interactive dashboards (Plotly +
Streamlit) rather than static reports.

## Install

```bash
pip install ibcs-plotly            # core: plotly + pandas + numpy
pip install ibcs-plotly[streamlit] # + Streamlit KPI cards / legend
```

## Quick example

```python
from ibcs_plotly import grouped_column, compare_periods

df_ac, df_py, py_year = compare_periods(df, period_col="year", current=2026)

fig = grouped_column(
    ac=df_ac.groupby("month")["revenue"].sum(),
    py=df_py.groupby("month")["revenue"].sum(),
    labels=month_names,
)
fig.show()
```

See [`examples/streamlit_demo.py`](examples/streamlit_demo.py) for a full
dashboard on synthetic data — run it with:

```bash
pip install -e ".[streamlit]"
streamlit run examples/streamlit_demo.py
```

## What's in v0.1

- `grouped_column`, `horizontal_bar`, `variance_waterfall`, `small_multiples`
  — chart primitives, framework-agnostic
- `compare_periods`, `safe_delta_pct` — period-over-period comparison helpers
- `IBCSTheme` — colors/layout as a dataclass you can override
- `ibcs_plotly.streamlit_ext` — `kpi_card`, `legend_html`, `inject_theme_css`

Not in scope for v0.1: matplotlib/other backends, PDF/PPT export, a
chart-linting/compliance checker.

## License

MIT
