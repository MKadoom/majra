from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


SCHEMAS = ("app", "raw", "norm")

# Derived metrics (ctr/cpc/cpm/roas) are computed after aggregation in the
# metrics service, never stored (SPEC §3). The view only joins names in.
VIEW_SQL = """
CREATE OR REPLACE VIEW norm.v_ad_performance AS
SELECT
    f.id, f.date, f.platform, f.breakdown,
    f.account_id, a.name AS account_name, a.currency AS account_currency,
    f.campaign_id, c.name AS campaign_name,
    f.adset_id, s.name AS adset_name,
    f.ad_id, ad.name AS ad_name,
    f.country, f.device, f.age, f.gender, f.placement,
    f.spend, f.currency, f.impressions, f.clicks,
    f.conversions, f.conversion_value, f.video_views, f.leads, f.purchases
FROM norm.fact_ad_performance f
JOIN norm.accounts a   ON a.id  = f.account_id
LEFT JOIN norm.campaigns c ON c.id = f.campaign_id
LEFT JOIN norm.adsets s    ON s.id = f.adset_id
LEFT JOIN norm.ads ad      ON ad.id = f.ad_id
"""


def init_db(bind) -> None:
    """Create schemas, all tables, and the reporting view.

    Used by the initial Alembic migration and by the test suite.
    """
    import app.models  # noqa: F401  (register all models on Base)

    with bind.begin() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    Base.metadata.create_all(bind)
    with bind.begin() as conn:
        conn.execute(text(VIEW_SQL))
