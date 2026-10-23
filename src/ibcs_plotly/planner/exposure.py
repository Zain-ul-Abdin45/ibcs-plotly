"""Exposure tiers: how much of a dataset an AI (or any outside consumer) may see.

Tiers are cumulative. The data owner picks a ceiling; a caller can ask for
less than the ceiling but never more.

    SCHEMA  column names, dtypes, inferred roles. No values at all.
    STATS   + null %, numeric min/max/mean, date ranges, top category counts.
    SAMPLE  + a small random sample of rows (PII columns masked).
    FULL    + all rows up to `max_rows` (PII columns masked).

PII columns (by name or by email-shaped values) are masked in SAMPLE/FULL
unless `allow_pii=True`, and never contribute category values to STATS.
"""

from __future__ import annotations

from enum import IntEnum

import pandas as pd

from .profile import DatasetProfile, coerce_types, profile_dataframe

MASK = "***"
MAX_SAMPLE_ROWS = 100  # hard cap for SAMPLE, whatever the caller asks for
MIN_GROUP = 5  # category counts below this are hidden: small groups can identify people
MAX_TEXT = 80  # longest string passed on; cell text is untrusted input to an LLM


class Tier(IntEnum):
    SCHEMA = 0
    STATS = 1
    SAMPLE = 2
    FULL = 3

    @classmethod
    def parse(cls, value: str | int | "Tier") -> "Tier":
        if isinstance(value, Tier):
            return value
        if isinstance(value, int):
            return cls(value)
        try:
            return cls[str(value).strip().upper()]
        except KeyError:
            raise ValueError(f"unknown tier {value!r}; use one of {[t.name for t in cls]}")


def clamp_tier(requested: Tier, ceiling: Tier) -> Tier:
    """A caller may ask for less than the owner's ceiling, never more."""
    return min(requested, ceiling)


def _clip(v):
    return v[:MAX_TEXT] + "..." if isinstance(v, str) and len(v) > MAX_TEXT else v


def _jsonable(df: pd.DataFrame) -> list[dict]:
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    rows = out.astype(object).where(out.notna(), None).to_dict(orient="records")
    return [{_clip(k): _clip(v) for k, v in r.items()} for r in rows]


def expose(
    df: pd.DataFrame,
    tier: Tier | str = Tier.SCHEMA,
    *,
    sample_rows: int = 20,
    max_rows: int = 1000,
    top_k: int = 5,
    allow_pii: bool = False,
    seed: int = 0,
    profile: DatasetProfile | None = None,
    pii_columns: tuple[str, ...] = (),
    min_group: int = MIN_GROUP,
) -> dict:
    """Return a JSON-serialisable view of `df` limited to `tier`."""
    tier = Tier.parse(tier)
    df = coerce_types(df)
    profile = profile or profile_dataframe(df, pii_columns)
    pii_cols = {c.name for c in profile.columns if c.pii}

    out: dict = {
        "tier": tier.name,
        "n_rows": profile.n_rows,
        "columns": [
            {"name": _clip(c.name), "dtype": c.dtype, "role": c.role, "pii": c.pii}
            for c in profile.columns
        ],
    }
    if tier >= Tier.STATS:
        out["stats"] = {}
        for c in profile.columns:
            st = {"n_unique": c.n_unique, "null_pct": c.null_pct, "notes": c.notes}
            if c.min is not None:
                st.update(min=c.min, max=c.max)
            if c.mean is not None:
                st["mean"] = c.mean
            if c.role in ("dimension", "boolean") and c.name not in pii_cols:
                vc = df[c.name].value_counts()
                st["top_values"] = {_clip(str(k)): int(v)
                                    for k, v in vc.head(top_k).items() if v >= min_group}
                if (vc < min_group).any():
                    st["small_groups_hidden"] = True  # labels and counts withheld
            out["stats"][_clip(c.name)] = st

    if tier >= Tier.SAMPLE:
        n = max_rows if tier == Tier.FULL else max(0, min(sample_rows, MAX_SAMPLE_ROWS))
        view = df if len(df) <= n else df.sample(n=n, random_state=seed).sort_index()
        if not allow_pii:
            view = view.copy()
            for c in pii_cols:
                view[c] = MASK
        out["rows"] = _jsonable(view)
        out["rows_truncated"] = len(view) < len(df)
    return out
