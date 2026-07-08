from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_accounts_platform_ext"),
        {"schema": "norm"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("app.connections.id"), index=True)
    platform: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(300))
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("account_id", "external_id", name="uq_campaigns_acct_ext"),
        {"schema": "norm"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("norm.accounts.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(400))
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    objective: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Adset(Base):
    __tablename__ = "adsets"
    __table_args__ = (
        UniqueConstraint("campaign_id", "external_id", name="uq_adsets_camp_ext"),
        {"schema": "norm"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("norm.campaigns.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(400))
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Ad(Base):
    __tablename__ = "ads"
    __table_args__ = (
        UniqueConstraint("adset_id", "external_id", name="uq_ads_adset_ext"),
        {"schema": "norm"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    adset_id: Mapped[int] = mapped_column(ForeignKey("norm.adsets.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(400))
    status: Mapped[str | None] = mapped_column(String(40), nullable=True)


class FactAdPerformance(Base):
    """Daily paid-media fact. Rows from different breakdown sets MUST NOT be
    summed together (SPEC §3) — queries filter on `breakdown`, default 'none'.
    Idempotency comes from delete+insert per (connection, breakdown, date range),
    so no unique constraint is needed here. Month partitioning: v2 migration.
    """

    __tablename__ = "fact_ad_performance"
    __table_args__ = (
        Index("ix_fact_date_acct_breakdown", "date", "account_id", "breakdown"),
        Index("ix_fact_campaign_date", "campaign_id", "date"),
        {"schema": "norm"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date: Mapped[object] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("norm.accounts.id"), nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("norm.campaigns.id"), nullable=True)
    adset_id: Mapped[int | None] = mapped_column(ForeignKey("norm.adsets.id"), nullable=True)
    ad_id: Mapped[int | None] = mapped_column(ForeignKey("norm.ads.id"), nullable=True)

    breakdown: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    device: Mapped[str | None] = mapped_column(String(80), nullable=True)
    age: Mapped[str | None] = mapped_column(String(30), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(30), nullable=True)
    placement: Mapped[str | None] = mapped_column(String(120), nullable=True)

    spend: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    conversions: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    conversion_value: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    video_views: Mapped[int] = mapped_column(BigInteger, default=0)
    leads: Mapped[int] = mapped_column(BigInteger, default=0)
    purchases: Mapped[int] = mapped_column(BigInteger, default=0)


class RawMetaInsights(Base):
    """Append-only raw layer (SPEC §3): re-normalization source of truth."""

    __tablename__ = "meta_insights"
    __table_args__ = {"schema": "raw"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connection_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_external_id: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RawGoogleInsights(Base):
    """Append-only raw layer for Google Ads (SPEC §3). Same shape as the Meta
    raw table; kept as a separate table per the one-table-per-platform rule."""

    __tablename__ = "google_insights"
    __table_args__ = {"schema": "raw"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connection_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_external_id: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FactWebPerformance(Base):
    """Daily web-analytics fact (GA4). Dimensioned by traffic source/medium/
    campaign name (strings, not joined entities — GA4 has no ad hierarchy).
    Idempotency via delete+insert scoped to (account, date range); no breakdown
    column (the source/medium/campaign grain is fixed, not an optional split)."""

    __tablename__ = "fact_web_performance"
    __table_args__ = (
        Index("ix_web_date_account", "date", "account_id"),
        {"schema": "norm"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date: Mapped[object] = mapped_column(Date, nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("norm.accounts.id"), nullable=False)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    campaign_name: Mapped[str | None] = mapped_column(String(400), nullable=True)
    sessions: Mapped[int] = mapped_column(BigInteger, default=0)
    users: Mapped[int] = mapped_column(BigInteger, default=0)
    conversions: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    # Revenue is in the GA4 property's currency (ISO mapping is a v2 concern).
    revenue: Mapped[float] = mapped_column(Numeric(18, 6), default=0)


class RawGA4Report(Base):
    """Append-only raw layer for GA4 runReport responses (SPEC §3)."""

    __tablename__ = "ga4_reports"
    __table_args__ = {"schema": "raw"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    connection_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_external_id: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
