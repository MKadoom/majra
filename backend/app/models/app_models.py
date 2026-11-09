from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    # Owner = may change instance-wide settings (FX rates). The first account on
    # an instance is the owner; later sign-ups are not.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(200))
    encrypted_tokens: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(30), default="active")  # active|needs_reauth|disabled
    # Optional allow-list of account external_ids this connection should sync.
    # NULL / empty = sync every account the token can see (default, backward
    # compatible). Used to focus a connection on the accounts that matter and
    # skip read-only / empty ones — which also conserves a platform's per-user
    # API rate budget (e.g. Meta Development-mode limits).
    synced_account_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Soft delete: when set, the connection is hidden from listings, its
    # schedules don't fire, and its facts drop out of metrics — but nothing is
    # destroyed, so it can be restored. Recoverable by design.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship()


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("app.connections.id"), index=True)
    cadence: Mapped[str] = mapped_column(String(20))  # hourly|every_3h|daily|weekly|manual
    hour_utc: Mapped[int] = mapped_column(Integer, default=3)
    breakdowns: Mapped[list] = mapped_column(JSONB, default=list)  # e.g. ["none","country"]
    restatement_days: Mapped[int] = mapped_column(Integer, default=7)
    backfill_days: Mapped[int] = mapped_column(Integer, default=365)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    connection: Mapped["Connection"] = relationship()


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("app.connections.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="manual")  # manual|scheduled|backfill
    date_from: Mapped[object] = mapped_column(Date)
    date_to: Mapped[object] = mapped_column(Date)
    breakdown: Mapped[str] = mapped_column(String(30), default="none")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    connection: Mapped["Connection"] = relationship()


class TrackingChange(Base):
    """A day the *meaning* of a platform's tracked metric changed (SPEC §5a):
    pixel routing, dedup or sender changes. Numbers before and after are not
    like for like, so /metrics warns when a range straddles `changed_on`.
    `platform` NULL means every platform."""

    __tablename__ = "tracking_changes"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(primary_key=True)
    changed_on: Mapped[object] = mapped_column(Date, index=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metric: Mapped[str] = mapped_column(String(50), default="purchases")
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class AccountSyncState(Base):
    """Per-account sync coverage: the contiguous date range this account has been
    synced through successfully, per breakdown. Unlike the fact-derived
    watermark, it advances even when an account legitimately had no activity,
    and it doesn't advance for an account that was skipped — so one healthy
    account can't hide another account's gap. `backfill_completed_at` is stamped
    once coverage reaches back to the schedule's backfill horizon."""

    __tablename__ = "account_sync_state"
    __table_args__ = {"schema": "app"}

    account_id: Mapped[int] = mapped_column(
        ForeignKey("norm.accounts.id", ondelete="CASCADE"), primary_key=True
    )
    breakdown: Mapped[str] = mapped_column(String(30), primary_key=True)
    synced_from: Mapped[object] = mapped_column(Date)
    synced_to: Mapped[object] = mapped_column(Date)
    backfill_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MetricCoverage(Base):
    """From which day a platform's metric is real (SPEC §5a). Before
    `valid_from` the stored value is missing/zero for a reason that isn't
    "nothing happened" (e.g. the field wasn't pulled yet), so /metrics warns."""

    __tablename__ = "metric_coverage"
    __table_args__ = {"schema": "app"}

    platform: Mapped[str] = mapped_column(String(50), primary_key=True)
    metric: Mapped[str] = mapped_column(String(50), primary_key=True)
    valid_from: Mapped[object] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReconciliationResult(Base):
    """One daily check (T7): a platform's own account-level total for a day
    vs. the sum of what majra stored for that account and day."""

    __tablename__ = "reconciliation_results"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("app.connections.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("norm.accounts.id", ondelete="CASCADE"))
    date: Mapped[object] = mapped_column(Date)
    metric: Mapped[str] = mapped_column(String(30))
    platform_value: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    stored_value: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # ok | mismatch | error
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class DonationOrder(Base):
    """A real donation/order from the Jood store, imported from its export.

    This is the ground truth the ad platforms' purchase claims are compared
    against: each platform reports the donations it *claims* (and several can
    claim the same one); Jood records each donation once. `platform` is the
    paid channel the order is attributed to (from an explicit column, the Jood
    per-platform ad link, or utm_source), or "unattributed". Re-importing the
    same export updates rows in place (unique per user + order id)."""

    __tablename__ = "donation_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "external_id", name="uq_donation_orders_user_ext"),
        Index("ix_donation_orders_user_date", "user_id", "date"),
        {"schema": "app"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"))
    source: Mapped[str] = mapped_column(String(30), default="jood")
    external_id: Mapped[str] = mapped_column(String(120))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date: Mapped[object] = mapped_column(Date)            # Asia/Riyadh calendar day
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="SAR")
    status: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True)
    platform: Mapped[str] = mapped_column(String(30), default="unattributed")
    utm_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SyncLog(Base):
    __tablename__ = "sync_logs"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sync_job_id: Mapped[int] = mapped_column(ForeignKey("app.sync_jobs.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(10), default="info")
    event: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class ApiKey(Base):
    """A read-only key for pulling data from scripts. The secret is shown once
    at creation and never stored — only its SHA-256 hash. `prefix` is the
    non-secret head of the key, used to look the row up before hashing."""

    __tablename__ = "api_keys"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list] = mapped_column(JSONB, default=lambda: ["read"])
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship()


class SavedReport(Base):
    """A metrics query that runs on a schedule and delivers itself somewhere.

    `params` holds the query (dataset, group_by, filters, and a RELATIVE window —
    `last_days` — because a saved report runs repeatedly and a fixed date range
    would go stale). `destination` holds the delivery config:
      {"kind": "google_sheets", "spreadsheet_id": "...", "sheet_name": "majra",
       "service_account": {...}}
      {"kind": "webhook", "url": "https://…", "secret": "…"}   (HMAC-SHA256 signed)
      {"kind": "email", "to": "you@example.com"}               (SMTP from env)
    """

    __tablename__ = "saved_reports"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    dataset: Mapped[str] = mapped_column(String(20), default="ads")  # ads|web|social
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    destination: Mapped[dict] = mapped_column(JSONB, default=dict)
    cadence: Mapped[str] = mapped_column(String(20), default="daily")  # daily|weekly
    hour_utc: Mapped[int] = mapped_column(Integer, default=6)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship()


class CardCharge(Base):
    """A top-up you put on the card that funds ad spend. Summed against ad spend
    to show the remaining prepaid balance, so the card never runs dry."""

    __tablename__ = "card_charges"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"), index=True)
    date: Mapped[object] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship()


class SavedView(Base):
    """A named Explore configuration (dataset, platform filter, group-by, date
    window, reporting currency) the user can reopen. `config` is the opaque
    dashboard state blob — the backend just persists and returns it."""

    __tablename__ = "saved_views"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship()


class Budget(Base):
    """A monthly spend budget for a scope (all platforms, one platform, or one
    account). Pacing compares month-to-date spend — blended into the budget's
    currency — against `amount_monthly` and the fraction of the month elapsed."""

    __tablename__ = "budgets"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app.users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)  # null = all
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("norm.accounts.id"), nullable=True
    )
    amount_monthly: Mapped[float] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship()


class FxRate(Base):
    """Exchange rate used to blend spend/value across account currencies into a
    single reporting currency. `rate_to_usd` is the USD value of one unit of
    `currency` (e.g. SAR ≈ 0.2667). Converting an amount from currency A to
    reporting currency R is amount * rate_to_usd[A] / rate_to_usd[R]. Rates are
    owner-supplied (a v2 auto-fetch could fill them); USD is implicitly 1.0."""

    __tablename__ = "fx_rates"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), unique=True, index=True)  # ISO 4217
    rate_to_usd: Mapped[float] = mapped_column(Numeric(18, 8))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
