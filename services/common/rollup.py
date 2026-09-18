"""이벤트 → 시간 단위 집계.

분석 Agent 는 event 를 직접 읽지 않고 event_rollup_hourly 만 본다.
원본을 읽게 두면 분석 한 번에 수백만 행을 훑게 되고, 그 순간 하루 한 번도 못 돌린다.

재실행 안전(idempotent)하다. 같은 구간을 여러 번 돌려도 결과가 같다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.orm import Session

ERROR_SEVERITY = 40

_ROLLUP_SQL = text(
    """
    INSERT INTO event_rollup_hourly (
        bucket, kiosk_id, event_type_id, peripheral_model_id, app_version,
        event_count, error_count, duration_p50_ms, duration_p95_ms, distinct_sessions
    )
    SELECT date_trunc('hour', e.occurred_at)                      AS bucket,
           e.kiosk_id,
           e.event_type_id,
           coalesce(p.peripheral_model_id, 0)                     AS peripheral_model_id,
           coalesce(e.app_version, '')                            AS app_version,
           count(*)                                               AS event_count,
           count(*) FILTER (WHERE e.severity >= :error_severity)  AS error_count,
           percentile_disc(0.5)  WITHIN GROUP (ORDER BY e.duration_ms)::int,
           percentile_disc(0.95) WITHIN GROUP (ORDER BY e.duration_ms)::int,
           count(DISTINCT e.session_id)                           AS distinct_sessions
    FROM event e
    LEFT JOIN peripheral p ON p.peripheral_id = e.peripheral_id
    WHERE e.occurred_at >= :start AND e.occurred_at < :end
    GROUP BY 1, 2, 3, 4, 5
    ON CONFLICT (bucket, kiosk_id, event_type_id, peripheral_model_id, app_version)
    DO UPDATE SET
        event_count       = EXCLUDED.event_count,
        error_count       = EXCLUDED.error_count,
        duration_p50_ms   = EXCLUDED.duration_p50_ms,
        duration_p95_ms   = EXCLUDED.duration_p95_ms,
        distinct_sessions = EXCLUDED.distinct_sessions
    """
)


def rollup_range(session: Session, start: dt.datetime, end: dt.datetime) -> int:
    """[start, end) 구간을 집계한다. 갱신된 행 수를 돌려준다."""
    # session.execute 의 선언 타입은 Result 지만 DML 의 실제 반환은 CursorResult 다.
    # rowcount 를 읽으려면 좁혀줘야 한다.
    result = cast(
        "CursorResult[Any]",
        session.execute(
            _ROLLUP_SQL, {"start": start, "end": end, "error_severity": ERROR_SEVERITY}
        ),
    )
    rowcount = result.rowcount
    session.commit()
    return int(rowcount)


def rollup_recent(session: Session, hours: int = 3, now: dt.datetime | None = None) -> int:
    """최근 N시간을 다시 집계한다.

    지난 시간만 보지 않고 몇 시간을 겹쳐 도는 이유: 오프라인이었던 키오스크가 과거 시각의
    이벤트를 뒤늦게 올린다. 겹치기가 없으면 그 데이터는 집계에 영원히 반영되지 않는다.
    """
    now = now or dt.datetime.now(dt.UTC)
    end = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return rollup_range(session, end - dt.timedelta(hours=hours + 1), end)
