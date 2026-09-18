"""세션당 재시도 폭주 탐지.

"이 지점 키오스크는 세션마다 되물음이 5배 많다" 를 잡는다.
사용자가 같은 걸 반복하고 있다는 뜻이고, 대개 UX 나 인식 품질 문제다.

분모가 '세션 수' 인 것이 핵심이다. 되물음 '횟수' 만 보면 손님 많은 지점이 항상 1등이 된다.
세션 수는 session.started 의 distinct_sessions 로 잡는다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.contracts.proposal import Candidate, Evidence
from agents.detectors.base import Window

# 세션당 반복을 보고 싶은 이벤트들. 사전이 늘면 여기에 추가한다.
RETRY_CODES = (
    "intent.clarify_requested",
    "input.retry",
    "stt.failed",
    "stt.timeout",
    "screen.back",
)

_SQL = text(
    """
    WITH sessions AS (
        SELECT r.kiosk_id, sum(r.distinct_sessions) AS sessions
        FROM event_rollup_hourly r
        JOIN event_type et ON et.event_type_id = r.event_type_id
        WHERE r.bucket >= :start AND r.bucket < :end
          AND et.code = 'session.started'
        GROUP BY r.kiosk_id
    ),
    retries AS (
        SELECT r.kiosk_id, r.event_type_id, sum(r.event_count) AS n
        FROM event_rollup_hourly r
        JOIN event_type et ON et.event_type_id = r.event_type_id
        WHERE r.bucket >= :start AND r.bucket < :end
          AND et.code = ANY(:codes)
        GROUP BY r.kiosk_id, r.event_type_id
    ),
    rated AS (
        SELECT t.kiosk_id, t.event_type_id, t.n, s.sessions,
               t.n::numeric / s.sessions AS per_session
        FROM retries t JOIN sessions s ON s.kiosk_id = t.kiosk_id
        WHERE s.sessions >= :min_sessions
    ),
    -- 비교 기준에서 대상 키오스크 자신을 뺀다. 빼지 않으면 대수의 키오스크가
    -- 사실상 자기 자신과 비교하게 되어 이상치로 잡히지 않는다.
    totals AS (
        SELECT event_type_id, sum(n) AS n, sum(sessions) AS sessions
        FROM rated GROUP BY event_type_id
    )
    SELECT k.serial_no,
           s.code           AS site_code,
           c.code           AS customer_code,
           et.code          AS event_code,
           r.kiosk_id,
           r.n,
           r.sessions,
           r.per_session,
           (t.n - r.n)::numeric / NULLIF(t.sessions - r.sessions, 0) AS baseline_per_session
    FROM rated r
    JOIN totals t      ON t.event_type_id = r.event_type_id
    JOIN event_type et ON et.event_type_id = r.event_type_id
    JOIN kiosk k       ON k.kiosk_id = r.kiosk_id
    JOIN site s        ON s.site_id = k.site_id
    JOIN customer c    ON c.customer_id = s.customer_id
    WHERE (t.sessions - r.sessions) >= :min_sessions
      AND r.n >= :min_events
      AND r.per_session >= :min_ratio
          * ((t.n - r.n)::numeric / NULLIF(t.sessions - r.sessions, 0))
    ORDER BY r.per_session DESC
    LIMIT :limit
    """
)


class RetryStormDetector:
    id = "retry_storm"
    description = "세션당 재시도·되물음이 동종 대비 과도한 키오스크"

    def __init__(
        self,
        min_sessions: int = 50,
        min_events: int = 30,
        min_ratio: float = 2.0,
        limit: int = 15,
    ) -> None:
        self.min_sessions = min_sessions
        self.min_events = min_events
        self.min_ratio = min_ratio
        self.limit = limit

    def run(self, session: Session, window: Window) -> list[Candidate]:
        rows = session.execute(
            _SQL,
            {
                "start": window.start,
                "end": window.end,
                "codes": list(RETRY_CODES),
                "min_sessions": self.min_sessions,
                "min_events": self.min_events,
                "min_ratio": self.min_ratio,
                "limit": self.limit,
            },
        ).mappings()

        candidates: list[Candidate] = []
        for r in rows:
            observed = float(r["per_session"])
            baseline = float(r["baseline_per_session"])
            candidates.append(
                Candidate(
                    detector_id=self.id,
                    signal=(
                        f"{r['serial_no']} 의 세션당 {r['event_code']} 가 "
                        f"동종 대비 {observed / max(baseline, 0.0001):.1f}배 "
                        f"({observed:.2f} vs {baseline:.2f})"
                    ),
                    dedupe_key=f"{self.id}:{r['kiosk_id']}:{r['event_code']}",
                    evidence=Evidence(
                        metric=f"{r['event_code']} per session ({r['serial_no']})",
                        query_id=self.id,
                        observed=round(observed, 4),
                        baseline=round(baseline, 4),
                        sample_size=int(r["n"]),
                        window=window.label,
                        affected_kiosks=1,
                    ),
                    context={
                        "kiosk_serial": r["serial_no"],
                        "site_code": r["site_code"],
                        "customer_code": r["customer_code"],
                        "event_code": r["event_code"],
                        "sessions": int(r["sessions"]),
                    },
                )
            )
        return candidates
