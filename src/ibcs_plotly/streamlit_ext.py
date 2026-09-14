"""Optional Streamlit components: KPI cards, the AC/PY/variance legend, and
the base CSS. Import this module only if streamlit is installed
(`pip install ibcs-plotly[streamlit]`).
"""

from __future__ import annotations

import numpy as np

from .theme import DEFAULT_THEME, IBCSTheme


def base_css(theme: IBCSTheme = DEFAULT_THEME) -> str:
    return f"""
<style>
  .stApp {{ background-color: {theme.bg_page}; }}
  .ibcs-kpi-card {{
    background: {theme.bg_card}; border: 0.5px solid {theme.border};
    border-radius: 10px; padding: 1rem 1.25rem;
  }}
  .ibcs-kpi-label {{
    font-size: 11px; font-weight: 500; letter-spacing: .06em;
    text-transform: uppercase; color: {theme.text_secondary}; margin-bottom: 4px;
  }}
  .ibcs-kpi-value {{
    font-size: 28px; font-weight: 600; color: {theme.text_primary}; line-height: 1.1;
  }}
  .ibcs-kpi-delta-pos {{ font-size: 12px; color: {theme.var_pos}; font-weight: 500; }}
  .ibcs-kpi-delta-neg {{ font-size: 12px; color: {theme.var_neg}; font-weight: 500; }}
  .ibcs-kpi-delta-na  {{ font-size: 12px; color: {theme.text_secondary}; font-style: italic; }}
  .ibcs-pill {{ display:inline-flex;align-items:center;gap:5px;font-size:11px;
                color:{theme.text_secondary};margin-right:12px; }}
  .ibcs-swatch {{ width:14px;height:14px;border-radius:2px;
                  display:inline-block;flex-shrink:0; }}
</style>
"""


def inject_theme_css(theme: IBCSTheme = DEFAULT_THEME) -> None:
    import streamlit as st

    st.markdown(base_css(theme), unsafe_allow_html=True)


def kpi_card(
    label: str,
    value: str,
    delta_pct: float | None = None,
    *,
    suffix: str = "",
    na_reason: str = "N/A",
) -> str:
    """Returns an HTML snippet - pass to st.markdown(..., unsafe_allow_html=True)."""
    if delta_pct is None:
        delta_html = f'<div class="ibcs-kpi-delta-na">{na_reason}</div>'
    elif np.isfinite(delta_pct):
        cls = "ibcs-kpi-delta-pos" if delta_pct >= 0 else "ibcs-kpi-delta-neg"
        arrow = "↑" if delta_pct >= 0 else "↓"
        delta_html = f'<div class="{cls}">{arrow} {abs(delta_pct):.1f}% vs PY</div>'
    else:
        delta_html = '<div class="ibcs-kpi-delta-na">No PY data</div>'
    return f"""
    <div class="ibcs-kpi-card">
      <div class="ibcs-kpi-label">{label}</div>
      <div class="ibcs-kpi-value">{value}{suffix}</div>
      {delta_html}
    </div>"""


def legend_html(theme: IBCSTheme = DEFAULT_THEME, *, show_py: bool = True) -> str:
    py_opacity = "" if show_py else ' style="opacity:.4;"'
    py_note = "PY &mdash; Prev. period" if show_py else "PY &mdash; n/a"
    return f"""
    <div style="display:flex;flex-wrap:wrap;gap:0;margin-bottom:14px;">
      <span class="ibcs-pill">
        <span class="ibcs-swatch" style="background:{theme.ac};"></span>AC &mdash; Actual
      </span>
      <span class="ibcs-pill"{py_opacity}>
        <span class="ibcs-swatch" style="background:{theme.py};"></span>{py_note}
      </span>
      <span class="ibcs-pill">
        <span class="ibcs-swatch" style="background:{theme.var_pos};"></span>+Delta Variance
      </span>
      <span class="ibcs-pill">
        <span class="ibcs-swatch" style="background:{theme.var_neg};"></span>-Delta Variance
      </span>
    </div>"""
