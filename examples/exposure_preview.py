"""Exposure preview: see exactly what an AI assistant would receive from your data.

Run with: streamlit run examples/exposure_preview.py

Pick a tier, mark extra sensitive columns, and read the JSON an assistant would get, the plan it
would work from, and the charts that plan produces. Nothing leaves your machine: this page only
calls ibcs-plotly locally. The settings tab shows the matching MCP server environment variables.
"""

import io

import numpy as np
import pandas as pd
import streamlit as st

from ibcs_plotly import check_figure
from ibcs_plotly.planner import (
    Tier,
    build_plan,
    estimate_tokens,
    llm_context,
    profile_dataframe,
    render_spec,
    to_json,
)
from ibcs_plotly.planner.exposure import MIN_GROUP
from ibcs_plotly.streamlit_ext import inject_theme_css, kpi_card

MAX_UPLOAD_MB = 50
TIER_HELP = {
    "SCHEMA": "Column names, types and inferred roles only. No values at all.",
    "STATS": "Adds null %, min/max/mean, date ranges and top categories (small groups hidden).",
    "SAMPLE": "Adds a small random sample of rows. Sensitive columns are masked as ***.",
    "FULL": "Adds all rows (capped at 1,000). Sensitive columns are still masked.",
}


@st.cache_data
def sample_data(seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.date_range("2025-01-01", "2026-06-30")
    n = len(days)
    region = rng.choice(["North", "South", "East", "West"], n)
    region[:3] = "Rare Client A"  # a tiny group: hidden from STATS output
    return pd.DataFrame({
        "date": days.strftime("%Y-%m-%d"),
        "region": region,
        "product": rng.choice([f"P{i}" for i in range(6)], n),
        "customer_email": [f"user{i}@example.com" for i in range(n)],
        "revenue": rng.uniform(10, 500, n).round(2),
    })


@st.cache_data(show_spinner="Reading file...")
def read_upload(name: str, raw: bytes) -> pd.DataFrame:
    buf = io.BytesIO(raw)
    if name.endswith(".parquet"):
        return pd.read_parquet(buf)
    if name.endswith(".json"):
        return pd.read_json(buf)
    return pd.read_csv(buf, low_memory=False)


@st.cache_data(show_spinner="Building the preview...")
def preview(df: pd.DataFrame, tier: str, goal: str, pii: tuple, min_group: int, rows: int):
    profile = profile_dataframe(df, pii)
    ctx = llm_context(df, tier, goal or None, pii_columns=pii, min_group=min_group,
                      sample_rows=rows)
    plan = build_plan(df, tier, goal or None, profile=profile)
    return profile.to_dict(), ctx, plan


st.set_page_config(page_title="ibcs-plotly exposure preview", layout="wide")
inject_theme_css()
st.title("What would the AI see?")
st.caption("Nothing leaves your machine. Built with ibcs-plotly: "
           "https://github.com/Zain-ul-Abdin45/ibcs-plotly")

upload = st.sidebar.file_uploader(
    f"Dataset (CSV, Parquet or JSON, up to {MAX_UPLOAD_MB} MB)", type=["csv", "parquet", "json"])
if upload is not None and upload.size > MAX_UPLOAD_MB * 1024 * 1024:
    st.sidebar.error(f"File is over {MAX_UPLOAD_MB} MB.")
    upload = None
df = read_upload(upload.name, upload.getvalue()) if upload is not None else sample_data()
if upload is None:
    st.sidebar.caption("Showing built-in sample data. Upload a file to use your own.")

tier = st.sidebar.selectbox("Exposure tier", [t.name for t in Tier], index=0)
st.sidebar.caption(TIER_HELP[tier])
goal = st.sidebar.text_input("Measure to focus on (optional)", "")
pii = tuple(st.sidebar.multiselect("Also treat as sensitive", list(df.columns)))
min_group = st.sidebar.number_input("Hide groups smaller than", 1, 50, MIN_GROUP)
rows = st.sidebar.slider("Sample rows (SAMPLE tier)", 1, 100, 20)

profile, ctx, plan = preview(df, tier, goal, pii, int(min_group), rows)
flagged = [c["name"] for c in profile["columns"] if c["pii"]]

cards = [
    ("Tier", tier),
    ("Rows x columns", f"{len(df):,} x {df.shape[1]}"),
    ("Sensitive columns", str(len(flagged))),
    ("Approx. tokens", f"{estimate_tokens(ctx):,}"),
]
for col, (label, value) in zip(st.columns(4), cards):
    col.markdown(kpi_card(label, value, na_reason=""), unsafe_allow_html=True)
if flagged:
    st.warning("Detected as sensitive, never exposed and never charted: "
               + ", ".join(f"`{c}`" for c in flagged)
               + ". Detection is heuristic; add anything else in the sidebar.")

tab_ai, tab_cols, tab_plan, tab_charts, tab_ops = st.tabs(
    ["What the AI receives", "Columns", "Plan", "Charts", "Operator settings"])

with tab_ai:
    st.caption("The data section below is exactly what an assistant would be given.")
    st.json(ctx["data"], expanded=3)
    with st.expander("Instructions sent with it"):
        st.write(ctx["instructions"])
    st.download_button("Download this bundle (JSON)", to_json(ctx), "ibcs_context.json")

with tab_cols:
    st.dataframe(pd.DataFrame(profile["columns"])[["name", "role", "dtype", "pii", "ratio"]],
                 use_container_width=True, hide_index=True)

with tab_plan:
    st.markdown(plan.to_markdown())

with tab_charts:
    ready = [c for c in plan.charts if c.status == "ready"]
    if not ready:
        st.info("No chart is ready at this tier. See the Plan tab for why.")
    for spec in ready:
        try:
            fig = render_spec(df, spec)
        except Exception as e:  # noqa: BLE001 - show the problem instead of crashing the page
            st.error(f"{spec.title}: {type(e).__name__}")
            continue
        st.subheader(spec.title)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        findings = check_figure(fig)
        st.caption("IBCS check: " + ("no issues" if not findings else
                                     "; ".join(f"{f.severity}: {f.rule}" for f in findings)))

with tab_ops:
    st.caption("Set these when you start the MCP server to apply what you see here.")
    lines = [f"IBCS_MCP_MAX_TIER={tier}", f"IBCS_MCP_MIN_GROUP={int(min_group)}"]
    if pii:
        lines.append("IBCS_MCP_PII_COLUMNS=" + ",".join(pii))
    lines.append("IBCS_MCP_DATA_DIR=/path/to/your/data")
    st.code("\n".join(lines) + "\nibcs-plotly-mcp", language="bash")
    st.caption("The assistant can ask for less than this ceiling, never more.")
