"""IBCS notation colors and layout tokens.

IBCS (International Business Communication Standards) fixes the meaning of
color in a report: actual, previous-period, and variance are always encoded
the same way so a reader never has to re-learn a legend from chart to chart.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IBCSTheme:
    ac: str = "#2C2C2A"          # AC - Actual (dark solid)
    py: str = "#B4B2A9"          # PY - Previous period (light solid)
    var_pos: str = "#639922"     # +Delta - positive variance
    var_neg: str = "#A32D2D"     # -Delta - negative variance
    bg_page: str = "#F7F6F3"
    bg_card: str = "#FFFFFF"
    text_primary: str = "#2C2C2A"
    text_secondary: str = "#73726C"
    border: str = "#D3D1C7"
    font_family: str = "Inter, Arial, sans-serif"

    def plotly_layout(self) -> dict:
        return dict(
            font=dict(family=self.font_family, size=12, color=self.text_primary),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=8, r=8, t=36, b=8),
            showlegend=True,
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="left", x=0,
                font=dict(size=11), bgcolor="rgba(0,0,0,0)",
            ),
        )


DEFAULT_THEME = IBCSTheme()
