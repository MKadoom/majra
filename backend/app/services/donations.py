"""Jood order import and the claims-vs-truth comparison.

The ad platforms report the donations they *claim*. Several can claim the same
donation, each with its own attribution window, so their sum is not the number
of donations. The Jood store records each donation once. This module:

1. **parses** a Jood order export (CSV or XLSX). Header names are matched against
   Arabic and English aliases. Anything not recognised can be mapped explicitly,
   and a dry run shows what was detected before anything is written;
2. **normalises** each row: Arabic-Indic digits, "ر.س"/"SAR" amounts, dates read
   as Riyadh local time, and whether the status counts as a paid donation;
3. **attributes** each order to one paid channel. It uses an explicit platform
   column first, then the Jood per-platform ad link (owner-supplied mapping),
   then `utm_source`. Otherwise the order is "unattributed";
4. **upserts** it into app.donation_orders (re-importing the same export updates
   rows in place);
5. **compares**, per platform and period, Jood's paid orders and amount with the
   purchases and purchase value that platform claims in majra (same reporting
   currency, SAR by default).
"""
from __future__ import annotations

import base64
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import DonationOrder

RIYADH = timezone(timedelta(hours=3), "Asia/Riyadh")   # no DST: a fixed offset is exact
MAX_ROWS = 100_000

# canonical field -> header aliases (compared after normalising: lowercase,
# trimmed, inner whitespace / _ / - collapsed to one space)
ALIASES: dict[str, list[str]] = {
    "order_id": ["order id", "order number", "order no", "order", "id", "transaction id",
                 "رقم الطلب", "رقم العملية", "رقم التبرع", "رقم الفاتورة", "الطلب", "المعرف"],
    "ordered_at": ["date", "created at", "order date", "date time", "datetime", "created",
                   "تاريخ الطلب", "التاريخ", "تاريخ التبرع", "تاريخ الإنشاء", "تاريخ الانشاء",
                   "وقت الطلب"],
    "amount": ["amount", "total", "total amount", "grand total", "order total", "value",
               "paid amount", "المبلغ", "الإجمالي", "الاجمالي", "المجموع", "قيمة التبرع",
               "مبلغ التبرع", "المبلغ المدفوع", "قيمة الطلب"],
    "currency": ["currency", "العملة"],
    "status": ["status", "payment status", "order status", "الحالة", "حالة الطلب",
               "حالة الدفع"],
    "platform": ["platform", "ad platform", "channel", "المنصة", "المنصة الإعلانية",
                 "القناة"],
    "utm_source": ["utm source", "source", "المصدر"],
    "utm_medium": ["utm medium", "medium", "الوسيط"],
    "utm_campaign": ["utm campaign", "campaign", "الحملة"],
    "link": ["link", "ad link", "tracking link", "marketing link", "الرابط", "رابط",
             "رابط الإعلان", "رابط الاعلان", "رابط التتبع", "الرابط التسويقي"],
}
REQUIRED = ("order_id", "ordered_at", "amount")

PAID_STATUSES = {
    "paid", "completed", "complete", "success", "successful", "succeeded", "done",
    "approved", "captured", "مكتمل", "مكتملة", "مدفوع", "مدفوعة", "تم الدفع", "ناجح",
    "ناجحة", "تم", "مقبول", "مقبولة", "تم التبرع",
}

# utm_source / platform text -> majra platform key
PLATFORM_ALIASES: dict[str, list[str]] = {
    "meta": ["meta", "facebook", "fb", "instagram", "ig", "ميتا", "فيسبوك", "انستقرام",
             "انستغرام"],
    "snapchat": ["snapchat", "snap", "سناب", "سناب شات"],
    "tiktok": ["tiktok", "tik tok", "تيك توك"],
    "google_ads": ["google", "google ads", "googleads", "adwords", "gads", "youtube",
                   "قوقل", "جوجل"],
    "whatsapp": ["whatsapp", "wa", "واتساب", "واتس"],
}
PLATFORMS = list(PLATFORM_ALIASES) + ["unattributed"]

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(h or "").strip().lower())


def platform_of(text: str | None) -> str | None:
    if not text:
        return None
    t = _norm_header(text)
    for key, names in PLATFORM_ALIASES.items():
        if t in names or any(t.startswith(n + " ") for n in names):
            return key
    return None


def parse_amount(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).translate(_ARABIC_DIGITS)
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "")                 # 1,234.50
    elif s.count(",") == 1 and len(s.split(",")[1]) in (1, 2):
        s = s.replace(",", ".")                # 1234,50
    else:
        s = s.replace(",", "")                 # 1,234
    try:
        return float(s)
    except ValueError:
        return None


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %I:%M %p", "%d/%m/%Y %I:%M %p",
)


def parse_datetime(v) -> datetime | None:
    """Naive values are Jood's local time (Riyadh). Aware ones are honoured."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, date):
        dt = datetime(v.year, v.month, v.day)
    else:
        s = str(v).translate(_ARABIC_DIGITS).strip()
        s = s.replace("ص", "AM").replace("م", "PM")
        dt = None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=RIYADH)


def is_paid(status: str | None) -> bool:
    if status is None or str(status).strip() == "":
        return True                    # no status column/value: the export lists paid orders
    return _norm_header(status) in PAID_STATUSES


# ---------------------------------------------------------------- reading
def read_table(filename: str, content: str | None, content_base64: str | None) -> list[dict]:
    """Rows as dicts keyed by the file's own header strings."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        if not content_base64:
            raise ValueError("an .xlsx file must be sent as content_base64")
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(base64.b64decode(content_base64)),
                           read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None) or []
        keys = [str(h).strip() if h is not None else "" for h in header]
        out = [dict(zip(keys, r, strict=False)) for r in rows
               if any(c not in (None, "") for c in r)]
    else:
        text = content
        if text is None and content_base64:
            raw = base64.b64decode(content_base64)
            text = raw.decode("utf-8-sig", errors="replace")
        if text is None:
            raise ValueError("send the file as content (CSV text) or content_base64")
        text = text.lstrip("﻿")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        out = [r for r in csv.DictReader(io.StringIO(text), dialect=dialect)
               if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    if len(out) > MAX_ROWS:
        raise ValueError(f"too many rows ({len(out)}); split the export below {MAX_ROWS}")
    return out


def detect_mapping(headers: list[str], override: dict | None = None) -> dict[str, str]:
    """canonical field -> the file's header. Explicit `override` wins."""
    norm = {_norm_header(h): h for h in headers if h}
    mapping: dict[str, str] = {}
    for fld, names in ALIASES.items():
        for n in [fld.replace("_", " ")] + names:
            if n in norm and norm[n] not in mapping.values():
                mapping[fld] = norm[n]
                break
    for fld, header in (override or {}).items():
        if fld in ALIASES and header:
            if header not in headers:
                raise ValueError(f"mapping for {fld!r}: column {header!r} is not in the file")
            mapping[fld] = header
    return mapping


@dataclass
class ImportResult:
    mapping: dict
    unmapped_columns: list[str]
    rows_read: int = 0
    valid: int = 0
    paid: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: list[dict] = field(default_factory=list)
    statuses: dict = field(default_factory=dict)
    by_platform: dict = field(default_factory=dict)
    date_range: list | None = None
    dry_run: bool = True

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__} | {
            "skipped": self.skipped[:50], "skipped_count": len(self.skipped)}


def normalise_rows(rows: list[dict], mapping: dict, link_platforms: dict | None,
                   default_currency: str = "SAR") -> tuple[list[dict], list[dict], dict]:
    missing = [f for f in REQUIRED if f not in mapping]
    if missing:
        raise ValueError(
            "could not find the column(s) for " + ", ".join(missing)
            + ": pass them in `mapping`, e.g. {\"order_id\": \"<header>\"}")
    links = {str(k).strip().lower(): v for k, v in (link_platforms or {}).items()}
    for v in links.values():
        if v not in PLATFORMS:
            raise ValueError(f"link_platforms: unknown platform {v!r} (use one of {PLATFORMS})")

    def get(r, f):
        h = mapping.get(f)
        v = r.get(h) if h else None
        return v.strip() if isinstance(v, str) else v

    good, bad, statuses = [], [], {}
    seen: set[str] = set()
    for i, r in enumerate(rows, start=2):           # row 1 is the header
        oid = get(r, "order_id")
        oid = str(oid).translate(_ARABIC_DIGITS).strip() if oid not in (None, "") else ""
        if isinstance(get(r, "order_id"), float) and float(oid).is_integer():
            oid = str(int(float(oid)))
        when = parse_datetime(get(r, "ordered_at"))
        amount = parse_amount(get(r, "amount"))
        reason = ("no order id" if not oid else "unreadable date" if when is None
                  else "unreadable amount" if amount is None
                  else "duplicate order id in file" if oid in seen else None)
        if reason:
            bad.append({"row": i, "reason": reason})
            continue
        seen.add(oid)
        status = get(r, "status")
        status = str(status) if status not in (None, "") else None
        statuses[status or "(none)"] = statuses.get(status or "(none)", 0) + 1
        link = get(r, "link")
        link = str(link) if link not in (None, "") else None
        src = get(r, "utm_source")
        platform = platform_of(get(r, "platform"))
        if platform is None and link:
            ll = link.lower()
            platform = next((p for k, p in links.items() if k and k in ll), None)
        if platform is None:
            platform = platform_of(src)
        cur = get(r, "currency")
        good.append({
            "external_id": oid[:120],
            "ordered_at": when,
            "date": when.astimezone(RIYADH).date(),
            "amount": round(amount, 2),
            "currency": (str(cur).strip().upper()[:10] if cur else default_currency),
            "status": status[:60] if status else None,
            "is_paid": is_paid(status),
            "platform": platform or "unattributed",
            "utm_source": str(src)[:120] if src else None,
            "utm_medium": (str(get(r, "utm_medium"))[:120] if get(r, "utm_medium") else None),
            "utm_campaign": (str(get(r, "utm_campaign"))[:200]
                             if get(r, "utm_campaign") else None),
            "link": link,
            "raw": {k: (v.isoformat() if isinstance(v, (date, datetime)) else v)
                    for k, v in r.items() if k},
        })
    return good, bad, statuses


def import_orders(db: Session, user_id: int, *, filename: str, content: str | None = None,
                  content_base64: str | None = None, mapping: dict | None = None,
                  link_platforms: dict | None = None, default_currency: str = "SAR",
                  dry_run: bool = True) -> ImportResult:
    rows = read_table(filename, content, content_base64)
    headers = list(rows[0].keys()) if rows else []
    m = detect_mapping(headers, mapping)
    res = ImportResult(mapping=m, dry_run=dry_run,
                       unmapped_columns=[h for h in headers if h and h not in m.values()],
                       rows_read=len(rows))
    good, bad, statuses = normalise_rows(rows, m, link_platforms, default_currency)
    res.valid, res.skipped, res.statuses = len(good), bad, statuses
    res.paid = sum(1 for g in good if g["is_paid"])
    for g in good:
        if g["is_paid"]:
            p = res.by_platform.setdefault(g["platform"], {"orders": 0, "amount": 0.0})
            p["orders"] += 1
            p["amount"] = round(p["amount"] + g["amount"], 2)
    if good:
        days = [g["date"] for g in good]
        res.date_range = [min(days).isoformat(), max(days).isoformat()]
    if dry_run or not good:
        return res

    existing = set(db.scalars(select(DonationOrder.external_id).where(
        DonationOrder.user_id == user_id,
        DonationOrder.external_id.in_([g["external_id"] for g in good]))))
    for chunk in range(0, len(good), 1000):
        batch = [{**g, "user_id": user_id, "source": "jood"} for g in good[chunk:chunk + 1000]]
        stmt = insert(DonationOrder).values(batch)
        cols = {c: stmt.excluded[c] for c in (
            "ordered_at", "date", "amount", "currency", "status", "is_paid", "platform",
            "utm_source", "utm_medium", "utm_campaign", "link", "raw")}
        cols["imported_at"] = func.now()
        db.execute(stmt.on_conflict_do_update(
            constraint="uq_donation_orders_user_ext", set_=cols))
    db.commit()
    res.updated = len(existing)
    res.inserted = len(good) - len(existing)
    return res


# -------------------------------------------------------------- comparison
def donation_summary(db: Session, user_id: int, date_from: date, date_to: date,
                     reporting_currency: str = "SAR") -> dict:
    """Paid Jood orders per platform, amounts converted to one currency."""
    from app.services.metrics import fx_rates

    rates = fx_rates(db)
    rep = reporting_currency.upper()
    r_rep = rates.get(rep)
    if not r_rep:
        raise ValueError(f"no FX rate for reporting currency {rep}")
    rows = db.execute(
        select(DonationOrder.platform, DonationOrder.currency,
               func.count(), func.sum(DonationOrder.amount))
        .where(DonationOrder.user_id == user_id, DonationOrder.is_paid.is_(True),
               DonationOrder.date >= date_from, DonationOrder.date <= date_to)
        .group_by(DonationOrder.platform, DonationOrder.currency)
    ).all()
    out: dict[str, dict] = {}
    warnings: list[str] = []
    for platform, ccy, n, amt in rows:
        r = rates.get((ccy or "").upper())
        p = out.setdefault(platform, {"orders": 0, "amount": 0.0})
        p["orders"] += int(n)
        if r:
            p["amount"] += float(amt or 0) * r / r_rep
        else:
            warnings.append(f"no FX rate for {ccy}: {n} Jood order(s) left out of amounts")
    for p in out.values():
        p["amount"] = round(p["amount"], 2)
    return {"currency": rep, "by_platform": out, "warnings": warnings}


def compare(db: Session, user_id: int, date_from: date, date_to: date,
            reporting_currency: str = "SAR") -> dict:
    """Each platform's claimed purchases vs the Jood donations attributed to it.

    `claim_ratio` > 1 means the platform claims more donations than Jood
    attributes to its links/UTMs: view-through, longer attribution windows,
    duplicate events, or orders really attributed elsewhere. It is per-platform
    evidence, not a correction: the platform's Ads Manager still shows its own
    number.
    """
    from app.services.metrics import query_metrics

    jood = donation_summary(db, user_id, date_from, date_to, reporting_currency)
    claims = query_metrics(db, date_from=date_from, date_to=date_to, group_by=["platform"],
                           reporting_currency=reporting_currency, user_id=user_id,
                           page_size=100)
    claimed = {r["platform"]: r for r in claims["data"]}
    rows = []
    for p in sorted(set(claimed) | set(jood["by_platform"])):
        c = claimed.get(p, {})
        j = jood["by_platform"].get(p, {"orders": 0, "amount": 0.0})
        cp = int(c.get("purchases") or 0)
        rows.append({
            "platform": p,
            "claimed_purchases": cp,
            "claimed_value": round(float(c.get("purchase_value") or 0), 2),
            "spend": round(float(c.get("spend") or 0), 2),
            "jood_orders": j["orders"],
            "jood_amount": j["amount"],
            "claim_ratio": round(cp / j["orders"], 2) if j["orders"] else None,
            "cost_per_jood_donation": (round(float(c.get("spend") or 0) / j["orders"], 2)
                                       if j["orders"] else None),
            "jood_roas": (round(j["amount"] / float(c["spend"]), 2)
                          if c.get("spend") else None),
        })
    total_orders = sum(v["orders"] for v in jood["by_platform"].values())
    total_amount = round(sum(v["amount"] for v in jood["by_platform"].values()), 2)
    claimed_total = sum(r["claimed_purchases"] for r in rows if r["platform"] != "unattributed")
    imported = db.scalar(select(func.count()).select_from(DonationOrder).where(
        DonationOrder.user_id == user_id, DonationOrder.date >= date_from,
        DonationOrder.date <= date_to))
    warnings = list(jood["warnings"]) + list(claims.get("warnings") or [])
    if not imported:
        warnings.append("no Jood orders imported for this range: import the export first")
    return {
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "currency": jood["currency"],
        "rows": rows,
        "totals": {
            "jood_orders": total_orders,
            "jood_amount": total_amount,
            "jood_unattributed_orders": jood["by_platform"].get("unattributed", {}).get(
                "orders", 0),
            "claimed_purchases_all_platforms": claimed_total,
            "overclaim": (round(claimed_total / total_orders, 2) if total_orders else None),
        },
        "warnings": warnings,
    }
