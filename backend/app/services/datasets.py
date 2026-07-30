"""One place that knows how to run a query against any of the three metric
datasets. Both exports and saved reports go through here, so a report and a
download of the same query can never disagree.
"""
from datetime import date

from sqlalchemy.orm import Session

from app.services.metrics import query_metrics
from app.services.social_metrics import query_social_metrics
from app.services.web_metrics import query_web_metrics

DATASETS = ("ads", "web", "social")


def run_dataset_query(
    db: Session,
    dataset: str,
    date_from: date,
    date_to: date,
    breakdown: str = "none",
    platforms: list[str] | None = None,
    account_ids: list[int] | None = None,
    campaign_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    sort: str | None = None,
    limit: int = 100_000,
    user_id: int | None = None,
) -> list[dict]:
    """Run the query and return every row (up to `limit`), not a page."""
    if dataset not in DATASETS:
        raise ValueError(f"dataset must be one of {DATASETS}")

    if dataset == "ads":
        page = query_metrics(
            db,
            date_from=date_from,
            date_to=date_to,
            breakdown=breakdown,
            platforms=platforms,
            account_ids=account_ids,
            campaign_ids=campaign_ids,
            group_by=group_by,
            sort=sort,
            page=1,
            page_size=limit,
            user_id=user_id,
        )
    elif dataset == "web":
        page = query_web_metrics(
            db,
            date_from=date_from,
            date_to=date_to,
            account_ids=account_ids,
            group_by=group_by,
            sort=sort,
            page=1,
            page_size=limit,
            user_id=user_id,
        )
    else:
        page = query_social_metrics(
            db,
            date_from=date_from,
            date_to=date_to,
            account_ids=account_ids,
            group_by=group_by,
            sort=sort,
            page=1,
            page_size=limit,
            user_id=user_id,
        )
    return page["data"]
