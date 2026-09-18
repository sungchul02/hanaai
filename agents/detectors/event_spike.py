"""이벤트 급증 탐지.

"어제까지 하루 20건이던 rag.no_result 가 오늘 180건" 같은 것을 잡는다.
가장 단순하지만 가장 많이 쓰이는 탐지기다. 원인은 모르지만 '뭔가 바뀌었다' 는 확실하다.

비교 방식: 최근 구간(recent_hours)의 시간당 평균을, 그 앞 기준 구간의 시간당 평균·표준편차와
비교해 z-score 를 낸다. 평균만 보면 원래 들쭉날쭉한 이벤트가 매번 걸린다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.contracts.proposal import Candidate, Evidence
from agents.detectors.base import Window

_SQL = text(
    """
    WITH hourly AS (
        SELECT r.event_type_id,
               r.bucket,
               sum(r.event_count)       AS n,
               count(DISTINCT r.kiosk_id) AS kiosks
        FROM event_rollup_hourly r
        JOIN event_type et ON et.event_type_id = r.event_type_id
        WHERE r.bucket >= :start AND r.bucket < :end
          AND et.is_actionable
        GROUP BY r.event_type_id, r.bucket
    ),
    split AS (
        SELECT event_type_id,
               bucket >= :recent_from AS is_recent,
               n, kiosks
        FROM hourly
    ),
    base AS (
        SELECT event_type_id,
               avg(n)                         AS mean,
               coalesce(stddev_samp(n), 0)    AS sd,
               count(*)                       AS hours
        FROM split WHERE NOT is_recent
        GROUP BY event_type_id
    ),
    recent AS (
        SELECT event_type_id,
               avg(n)                 AS mean,
               sum(n)                 AS total,
               max(kiosks)            AS kiosks,
               count(*)               AS hours
        FROM split WHERE is_recent
        GROUP BY event_type_id
    )
    SELECT et.code                                   AS event_code,
           et.category,
           r.mean                                    AS recent_mean,
           b.mean                                    AS baseline_mean,
           b.sd                                      AS baseline_sd,
           r.total                                   AS recent_total,
           r.kiosks,
           -- 표준편차가 0 이면(완전히 평탄했으면) 평균의 10퍼센트를 최소 분산으로 둔다.
           -- 그러지 않으면 1건만 늘어도 z 가 무한대가 된다.
           (r.mean - b.mean) / GREATEST(b.sd, b.mean * 0.1, 0.5) AS z
    FROM recent r
    JOIN base b       ON b.event_type_id = r.event_type_id
    JOIN event_type et ON et.event_type_id = r.event_type_id
    WHERE b.hours >= :min_baseline_hours
      AND r.total >= :min_events
      AND r.mean > b.mean * :min_ratio
      AND (r.mean - b.mean) / GREATEST(b.sd, b.mean * 0.1, 0.5) >= :min_z
    ORDER BY z DESC
    LIMIT :limit
    """
)


class EventSpikeDetector:
    id = "event_type_spike"
    description = "최근 구간에 특정 이벤트가 기준 대비 급증"

    def __init__(
        self,
        recent_hours: int = 24,
        min_baseline_hours: int = 48,
        min_events: int = 50,
        min_ratio: float = 2.0,
        min_z: float = 3.0,
        limit: int = 10,
    ) -> None:
        self.recent_hours = recent_hours
        self.min_baseline_hours = min_baseline_hours
        self.min_events = min_events
        self.min_ratio = min_ratio
        self.min_z = min_z
        self.limit = limit

    def run(self, session: Session, window: Window) -> list[Candidate]:
        import datetime as dt

        recent_from = window.end - dt.timedelta(hours=self.recent_hours)
        rows = session.execute(
            _SQL,
            {
                "start": window.start,
                "end": window.end,
                "recent_from": recent_from,
                "min_baseline_hours": self.min_baseline_hours,
                "min_events": self.min_events,
                "min_ratio": self.min_ratio,
                "min_z": self.min_z,
                "limit": self.limit,
            },
        ).mappings()

        candidates: list[Candidate] = []
        for r in rows:
            ratio = float(r["recent_mean"]) / max(float(r["baseline_mean"]), 0.001)
            candidates.append(
                Candidate(
                    detector_id=self.id,
                    signal=(
                        f"{r['event_code']} 가 최근 {self.recent_hours}시간 동안 "
                        f"기준 대비 {ratio:.1f}배 (z={float(r['z']):.1f})"
                    ),
                    dedupe_key=f"{self.id}:{r['event_code']}",
                    evidence=Evidence(
                        metric=f"{r['event_code']} hourly count",
                        query_id=self.id,
                        observed=round(float(r["recent_mean"]), 3),
                        baseline=round(float(r["baseline_mean"]), 3),
                        sample_size=int(r["recent_total"]),
                        window=window.label,
                        affected_kiosks=int(r["kiosks"]),
                    ),
                    context={
                        "event_code": r["event_code"],
                        "category": r["category"],
                        "z_score": round(float(r["z"]), 2),
                        "recent_hours": self.recent_hours,
                        "baseline_sd": round(float(r["baseline_sd"]), 3),
                    },
                )
            )
        return candidates
