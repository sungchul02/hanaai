"""앱 버전 전후 지연 회귀 탐지.

"1.1.0 배포 후 추론 응답이 2.5배 느려졌다" 를 잡는다.

이 탐지기가 event_rollup_hourly 에 app_version 차원을 넣은 이유다. kiosk.app_version 은
현재 값이라, 배포 이후 과거 이벤트까지 새 버전으로 보이게 만들어 비교 자체를 망친다.

같은 키오스크의 버전 전후를 비교하는 것이 아니라, **같은 구간에 공존하는 두 버전**을
비교한다. 플릿이 한꺼번에 올라가지 않기 때문에(카나리) 이 비교가 더 공정하다.
시간대·요일 효과가 양쪽에 똑같이 걸린다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.contracts.proposal import Candidate, Evidence
from agents.detectors.base import Window

_SQL = text(
    """
    WITH per_version AS (
        SELECT r.event_type_id,
               r.app_version,
               sum(r.event_count)                    AS n,
               count(DISTINCT r.kiosk_id)            AS kiosks,
               -- p95 의 가중평균. 시간당 건수로 가중해야 한산한 시간대가 과대반영되지 않는다.
               sum(r.duration_p95_ms::numeric * r.event_count)
                   / NULLIF(sum(r.event_count), 0)   AS p95
        FROM event_rollup_hourly r
        WHERE r.bucket >= :start AND r.bucket < :end
          AND r.app_version <> ''
          AND r.duration_p95_ms IS NOT NULL
        GROUP BY r.event_type_id, r.app_version
    ),
    -- 같은 이벤트 타입에 버전이 둘 이상 있을 때만 비교가 성립한다
    comparable AS (
        SELECT event_type_id
        FROM per_version
        GROUP BY event_type_id
        HAVING count(DISTINCT app_version) >= 2
    )
    SELECT et.code                AS event_code,
           slow.app_version       AS slow_version,
           fast.app_version       AS fast_version,
           slow.p95               AS slow_p95,
           fast.p95               AS fast_p95,
           slow.n                 AS slow_n,
           fast.n                 AS fast_n,
           slow.kiosks            AS slow_kiosks,
           slow.p95 / NULLIF(fast.p95, 0) AS ratio
    FROM per_version slow
    JOIN per_version fast
      ON fast.event_type_id = slow.event_type_id
     AND fast.app_version <> slow.app_version
    JOIN comparable c  ON c.event_type_id = slow.event_type_id
    JOIN event_type et ON et.event_type_id = slow.event_type_id
    WHERE slow.n >= :min_events
      AND fast.n >= :min_events
      AND slow.p95 > fast.p95
      AND slow.p95 >= :min_p95_ms
      AND slow.p95 / NULLIF(fast.p95, 0) >= :min_ratio
    ORDER BY ratio DESC
    LIMIT :limit
    """
)


class LatencyRegressionDetector:
    id = "latency_regression"
    description = "앱 버전에 따라 응답 지연이 유의하게 나빠진 동작"

    def __init__(
        self,
        min_events: int = 100,
        min_p95_ms: int = 300,
        min_ratio: float = 1.5,
        limit: int = 10,
    ) -> None:
        self.min_events = min_events
        self.min_p95_ms = min_p95_ms
        self.min_ratio = min_ratio
        self.limit = limit

    def run(self, session: Session, window: Window) -> list[Candidate]:
        rows = session.execute(
            _SQL,
            {
                "start": window.start,
                "end": window.end,
                "min_events": self.min_events,
                "min_p95_ms": self.min_p95_ms,
                "min_ratio": self.min_ratio,
                "limit": self.limit,
            },
        ).mappings()

        candidates: list[Candidate] = []
        for r in rows:
            ratio = float(r["ratio"])
            candidates.append(
                Candidate(
                    detector_id=self.id,
                    signal=(
                        f"{r['event_code']} p95 지연이 {r['fast_version']} 대비 "
                        f"{r['slow_version']} 에서 {ratio:.1f}배 "
                        f"({float(r['fast_p95']):.0f}ms → {float(r['slow_p95']):.0f}ms)"
                    ),
                    dedupe_key=(
                        f"{self.id}:{r['event_code']}:{r['slow_version']}:{r['fast_version']}"
                    ),
                    evidence=Evidence(
                        metric=f"{r['event_code']} p95 latency ms ({r['slow_version']})",
                        query_id=self.id,
                        observed=round(float(r["slow_p95"]), 1),
                        baseline=round(float(r["fast_p95"]), 1),
                        sample_size=int(r["slow_n"]),
                        window=window.label,
                        affected_kiosks=int(r["slow_kiosks"]),
                    ),
                    context={
                        "event_code": r["event_code"],
                        "slow_version": r["slow_version"],
                        "fast_version": r["fast_version"],
                        "ratio": round(ratio, 2),
                        "comparison": "동일 구간에 공존한 두 버전 비교",
                    },
                )
            )
        return candidates
