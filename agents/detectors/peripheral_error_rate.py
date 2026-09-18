"""장치 모델별 장애 발생률 이상 탐지.

"이 모델의 프린터가 전 지점에서 유독 더 고장난다" 를 찾는다.

설계에서 틀리기 쉬운 지점이 둘 있다.

1. **분모를 무엇으로 두는가.**
   처음엔 같은 이벤트 타입 안에서 errors/events 로 계산했는데, 이러면 절대 발화하지
   않는다. printer.print_failed 처럼 심각도가 error 인 타입은 errors == events 라서
   모든 모델의 비율이 1.0 이 되고, 비교가 무의미해진다. 찾으려는 대상이 바로 그런
   하드웨어 장애 타입인데 말이다.
   그래서 분모를 **그 장치 모델의 전체 활동량**(모든 이벤트 수)으로 둔다.
   "이 장치가 일한 양 대비 얼마나 실패했는가" 가 실제로 묻고 싶은 것이다.

2. **비교 기준에서 자기 자신을 뺀다.**
   빼지 않으면 점유율 높은 모델은 사실상 자기 자신과 비교하게 되어 이상치로 안 잡힌다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.contracts.proposal import Candidate, Evidence
from agents.detectors.base import Window

_SQL = text(
    """
    -- 장치 모델의 전체 활동량. 이것이 분모가 된다.
    WITH model_activity AS (
        SELECT peripheral_model_id, sum(event_count) AS activity
        FROM event_rollup_hourly
        WHERE bucket >= :start AND bucket < :end
          AND peripheral_model_id <> 0
        GROUP BY peripheral_model_id
    ),
    per_model AS (
        SELECT peripheral_model_id,
               event_type_id,
               sum(event_count)          AS events,
               count(DISTINCT kiosk_id)  AS kiosks
        FROM event_rollup_hourly
        WHERE bucket >= :start AND bucket < :end
          AND peripheral_model_id <> 0
        GROUP BY peripheral_model_id, event_type_id
    ),
    rated AS (
        SELECT m.peripheral_model_id,
               m.event_type_id,
               m.events,
               m.kiosks,
               a.activity,
               m.events::numeric / a.activity AS rate
        FROM per_model m
        JOIN model_activity a ON a.peripheral_model_id = m.peripheral_model_id
        WHERE a.activity > 0
    ),
    -- 해당 이벤트가 0건인 모델도 분모에 포함시킨다. 빼면 비교 기준이 부풀려져
    -- 멀쩡한 모델까지 이상치로 잡힌다.
    totals AS (
        SELECT et.event_type_id,
               sum(coalesce(m.events, 0)) AS events,
               sum(a.activity)            AS activity
        FROM (SELECT DISTINCT event_type_id FROM per_model) et
        CROSS JOIN model_activity a
        LEFT JOIN per_model m
               ON m.event_type_id = et.event_type_id
              AND m.peripheral_model_id = a.peripheral_model_id
        GROUP BY et.event_type_id
    )
    SELECT r.peripheral_model_id,
           r.event_type_id,
           r.events,
           r.activity,
           r.kiosks,
           pm.vendor,
           pm.model_name,
           pm.driver_version,
           pm.kind::text                           AS kind,
           et.code                                 AS event_code,
           r.rate,
           (t.events - r.events)::numeric
               / NULLIF(t.activity - r.activity, 0) AS baseline_rate
    FROM rated r
    JOIN totals t            ON t.event_type_id = r.event_type_id
    JOIN peripheral_model pm ON pm.peripheral_model_id = r.peripheral_model_id
    JOIN event_type et       ON et.event_type_id = r.event_type_id
    WHERE et.is_actionable
      -- 경고 이상만 본다. order.created 같은 정상 이벤트의 '비율'은 의미가 없다.
      AND et.default_sev >= 30
      AND r.events >= :min_events
      AND (t.activity - r.activity) >= :min_activity
      AND r.rate >= :min_rate
      AND r.rate >= :ratio * ((t.events - r.events)::numeric
                              / NULLIF(t.activity - r.activity, 0))
    ORDER BY r.rate DESC
    LIMIT :limit
    """
)


class PeripheralErrorRateDetector:
    id = "peripheral_error_rate"
    description = "동종 장치 대비 장애 발생률이 유의하게 높은 주변장치 모델"

    def __init__(
        self,
        min_events: int = 20,
        min_activity: int = 200,
        min_rate: float = 0.005,
        ratio: float = 2.0,
        limit: int = 20,
    ) -> None:
        # min_events    : 대상 모델의 해당 장애 최소 건수 (우연을 걸러낸다)
        # min_activity  : 비교 기준 쪽 최소 활동량 (비교 대상이 너무 작으면 판단 불가)
        # min_rate      : 활동 대비 최소 발생률. 이보다 드물면 실무상 무시한다
        # ratio         : 기준 대비 몇 배부터 이상으로 볼지
        self.min_events = min_events
        self.min_activity = min_activity
        self.min_rate = min_rate
        self.ratio = ratio
        self.limit = limit

    def run(self, session: Session, window: Window) -> list[Candidate]:
        rows = session.execute(
            _SQL,
            {
                "start": window.start,
                "end": window.end,
                "min_events": self.min_events,
                "min_activity": self.min_activity,
                "min_rate": self.min_rate,
                "ratio": self.ratio,
                "limit": self.limit,
            },
        ).mappings()

        candidates: list[Candidate] = []
        for r in rows:
            label = f"{r['vendor']} {r['model_name']}"
            candidates.append(
                Candidate(
                    detector_id=self.id,
                    signal=(
                        f"{label} 의 {r['event_code']} 발생률이 동종 장치 대비 "
                        f"{float(r['rate']) / float(r['baseline_rate']):.1f}배"
                    ),
                    dedupe_key=f"{self.id}:{r['peripheral_model_id']}:{r['event_type_id']}",
                    evidence=Evidence(
                        metric=f"{r['event_code']} per device activity ({label})",
                        query_id=self.id,
                        observed=float(r["rate"]),
                        baseline=float(r["baseline_rate"]),
                        sample_size=int(r["events"]),
                        window=window.label,
                        affected_kiosks=int(r["kiosks"]),
                    ),
                    context={
                        "peripheral_model_id": int(r["peripheral_model_id"]),
                        "kind": r["kind"],
                        "vendor": r["vendor"],
                        "model_name": r["model_name"],
                        "driver_version": r["driver_version"],
                        "event_code": r["event_code"],
                        "event_count": int(r["events"]),
                        "device_activity": int(r["activity"]),
                    },
                )
            )
        return candidates
