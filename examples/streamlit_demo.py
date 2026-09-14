"""Example dashboard built on ibcs-plotly, using synthetic sales data.

Run with: streamlit run examples/streamlit_demo.py
"""

import numpy as np
import pandas as pd
import streamlit as st

from ibcs_plotly import compare_periods, grouped_column, has_comparison, horizontal_bar
from ibcs_plotly.streamlit_ext import inject_theme_css, kpi_card, legend_html


@st.cache_data
def synthetic_data(seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", "2026-08-31", freq="D")
    regions = ["EMEA", "AMER", "APAC"]
    n = len(dates) * 2
    return pd.DataFrame({
        "date": rng.choice(dates, n),
        "region": rng.choice(regions, n, p=[0.5, 0.3, 0.2]),
        "revenue": rng.gamma(shape=2.0, scale=800, size=n).round(0),
    }).assign(year=lambda d: d["date"].dt.year, month=lambda d: d["date"].dt.strftime("%b"))


st.set_page_config(page_title="ibcs-plotly demo", layout="wide")
inject_theme_css()

df = synthetic_data()
years = sorted(df["year"].unique(), reverse=True)
sel_year = st.sidebar.selectbox("Year", years, index=0)

df_ac, df_py, py_year = compare_periods(df, "year", sel_year)
show_py = has_comparison(df_py)

st.title("Regional Revenue — IBCS Demo")
st.caption("Synthetic data. Built with ibcs-plotly: https://github.com/YOUR_GITHUB_USERNAME/ibcs-plotly")

total_ac = df_ac["revenue"].sum()
total_py = df_py["revenue"].sum() if show_py else None
delta = None if total_py is None else (total_ac - total_py) / total_py * 100 if total_py else None

top_region = df_ac.groupby("region")["revenue"].sum().idxmax()

col1, col2 = st.columns(2)
col1.markdown(kpi_card("Total revenue", f"${total_ac:,.0f}", delta), unsafe_allow_html=True)
col2.markdown(kpi_card("Top region", top_region), unsafe_allow_html=True)

st.markdown(legend_html(show_py=show_py), unsafe_allow_html=True)

month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ac_by_month = df_ac.groupby("month")["revenue"].sum().reindex(month_order, fill_value=0)
py_by_month = (
    df_py.groupby("month")["revenue"].sum().reindex(month_order, fill_value=0)
    if show_py else [0] * 12
)

fig1 = grouped_column(
    ac_by_month.values, list(py_by_month), month_order, show_py=show_py, height=340,
)
st.plotly_chart(fig1, use_container_width=True, config={"displayModeBar": False})

regions = sorted(df["region"].unique())
ac_by_region = df_ac.groupby("region")["revenue"].sum().reindex(regions, fill_value=0)
py_by_region = (
    df_py.groupby("region")["revenue"].sum().reindex(regions, fill_value=0)
    if show_py else [0] * len(regions)
)

fig2 = horizontal_bar(
    regions, list(ac_by_region), list(py_by_region), show_py=show_py, height=260,
)
st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
