"""Exports: turn a metrics result into a downloadable file.

CSV streams row-by-row (constant memory). xlsx uses openpyxl's write-only mode,
and parquet goes through pyarrow — both build in memory, which is why every
export is capped at `MAJRA_EXPORT_MAX_ROWS`.
"""
import csv
import io
import json
from collections.abc import Iterator
from datetime import date

from app.core.config import settings

FORMATS = ("csv", "json", "xlsx", "parquet")

MEDIA_TYPE = {
    "csv": "text/csv",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
}


def filename(dataset: str, date_from: date, date_to: date, fmt: str) -> str:
    return f"majra-{dataset}-{date_from}_{date_to}.{fmt}"


def _columns(rows: list[dict]) -> list[str]:
    return list(rows[0].keys()) if rows else []


_RATIO_COLS = ("ctr", "cpc", "cpm", "cpa", "roas")


def total_row(rows: list[dict]) -> dict | None:
    """A summary row: additive columns summed, the five ad ratios recomputed
    from the sums (never averaged), and a 'Total' label in the first non-numeric
    column. Returns None for an empty result."""
    if not rows:
        return None
    cols = list(rows[0].keys())
    t: dict = {}
    for c in cols:
        if c in _RATIO_COLS:
            t[c] = None
            continue
        nums = [
            r[c] for r in rows
            if isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
        ]
        t[c] = sum(nums) if nums else None
    spend, imp = t.get("spend") or 0, t.get("impressions") or 0
    clicks, conv = t.get("clicks") or 0, t.get("conversions") or 0
    value = t.get("conversion_value") or 0
    if "ctr" in t:
        t["ctr"] = round(clicks / imp * 100, 4) if imp else None
    if "cpc" in t:
        t["cpc"] = round(spend / clicks, 4) if clicks else None
    if "cpm" in t:
        t["cpm"] = round(spend / imp * 1000, 4) if imp else None
    if "cpa" in t:
        t["cpa"] = round(spend / conv, 4) if conv else None
    if "roas" in t:
        t["roas"] = round(value / spend, 4) if spend else None
    for c in cols:  # label the first column that isn't a summed number
        if t.get(c) is None and c not in _RATIO_COLS:
            t[c] = "Total"
            break
    return t


def stream_csv(rows: list[dict]) -> Iterator[str]:
    cols = _columns(rows)
    if not cols:
        return
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue()
    for row in rows:
        buf.seek(0)
        buf.truncate(0)
        writer.writerow(row)
        yield buf.getvalue()


def to_json(rows: list[dict]) -> bytes:
    return json.dumps({"data": rows}, default=str).encode()


def to_xlsx(rows: list[dict], sheet_name: str = "majra") -> bytes:
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=sheet_name[:31])
    cols = _columns(rows)
    if cols:
        ws.append(cols)
        for row in rows:
            ws.append([row.get(c) for c in cols])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_parquet(rows: list[dict]) -> bytes:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Parquet export needs pyarrow. Install it, or export CSV instead."
        ) from exc

    cols = _columns(rows)
    table = pa.table({c: [row.get(c) for row in rows] for c in cols} if cols else {})
    out = io.BytesIO()
    pq.write_table(table, out)
    return out.getvalue()


def cap() -> int:
    return settings.export_max_rows
