from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import MetricPoint, MetricsSeries, TrafficSummary
from app.services import lightsail as ls
from app.services.collector import sync_user_traffic
from app.services.traffic import NoCredentialsError, build_traffic_summary, get_user_aws_keys_and_cred

router = APIRouter(tags=["metrics"])


def _keys(user: User, credential_id: int | None = None):
    try:
        return get_user_aws_keys_and_cred(user, credential_id)
    except NoCredentialsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/traffic/summary", response_model=TrafficSummary)
def traffic_summary(
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TrafficSummary:
    data = build_traffic_summary(db, user, credential_id)
    return TrafficSummary(**data)


@router.post("/traffic/sync")
async def traffic_sync(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        count = await asyncio.to_thread(sync_user_traffic, db, user)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": f"同步完成，更新 {count} 条记录", "count": count}


@router.get("/metrics/{region}/{name}", response_model=MetricsSeries)
async def metrics(
    region: str,
    name: str,
    period: str = Query("day", pattern="^(day|week|month)$"),
    credential_id: int | None = Query(None),
    user: User = Depends(get_current_user),
) -> MetricsSeries:
    ak, sk, _cred = _keys(user, credential_id)
    now = datetime.now(timezone.utc)
    if period == "day":
        start = now - timedelta(hours=24)
        p = 3600
    elif period == "week":
        start = now - timedelta(days=7)
        p = 3600 * 6
    else:
        start = now - timedelta(days=30)
        p = 86400
    try:
        points = await asyncio.to_thread(
            ls.get_metric_series, ak, sk, region, name, start, now, p
        )
    except ls.LightsailError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    return MetricsSeries(
        region=region,
        name=name,
        period=period,
        points=[MetricPoint(**pt) for pt in points],
    )
