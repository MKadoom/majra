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
