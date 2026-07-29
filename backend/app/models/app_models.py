from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
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
