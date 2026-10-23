"""Dataset profiling: infer what each column *is* (measure, dimension, time...).

The planner never looks at raw rows directly; it works from this profile, so
the same logic runs whether an AI sees only the schema or the full data.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

import pandas as pd

ROLES = ("measure", "dimension", "time", "period", "id", "boolean", "text", "geo")

_PII_NAME = re.compile(
    r"(e-?mail|phone|mobile|ssn|passport|iban|address|first_?name|last_?name|"
    r"full_?name|^name$|dob|birth|password|token|secret|contact|card|cvv|iban|tax_?id)",
    re.I,
)
_PII_VALUE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"                      # email, also inside free text
    r"|^\+\d{8,15}$"                                 # international phone
    r"|^\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}$"              # US phone
    r"|^\d{3}-\d{2}-\d{4}$"                          # US SSN
    r"|^\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}$"         # payment card
    r"|\b\d{1,5}\s+(?:[A-Za-z0-9.']+\s+){1,3}"                  # street address:
    r"(?:st|street|ave|avenue|rd|road|blvd|dr|drive|ln|lane|way)\b"  # 502 Park Avenue
, re.I)
MAX_SCAN_CHARS = 256
_TIME_NAME = re.compile(r"(date|time|timestamp|^ts$|created|updated|_at$|_on$)", re.I)
_RATIO_NAME = re.compile(
    r"(pct|percent|rate|ratio|margin|share|average|^avg|_avg|mean|per_|_per_|index|score|"
    r"price|temperature|conversion|churn|utili[sz]ation)", re.I)
_GEO_NAME = re.compile(
    r"(^|_)(lat|latitude|lon|lng|long|longitude|geo|location)($|_)|coordinate", re.I)
_CODE_NAME = re.compile(
    r"(^|_)(zip|zipcode|postal|postcode|fips|code|district|ward|beat|precinct|council|"
    r"borough|community|bbl|naics|sic)($|_)", re.I)
_MONTHS = {m.lower() for m in (
    "January February March April May June July August September October November December "
    "Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec").split()}
_DATE_TOKEN = re.compile(r"[-/:]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", re.I)
_AGG_STRONG = {"world", "total", "all", "overall", "grand total", "all countries"}
_AGG_REGION = re.compile(
    r"^(africa|asia|europe|north america|south america|oceania|european union.*|"
    r"high-income.*|low-income.*|upper-middle-income.*|lower-middle-income.*|"
    r".*\((gcp|ember|eia)\)|.*\(excl\..*\))$", re.I)
_ID_NAME = re.compile(r"(?i:(^|[\s_-])id($|[\s_-])|uuid|key$)|[a-z]ID$")
_ID_LIKE_TEXT = re.compile(
    r"(?i:invoice|order|ticket|ref|sku|stock_?code|account|serial|number|^no$|_no$)"
    r"|(?i:(^|[\s_-])id($|[\s_-]))|[a-z]ID$")
_CODE_VALUE = re.compile(r"[A-Za-z]{1,3}[-_ ]?\d+")
_PERIOD_NAME = re.compile(r"^(year|yr|fy|fiscal_?year)$", re.I)


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    role: str
    n_unique: int
    null_pct: float
    pii: bool = False
    min: float | str | None = None
    max: float | str | None = None
    mean: float | None = None
    has_negatives: bool = False
    ratio: str | None = None  # "name" | "range" | "percent-text": summing would be wrong
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DatasetProfile:
    n_rows: int
    columns: list[ColumnProfile]
    notes: list[str] = field(default_factory=list)  # cleaning applied while coercing types

    def by_role(self, role: str) -> list[ColumnProfile]:
        """Columns with this role, excluding PII: sensitive columns are never charted."""
        return [c for c in self.columns if c.role == role and not c.pii]

    def get(self, name: str) -> ColumnProfile:
        return next(c for c in self.columns if c.name == name)

    def to_dict(self) -> dict:
        return {"n_rows": self.n_rows, "columns": [c.to_dict() for c in self.columns]}


def _is_text(s: pd.Series) -> bool:
    return s.dtype == object or pd.api.types.is_string_dtype(s)


_TZ_SUFFIX = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
_DATE_FORMATS = ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y")
_CURRENCY = re.compile(r"[$\u20ac\u00a3\u00a5\s]")


def _parse_numeric_text(s: pd.Series) -> pd.Series | None:
    """Parse '$1,200.50', '1.234,56', '12%', '(300)' to numbers; None if <90% parse."""
    raw = s.dropna().astype(str).str.strip()
    if raw.empty:
        return None
    sample = raw.head(200)
    eu = sample.str.fullmatch(r"[^\d]*\d{1,3}(\.\d{3})*,\d+[^\d]*").mean() > 0.5

    def clean(x: pd.Series) -> pd.Series:
        neg = x.str.fullmatch(r"\(.*\)")
        x = x.str.replace(_CURRENCY, "", regex=True).str.strip("()%")
        x = x.str.replace(".", "", regex=False).str.replace(",", ".", regex=False) if eu \
            else x.str.replace(",", "", regex=False)
        num = pd.to_numeric(x, errors="coerce")
        return num.where(~neg, -num)

    if clean(sample).notna().mean() < 0.9:
        return None
    out = clean(s.astype(str).str.strip().where(s.notna()))
    failed = raw[out.reindex(raw.index).isna()]
    if len(failed) and failed.str.fullmatch(_CODE_VALUE).mean() > 0.5:
        return None  # e.g. invoice numbers where "C489449" marks a cancellation: not a number
    return out


def _parse_dates(s: pd.Series, notes: list[str], name: str) -> pd.Series | None:
    """Return a datetime Series if >=90% of values look like dates, else None."""
    raw = s.dropna().astype(str)
    sample = raw.head(500)
    if set(sample.str.strip().str.lower().unique()[:50]) <= _MONTHS:
        return None  # month names alone are categories, not dates
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for fmt_group in (("%Y-%m-%d",), _DATE_FORMATS):
            ok = {f: pd.to_datetime(sample, format=f, errors="coerce").notna().mean()
                  for f in fmt_group}
            good = [f for f, r in ok.items() if r >= 0.98]
            if not good:
                continue
            if len(good) > 1:
                notes.append(f"'{name}': day/month order is ambiguous; assumed {good[0]}")
            return pd.to_datetime(s, format=good[0], errors="coerce")
        if sample.str.contains(_DATE_TOKEN).mean() < 0.9:
            return None  # codes like "0110" or "031A" must not become years
        utc = bool(sample.str.contains(_TZ_SUFFIX).mean() > 0.5)
        parsed = pd.to_datetime(sample, errors="coerce", utc=utc)
        if parsed.notna().mean() < 0.9 or not parsed.dt.year.between(1900, 2100).mean() >= 0.95:
            return None
        out = pd.to_datetime(s, errors="coerce", utc=utc)
        return out.dt.tz_convert("UTC").dt.tz_localize(None) if utc else out


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a frame for profiling: str column names, real dates and numbers.

    Handles date strings (ISO, d/m/Y, tz-aware), Unix timestamps in date-named
    columns, currency/percent/decimal-comma text, and +-inf (treated as missing).
    Anything it changes is recorded in `out.attrs["ibcs_notes"]`.
    """
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    if out.columns.duplicated().any():
        raise ValueError("duplicate column names: rename them before planning")
    notes: list[str] = list(df.attrs.get("ibcs_notes", []))
    percent_cols: list[str] = list(df.attrs.get("ibcs_percent_cols", []))
    for col in out.columns:
        s = out[col]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            out[col] = s.dt.tz_convert("UTC").dt.tz_localize(None)
        elif pd.api.types.is_float_dtype(s):
            if bool(((s == float("inf")) | (s == float("-inf"))).any()):
                out[col] = s.replace([float("inf"), float("-inf")], float("nan"))
                notes.append(f"'{col}': infinite values treated as missing")
        elif pd.api.types.is_integer_dtype(s) and _TIME_NAME.search(col):
            v = s.dropna()
            if len(v) and v.between(1e9, 4e9).all():
                out[col] = pd.to_datetime(s, unit="s")
            elif len(v) and v.between(1e12, 4e12).all():
                out[col] = pd.to_datetime(s, unit="ms")
        elif _is_text(s) and s.notna().any():
            if s.dropna().astype(str).str.fullmatch(r"-?\d+(\.\d+)?").all():
                continue
            dates = _parse_dates(s, notes, col)
            if dates is not None:
                out[col] = dates
                continue
            num = None if _ID_LIKE_TEXT.search(col) else _parse_numeric_text(s)
            if num is not None:
                out[col] = num
                if s.dropna().astype(str).str.strip().str.endswith("%").mean() > 0.5:
                    percent_cols.append(col)
                notes.append(f"'{col}': parsed from text (currency/percent/thousand separators)")
    out.attrs["ibcs_notes"] = notes
    out.attrs["ibcs_percent_cols"] = percent_cols
    return out


def _role(s: pd.Series, name: str, n_rows: int, n_unique: int) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "time"
    if pd.api.types.is_numeric_dtype(s):
        vals = s.dropna()
        integral = bool(len(vals)) and bool((vals % 1 == 0).all())
        if _PERIOD_NAME.match(name) and integral and vals.between(1000, 2200).all():
            return "period"
        if _GEO_NAME.search(name):
            return "geo"  # coordinates: location data, not a measure
        repeats = n_unique <= 1000 and n_unique <= 0.5 * max(n_rows, 1)  # a code list, not a key
        if _CODE_NAME.search(name) and integral:
            return "dimension" if repeats else "id"  # zip/district/ward codes
        if _ID_NAME.search(name):
            return "dimension" if repeats else "id"  # code lists vs. row keys
        if n_rows > 20 and n_unique == n_rows and pd.api.types.is_integer_dtype(s):
            return "id"
        if pd.api.types.is_integer_dtype(s) and n_unique <= 12 and n_rows > 4 * n_unique:
            return "dimension"  # small-integer codes behave like categories
        return "measure"
    nonnull = s.dropna().astype(str)
    if n_rows > 20 and n_unique == n_rows:
        return "id" if nonnull.str.len().mean() < 40 else "text"
    if len(nonnull) and nonnull.str.len().mean() > 40:
        return "text"
    return "dimension"


def aggregate_labels(s: pd.Series) -> list[str]:
    """Labels that are totals or regions mixed into a detail column (e.g. 'World', 'Asia')."""
    labels = list(s.dropna().astype(str).unique()[:5000])
    strong = [v for v in labels if v.strip().lower() in _AGG_STRONG]
    regions = [v for v in labels if _AGG_REGION.match(v.strip())]
    return strong + (regions if strong or len(regions) >= 3 else [])


def _pii_by_value(s: pd.Series) -> bool:
    """Value-based PII detection on a sample: emails, phones, SSNs, cards, embedded emails."""
    if pd.api.types.is_integer_dtype(s):
        v = s.dropna().abs()
        return bool(len(v) >= 5 and v.between(1e9, 1e15).mean() >= 0.95)  # phone-length ints
    if not _is_text(s):
        return False
    sample = s.dropna().head(200).astype(str)
    # clip first: the email pattern backtracks quadratically on long hostile strings
    sample = sample.str.slice(0, MAX_SCAN_CHARS)
    return bool(len(sample)) and sample.map(lambda v: bool(_PII_VALUE.search(v))).mean() >= 0.2


def _ratio_reason(s: pd.Series, name: str, percent_cols: set[str]) -> str | None:
    if _RATIO_NAME.search(name):
        return "name"
    if name in percent_cols:
        return "percent-text"
    v = s.dropna()
    if pd.api.types.is_float_dtype(s) and len(v) > 5 and v.nunique() > 2 \
            and v.min() >= 0 and v.max() <= 1:
        return "range"
    return None


def profile_dataframe(
    df: pd.DataFrame, pii_columns: Iterable[str] = ()
) -> DatasetProfile:
    """pii_columns: names the operator knows are sensitive, on top of the heuristics."""
    df = coerce_types(df)
    n = len(df)
    forced = {str(c) for c in pii_columns}
    percent_cols = set(df.attrs.get("ibcs_percent_cols", []))
    cols = []
    for name in df.columns:
        s = df[name]
        n_unique = int(s.nunique(dropna=True))
        role = _role(s, str(name), n, n_unique)
        pii = bool(str(name) in forced or _PII_NAME.search(str(name)) or _pii_by_value(s))

        cp = ColumnProfile(
            name=str(name), dtype=str(s.dtype), role=role, n_unique=n_unique,
            null_pct=round(float(s.isna().mean() * 100), 2) if n else 0.0, pii=pii,
        )
        if pii:
            cp.notes.append("looks like PII: no statistics or values are exposed")
        elif role in ("measure", "period") and s.notna().any():
            cp.min, cp.max = float(s.min()), float(s.max())
            cp.mean = float(s.mean())
            cp.has_negatives = bool((s < 0).any())
            if cp.has_negatives:
                cp.notes.append("contains negatives: variance % and share-of-total need care")
            if role == "measure":
                cp.ratio = _ratio_reason(s, str(name), percent_cols)
                if cp.ratio:
                    cp.notes.append("looks like a ratio/rate: averaged, not summed")
        elif role == "time" and s.notna().any():
            cp.min, cp.max = str(s.min().date()), str(s.max().date())
        if cp.null_pct > 20:
            cp.notes.append(f"{cp.null_pct}% missing")
        if role == "dimension" and n_unique > 30:
            cp.notes.append("high cardinality: show top-N, not every category")
        cols.append(cp)
    return DatasetProfile(n_rows=n, columns=cols, notes=list(df.attrs.get("ibcs_notes", [])))


def core_date_bounds(col: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Date range of the bulk of the data: 1st-99th percentile plus a 10% margin.

    Stray dates (a 2002 trip in a 2024 file) fall outside; real edge days stay inside.
    """
    lo, hi = col.quantile(0.01), col.quantile(0.99)
    pad = pd.Timedelta(days=1) + (hi - lo) * 0.1
    return lo - pad, hi + pad


def with_time_parts(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Add `<col>__ibcs_year` / `<col>__ibcs_month` helper columns for a datetime column."""
    out = df.copy()
    out[f"{col}__ibcs_year"] = out[col].dt.year
    out[f"{col}__ibcs_month"] = out[col].dt.month
    return out


__all__ = [
    "ROLES", "ColumnProfile", "DatasetProfile", "coerce_types",
    "profile_dataframe", "with_time_parts", "aggregate_labels", "core_date_bounds",
]
