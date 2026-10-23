"""MCP server: lets an AI assistant inspect a dataset and plan IBCS charts.

Run:  IBCS_MCP_DATA_DIR=./data IBCS_MCP_MAX_TIER=STATS ibcs-plotly-mcp

The *operator* (whoever launches the server) controls exposure:
  IBCS_MCP_DATA_DIR   only files under this directory can be loaded (required)
  IBCS_MCP_MAX_TIER   SCHEMA | STATS | SAMPLE | FULL ceiling (default SCHEMA)
  IBCS_MCP_ALLOW_PII  "1" to stop masking PII columns in SAMPLE/FULL
  IBCS_MCP_PII_COLUMNS comma-separated column names to treat as PII, on top of the
                      name/value heuristics (which are best-effort, not a guarantee)
  IBCS_MCP_MIN_GROUP  category counts below this are hidden in STATS (default 5)
  IBCS_MCP_MAX_BYTES  largest file load_dataset will read (default 100 MB)
The AI can request a lower tier per call but cannot exceed the ceiling.
Rendering always happens locally on the full data; the AI only receives the
chart file path, not the rows.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import pandas as pd

from .lint import check_figure
from .planner import (
    CHART_KINDS,
    ChartSpec,
    Tier,
    build_plan,
    clamp_tier,
    expose,
    llm_context,
    render_spec,
)
from .planner.exposure import MIN_GROUP

MAX_DATASETS = 20
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
# low_memory=False: chunked type inference yields mixed-type columns on large files
_READERS = {".csv": functools.partial(pd.read_csv, low_memory=False),
            ".parquet": pd.read_parquet, ".json": pd.read_json, ".xlsx": pd.read_excel}


class Session:
    """Holds loaded datasets and the operator's exposure policy."""

    def __init__(self, data_dir: str | Path, max_tier: Tier = Tier.SCHEMA,
                 allow_pii: bool = False, pii_columns: tuple[str, ...] = (),
                 max_bytes: int = DEFAULT_MAX_BYTES, min_group: int = MIN_GROUP):
        self.data_dir = Path(data_dir).resolve()
        self.max_tier = max_tier
        self.allow_pii = allow_pii
        self.pii_columns = tuple(pii_columns)
        self.max_bytes = max_bytes
        self.min_group = min_group
        self.datasets: dict[str, pd.DataFrame] = {}
        self._written: set[Path] = set()  # files this session created; the only ones it may replace

    @classmethod
    def from_env(cls) -> "Session":
        data_dir = os.environ.get("IBCS_MCP_DATA_DIR")
        if not data_dir:
            raise SystemExit("Set IBCS_MCP_DATA_DIR to the directory the AI may read from.")
        pii = tuple(c.strip() for c in os.environ.get("IBCS_MCP_PII_COLUMNS", "").split(",")
                    if c.strip())
        return cls(data_dir, Tier.parse(os.environ.get("IBCS_MCP_MAX_TIER", "SCHEMA")),
                   os.environ.get("IBCS_MCP_ALLOW_PII") == "1", pii,
                   int(os.environ.get("IBCS_MCP_MAX_BYTES", DEFAULT_MAX_BYTES)),
                   int(os.environ.get("IBCS_MCP_MIN_GROUP", MIN_GROUP)))

    def load(self, path: str, name: str | None = None) -> dict:
        full = (self.data_dir / path).resolve()
        if not full.is_relative_to(self.data_dir):
            raise PermissionError("path escapes IBCS_MCP_DATA_DIR")
        reader = _READERS.get(full.suffix.lower())
        if reader is None:
            raise ValueError(f"unsupported file type {full.suffix!r}; use {sorted(_READERS)}")
        if not full.is_file():
            raise FileNotFoundError(path)
        if full.stat().st_size > self.max_bytes:
            raise ValueError(f"file is larger than the {self.max_bytes}-byte limit")
        name = name or full.stem
        if name not in self.datasets and len(self.datasets) >= MAX_DATASETS:
            raise ValueError(f"at most {MAX_DATASETS} datasets can be loaded at once")
        try:
            self.datasets[name] = reader(full)
        except ImportError as e:
            raise ValueError(f"missing optional dependency for {full.suffix}: {e}") from e
        except Exception as e:  # parser messages can quote file content: keep them server-side
            raise ValueError(f"could not parse {full.name} as {full.suffix[1:]}") from e
        df = self._df(name)
        return {"name": name, "n_rows": len(df), "n_columns": df.shape[1],
                "max_tier": self.max_tier.name}

    def _df(self, name: str) -> pd.DataFrame:
        try:
            return self.datasets[name]
        except KeyError:
            raise ValueError(f"dataset {name!r} is not loaded; call load_dataset first "
                             f"(loaded: {sorted(self.datasets)})") from None

    @staticmethod
    def _spec(spec) -> ChartSpec:
        fields = {"kind", "title", "params", "rationale", "status", "issues"}
        if not isinstance(spec, dict) or "kind" not in spec:
            raise ValueError("spec must be an object with 'kind' and 'params': copy one chart "
                             "from the plan_charts result")
        if set(spec) - fields:
            raise ValueError(f"unknown spec fields {sorted(set(spec) - fields)}; "
                             f"allowed: {sorted(fields)}")
        if spec["kind"] not in CHART_KINDS:
            raise ValueError(f"kind must be one of {list(CHART_KINDS)}")
        return ChartSpec(**spec)

    def _tier(self, requested: str) -> Tier:
        return clamp_tier(Tier.parse(requested), self.max_tier)

    def describe(self, name: str, tier: str = "SCHEMA", sample_rows: int = 20) -> dict:
        return expose(self._df(name), self._tier(tier), sample_rows=sample_rows,
                      allow_pii=self.allow_pii, pii_columns=self.pii_columns,
                      min_group=self.min_group)

    def plan(self, name: str, tier: str = "SCHEMA", goal: str | None = None) -> dict:
        t = self._tier(tier)
        plan = build_plan(self._df(name), t, goal, pii_columns=self.pii_columns)
        return {**plan.to_dict(), "markdown": plan.to_markdown()}

    def context(self, name: str, tier: str = "SCHEMA", goal: str | None = None) -> dict:
        return llm_context(self._df(name), self._tier(tier), goal,
                           allow_pii=self.allow_pii, pii_columns=self.pii_columns,
                           min_group=self.min_group)

    def check(self, name: str, spec: dict) -> list[dict]:
        fig = render_spec(self._df(name), self._spec(spec))
        findings = [f.to_dict() for f in check_figure(fig)]
        if self.max_tier < Tier.STATS:
            # messages quote figure values (axis starts, bar counts): rule + severity only
            findings = [{"rule": f["rule"], "severity": f["severity"]} for f in findings]
        return findings

    def render(self, name: str, spec: dict, out_path: str) -> dict:
        out = (self.data_dir / out_path).resolve()
        if not out.is_relative_to(self.data_dir) or out.suffix not in (".html", ".json"):
            raise PermissionError("output must be a .html or .json file under IBCS_MCP_DATA_DIR")
        if out.exists() and out not in self._written:
            raise FileExistsError("refusing to overwrite a file this session did not create")
        fig = render_spec(self._df(name), self._spec(spec))
        self._written.add(out)
        out.write_text(fig.to_html(include_plotlyjs="cdn") if out.suffix == ".html"
                       else fig.to_json())
        return {"written": str(out)}


def _tool_error_class():
    try:
        from mcp.server.mcpserver.exceptions import ToolError  # mcp >= 2
    except ImportError:
        from mcp.server.fastmcp.exceptions import ToolError  # mcp 1.x
    return ToolError


def _guard(fn):
    """Turn expected refusals into short client-facing errors (no server traceback)."""
    ToolError = _tool_error_class()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as e:
            raise ToolError(f"unknown dataset or column: {e}") from None
        except (PermissionError, FileExistsError, FileNotFoundError, ValueError) as e:
            raise ToolError(str(e) or type(e).__name__) from None

    return wrapper


def build_server(session: Session):
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:  # pragma: no cover
            raise SystemExit("Install the MCP extra: pip install 'ibcs-plotly[mcp]'") from e

    mcp = Server("ibcs-plotly")

    @mcp.tool()
    @_guard
    def load_dataset(path: str, name: str | None = None) -> dict:
        """Load a CSV/Parquet/JSON/XLSX file from the operator's data directory."""
        return session.load(path, name)

    @mcp.tool()
    @_guard
    def describe_dataset(name: str, tier: str = "SCHEMA", sample_rows: int = 20) -> dict:
        """Describe a dataset at an exposure tier: SCHEMA, STATS, SAMPLE or FULL.
        Capped by the operator's ceiling; start at SCHEMA and escalate only if needed."""
        return session.describe(name, tier, sample_rows)

    @mcp.tool()
    @_guard
    def plan_charts(name: str, tier: str = "SCHEMA", goal: str | None = None) -> dict:
        """Return a development plan: column roles, feasible calculations
        (variance, share, rank, YTD...), IBCS chart specs, warnings, next steps."""
        return session.plan(name, tier, goal)

    @mcp.tool()
    @_guard
    def llm_context_bundle(name: str, tier: str = "SCHEMA", goal: str | None = None) -> dict:
        """One bundle (instructions + data view + plan + spec schema) at an exposure tier."""
        return session.context(name, tier, goal)

    @mcp.tool()
    @_guard
    def check_chart(name: str, spec: dict) -> list[dict]:
        """Render a ChartSpec locally and return IBCS compliance findings (no data leaves)."""
        return session.check(name, spec)

    @mcp.tool()
    @_guard
    def render_chart(name: str, spec: dict, out_path: str) -> dict:
        """Render a ChartSpec from plan_charts to an .html or .json file."""
        return session.render(name, spec, out_path)

    return mcp


def main() -> None:  # pragma: no cover
    build_server(Session.from_env()).run()


if __name__ == "__main__":  # pragma: no cover
    main()
