#!/usr/bin/env python3
"""Seed a demo user and synthetic data.

Point of this script: you can see the whole dashboard working — blended spend,
charts, channel strips, exports — BEFORE registering a single developer app.
Nothing here touches a real platform; it writes plausible rows straight into the
warehouse.

    python scripts/seed_demo.py                 # demo@majra.dev / demo-password
    python scripts/seed_demo.py --days 90 --reset

The data is deliberately imperfect: TikTok has a two-day outage so you can see
what a broken tributary looks like in the channel strips.
"""
import argparse
import math
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import encrypt_tokens, hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    Ad,
    Adset,
    Campaign,
    Connection,
    FactAdPerformance,
    FactSocialPerformance,
    FactWebPerformance,
    SyncJob,
    User,
)

EMAIL = "demo@majra.dev"
PASSWORD = "demo-password"

# Each ad platform gets its own character: a daily budget, a click-through rate,
# a conversion rate and an average order value. The numbers below are ordinary
# for a small Gulf e-commerce advertiser, so the dashboard reads as plausible.
PLATFORMS = {
    "meta":       {"budget": 420, "ctr": 0.014, "cvr": 0.031, "aov": 240},
    "google_ads": {"budget": 310, "ctr": 0.048, "cvr": 0.044, "aov": 265},
    "tiktok":     {"budget": 180, "ctr": 0.009, "cvr": 0.017, "aov": 190},
    "snapchat":   {"budget": 120, "ctr": 0.007, "cvr": 0.012, "aov": 175},
}

CAMPAIGNS = {
    "meta": ["Always-On Prospecting", "Retargeting — 30d", "Eid Push"],
    "google_ads": ["Brand", "Shopping — All Products", "Generic Non-Brand"],
    "tiktok": ["Creator Spark", "Awareness Reach"],
    "snapchat": ["Story Ads — Youth"],
}

# TikTok goes dark for two days: this is what a broken connector looks like, and
# the channel strip on the Connections page will show the gap.
OUTAGE_PLATFORM = "tiktok"
OUTAGE_DAYS = (6, 7)  # days back from yesterday


def seasonality(d: date) -> float:
    """Weekends run hotter; a gentle wave over the month keeps it from looking
    machine-generated."""
    weekend = 1.25 if d.weekday() in (4, 5) else 1.0  # Fri/Sat in the Gulf
    wave = 1 + 0.18 * math.sin(d.toordinal() / 9.0)
    return weekend * wave


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="days of history")
    ap.add_argument("--reset", action="store_true", help="wipe demo data first")
    args = ap.parse_args()

    rng = random.Random(7)  # deterministic: the same demo every time
    db = SessionLocal()
    today = date.today()
    yesterday = today - timedelta(days=1)

    user = db.scalar(select(User).where(User.email == EMAIL))
    if user is None:
        user = User(email=EMAIL, password_hash=hash_password(PASSWORD))
        db.add(user)
        db.commit()
        print(f"created user {EMAIL}")

    if args.reset:
        accounts = db.scalars(
            select(Account).join(Connection).where(Connection.user_id == user.id)
        ).all()
        ids = [a.id for a in accounts]
        if ids:
            for model in (FactAdPerformance, FactWebPerformance, FactSocialPerformance):
                db.execute(delete(model).where(model.account_id.in_(ids)))
            db.execute(delete(Ad).where(Ad.adset_id.in_(
                select(Adset.id).where(Adset.campaign_id.in_(
                    select(Campaign.id).where(Campaign.account_id.in_(ids)))))))
            db.execute(delete(Adset).where(Adset.campaign_id.in_(
                select(Campaign.id).where(Campaign.account_id.in_(ids)))))
            db.execute(delete(Campaign).where(Campaign.account_id.in_(ids)))
            db.execute(delete(Account).where(Account.id.in_(ids)))
        db.execute(delete(SyncJob).where(SyncJob.connection_id.in_(
            select(Connection.id).where(Connection.user_id == user.id))))
        db.execute(delete(Connection).where(Connection.user_id == user.id))
        db.commit()
        print("reset demo data")

    facts: list[dict] = []

    # ---------------------------------------------------------- ad platforms
    for platform, profile in PLATFORMS.items():
        conn = Connection(
            user_id=user.id, platform=platform, name=f"{platform} (demo)",
            encrypted_tokens=encrypt_tokens({"access_token": "demo"}),
            status="active",
        )
        db.add(conn)
        db.flush()

        acc = Account(
            connection_id=conn.id, platform=platform,
            external_id=f"demo-{platform}", name=f"Demo {platform.replace('_', ' ').title()}",
            currency="SAR", timezone="Asia/Riyadh",
        )
        db.add(acc)
        db.flush()

        ad_ids: list[int] = []
        for cname in CAMPAIGNS[platform]:
            camp = Campaign(account_id=acc.id, external_id=f"c-{rng.randrange(10**6)}",
                            name=cname, status="ACTIVE", objective="CONVERSIONS")
            db.add(camp)
            db.flush()
            for seg in ("Broad", "Lookalike 3%"):
                aset = Adset(campaign_id=camp.id, external_id=f"s-{rng.randrange(10**6)}",
                             name=f"{cname} · {seg}", status="ACTIVE")
                db.add(aset)
                db.flush()
                for creative in ("Video 15s", "Carousel"):
                    ad = Ad(adset_id=aset.id, external_id=f"a-{rng.randrange(10**6)}",
                            name=f"{seg} — {creative}", status="ACTIVE")
                    db.add(ad)
                    db.flush()
                    ad_ids.append(ad.id)
        db.flush()

        campaign_of = {}
        adset_of = {}
        for ad in db.scalars(select(Ad).where(Ad.id.in_(ad_ids))):
            aset = db.get(Adset, ad.adset_id)
            adset_of[ad.id] = aset.id
            campaign_of[ad.id] = aset.campaign_id

        for back in range(args.days, 0, -1):
            d = yesterday - timedelta(days=back - 1)
            if platform == OUTAGE_PLATFORM and back in OUTAGE_DAYS:
                continue  # the deliberate gap
            day_budget = profile["budget"] * seasonality(d) * rng.uniform(0.85, 1.15)
            share = [rng.uniform(0.6, 1.4) for _ in ad_ids]
            total = sum(share)
            for ad_id, w in zip(ad_ids, share, strict=True):
                spend = day_budget * (w / total)
                if spend < 0.5:
                    continue
                cpc = rng.uniform(0.9, 2.4)
                clicks = max(1, int(spend / cpc))
                impressions = int(clicks / max(profile["ctr"] * rng.uniform(0.8, 1.2), 1e-4))
                conversions = round(clicks * profile["cvr"] * rng.uniform(0.6, 1.5), 2)
                value = round(conversions * profile["aov"] * rng.uniform(0.85, 1.2), 2)
                facts.append(dict(
                    date=d, platform=platform, account_id=acc.id,
                    campaign_id=campaign_of[ad_id], adset_id=adset_of[ad_id], ad_id=ad_id,
                    breakdown="none", spend=round(spend, 2), currency="SAR",
                    impressions=impressions, clicks=clicks,
                    conversions=conversions, conversion_value=value,
                    video_views=int(impressions * rng.uniform(0.1, 0.3)),
                    leads=0, purchases=int(conversions),
                ))

        db.add(SyncJob(
            connection_id=conn.id, kind="scheduled",
            date_from=yesterday - timedelta(days=7), date_to=yesterday,
            breakdown="none", status="success",
            stats={"accounts": 1, "rows_fetched": 0, "rows_deleted": 0,
                   "rows_inserted": 0},
            started_at=datetime.now(UTC) - timedelta(minutes=4),
            finished_at=datetime.now(UTC) - timedelta(minutes=3),
        ))

    db.bulk_insert_mappings(FactAdPerformance, facts)
    print(f"wrote {len(facts):,} ad rows across {len(PLATFORMS)} platforms")

    # -------------------------------------------------------------- GA4 (web)
    conn = Connection(user_id=user.id, platform="ga4", name="ga4 (demo)",
                      encrypted_tokens=encrypt_tokens({"access_token": "demo"}))
    db.add(conn)
    db.flush()
    ga_acc = Account(connection_id=conn.id, platform="ga4", external_id="demo-ga4",
                     name="Demo property", currency="SAR", timezone="Asia/Riyadh")
    db.add(ga_acc)
    db.flush()

    web = []
    for back in range(args.days, 0, -1):
        d = yesterday - timedelta(days=back - 1)
        base = 900 * seasonality(d)
        for source, medium, share in (
            ("google", "organic", 0.34), ("google", "cpc", 0.22),
            ("facebook", "paid_social", 0.18), ("(direct)", "(none)", 0.16),
            ("instagram", "referral", 0.10),
        ):
            sessions = int(base * share * rng.uniform(0.85, 1.15))
            conversions = round(sessions * rng.uniform(0.012, 0.035), 2)
            web.append(dict(
                date=d, account_id=ga_acc.id, source=source, medium=medium,
                campaign_name="(not set)" if medium in ("organic", "(none)") else "always-on",
                sessions=sessions, users=int(sessions * rng.uniform(0.72, 0.88)),
                conversions=conversions,
                revenue=round(conversions * rng.uniform(180, 300), 2),
            ))
    db.bulk_insert_mappings(FactWebPerformance, web)
    print(f"wrote {len(web):,} web rows")

    # ------------------------------------------------- Facebook / Instagram
    conn = Connection(user_id=user.id, platform="meta_social", name="social (demo)",
                      encrypted_tokens=encrypt_tokens({"access_token": "demo"}))
    db.add(conn)
    db.flush()
    pg = Account(connection_id=conn.id, platform="meta_social",
                 external_id="demo-page", name="Demo Page", timezone="Asia/Riyadh")
    db.add(pg)
    db.flush()

    social = []
    followers_page, followers_ig = 12_400, 8_300
    for back in range(args.days, 0, -1):
        d = yesterday - timedelta(days=back - 1)
        followers_page += rng.randint(-4, 26)
        followers_ig += rng.randint(-2, 34)
        imp = int(5200 * seasonality(d) * rng.uniform(0.8, 1.2))
        social.append(dict(date=d, account_id=pg.id, grain="page",
                           external_ref="demo-page", impressions=imp,
                           reach=int(imp * rng.uniform(0.6, 0.8)),
                           engagement=int(imp * rng.uniform(0.02, 0.05)),
                           followers=followers_page))
        imp_ig = int(3900 * seasonality(d) * rng.uniform(0.8, 1.2))
        social.append(dict(date=d, account_id=pg.id, grain="ig_user",
                           external_ref="demo-ig", impressions=imp_ig,
                           reach=int(imp_ig * rng.uniform(0.55, 0.75)),
                           engagement=int(imp_ig * rng.uniform(0.03, 0.07)),
                           followers=followers_ig))
    db.bulk_insert_mappings(FactSocialPerformance, social)
    print(f"wrote {len(social):,} social rows")

    db.commit()
    db.close()

    print()
    print("Demo data ready. Open the dashboard and sign in:")
    print(f"  email:    {EMAIL}")
    print(f"  password: {PASSWORD}")
    print()
    print(f"TikTok is missing {len(OUTAGE_DAYS)} days on purpose — look at the")
    print("channel strips on the Connections page to see what a broken")
    print("connector looks like.")


if __name__ == "__main__":
    main()
