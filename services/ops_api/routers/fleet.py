from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.ops_api.schemas import FleetSummary, PeripheralHealthRow

router = APIRouter(prefix="/v1/fleet", tags=["fleet"])
DbSession = Annotated[Session, Depends(get_session)]

# 이 쿼리가 peripheral_model 과 peripheral 을 분리한 설계의 이유다.
# 개체 단위로만 저장했다면 "이 모델이 전 지점에서 더 고장나는가"를 물을 수 없다.
#
# installed 와 usage_stats 를 각각 집계한 뒤 조인한다. 한 번에 조인하면
# (개체 수 x 집계 행 수) 로 행이 곱해져 error_count 가 부풀려진다.
_PERIPHERAL_HEALTH_SQL = text(
    """
    WITH installed AS (
        SELECT peripheral_model_id, count(*) AS installed_count
        FROM peripheral
        WHERE detached_at IS NULL
        GROUP BY peripheral_model_id
    ),
    usage_stats AS (
        SELECT peripheral_model_id,
               sum(event_count) AS event_count,
               sum(error_count) AS error_count
        FROM event_rollup_hourly
        WHERE bucket >= :since
          AND peripheral_model_id <> 0
        GROUP BY peripheral_model_id
    )
    SELECT pm.peripheral_model_id,
           pm.kind::text            AS kind,
           pm.vendor,
           pm.model_name,
           pm.driver_version,
           coalesce(i.installed_count, 0) AS installed_count,
           coalesce(u.event_count, 0)     AS event_count,
           coalesce(u.error_count, 0)     AS error_count,
           CASE WHEN coalesce(u.event_count, 0) = 0 THEN NULL
                ELSE round(u.error_count::numeric / u.event_count, 4)
           END AS error_rate
    FROM peripheral_model pm
    LEFT JOIN installed   i ON i.peripheral_model_id = pm.peripheral_model_id
    LEFT JOIN usage_stats u ON u.peripheral_model_id = pm.peripheral_model_id
    WHERE coalesce(i.installed_count, 0) > 0 OR coalesce(u.event_count, 0) > 0
    ORDER BY error_rate DESC NULLS LAST, event_count DESC
    LIMIT :limit
    """
)


@router.get("/peripheral-health", response_model=list[PeripheralHealthRow])
def peripheral_health(
    session: DbSession,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=100, le=500),
) -> list[PeripheralHealthRow]:
    """장치 모델별 오류율. 분석 Agent 가 보는 것과 같은 집계를 사람도 본다."""
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    rows = session.execute(_PERIPHERAL_HEALTH_SQL, {"since": since, "limit": limit}).mappings()
    return [
        PeripheralHealthRow(
            peripheral_model_id=r["peripheral_model_id"],
            kind=r["kind"],
            vendor=r["vendor"],
            model_name=r["model_name"],
            driver_version=r["driver_version"],
            installed_count=r["installed_count"],
            event_count=r["event_count"],
            error_count=r["error_count"],
            error_rate=float(r["error_rate"]) if r["error_rate"] is not None else None,
        )
        for r in rows
    ]


# 대시보드 카드용. 화면 한 장에 필요한 수치를 왕복 한 번에 모은다.
# 이벤트 카운트는 occurred_at 조건이 있어 파티션 프루닝이 걸린다.
_SUMMARY_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM customer)                       AS customers,
      (SELECT count(*) FROM site)                           AS sites,
      (SELECT count(*) FROM kiosk)                          AS kiosks,
      (SELECT count(*) FROM kiosk
        WHERE last_seen_at IS NULL
           OR last_seen_at < now() - (:stale_minutes || ' minutes')::interval)
                                                            AS kiosks_stale,
      (SELECT count(*) FROM event
        WHERE occurred_at >= now() - interval '24 hours')    AS events_24h,
      (SELECT count(*) FROM event
        WHERE occurred_at >= now() - interval '24 hours'
          AND severity >= 40)                                AS errors_24h,
      (SELECT count(*) FROM event_quarantine
        WHERE received_at >= now() - interval '24 hours')     AS quarantined_24h,
      (SELECT count(*) FROM feature_proposal
        WHERE status IN ('draft', 'pending_review'))          AS proposals_pending,
      (SELECT count(*) FROM feature_proposal
        WHERE status = 'approved')                            AS proposals_approved
    """
)


@router.get("/summary", response_model=FleetSummary)
def fleet_summary(session: DbSession, stale_minutes: int = Query(default=60, ge=1)) -> FleetSummary:
    row = session.execute(_SUMMARY_SQL, {"stale_minutes": stale_minutes}).mappings().one()
    return FleetSummary(**dict(row))
