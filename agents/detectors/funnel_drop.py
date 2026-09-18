"""퍼널 이탈 탐지.

"접근성 모드에 들어온 사람 중 28퍼센트가 높이조절 직후 나간다" 처럼,
정해진 흐름의 어느 단계에서 사람이 빠지는지를 잡는다.

세션을 하나씩 따라가지 않고 **단계별 건수의 비율**로 본다. 집계 테이블만으로 계산되므로
원본 event 를 훑지 않아도 되고, 그래서 매일 돌릴 수 있다.

퍼널은 코드에 선언한다. 데이터에서 자동으로 흐름을 추론하는 것보다,
"이 순서가 우리가 의도한 흐름" 이라고 사람이 적어두는 편이 정확하고 설명도 쉽다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.contracts.proposal import Candidate, Evidence
from agents.detectors.base import Window


@dataclass(frozen=True)
class Funnel:
    name: str
    steps: tuple[str, ...]


# 의도한 흐름. 단계가 바뀌면 여기만 고친다.
FUNNELS = (
    Funnel(
        name="배리어프리 이용",
        steps=(
            "access.mode_entered",
            "access.height_adjust_completed",
            "access.mode_completed",
        ),
    ),
    Funnel(
        name="음성 주문",
        steps=(
            "session.started",
            "intent.resolved",
            "order.created",
            "payment.approved",
        ),
    ),
)

# 단계별 건수를 app_version 으로 갈라서 센다.
# 버전별로 비교해야 "배포 때문인지" 를 물을 수 있다.
_SQL = text(
    """
    SELECT r.app_version,
           et.code            AS event_code,
           sum(r.event_count) AS n,
           count(DISTINCT r.kiosk_id) AS kiosks
    FROM event_rollup_hourly r
    JOIN event_type et ON et.event_type_id = r.event_type_id
    WHERE r.bucket >= :start AND r.bucket < :end
      AND r.app_version <> ''
      AND et.code = ANY(:codes)
    GROUP BY r.app_version, et.code
    """
)


class FunnelDropDetector:
    id = "session_funnel_drop"
    description = "정해진 흐름의 특정 단계에서 이탈이 집중되는 구간"

    def __init__(
        self,
        min_entries: int = 50,
        min_ratio: float = 1.5,
        min_drop: float = 0.10,
        limit: int = 10,
    ) -> None:
        # min_drop : 이탈률이 이보다 낮으면 실무상 무시한다
        # min_ratio: 비교 대상 버전 대비 몇 배부터 이상으로 볼지
        self.min_entries = min_entries
        self.min_ratio = min_ratio
        self.min_drop = min_drop
        self.limit = limit

    def run(self, session: Session, window: Window) -> list[Candidate]:
        codes = sorted({code for funnel in FUNNELS for code in funnel.steps})
        rows = session.execute(
            _SQL, {"start": window.start, "end": window.end, "codes": codes}
        ).mappings()

        # (version, code) -> count
        counts: dict[tuple[str, str], int] = {}
        kiosks: dict[str, int] = {}
        for r in rows:
            counts[(r["app_version"], r["event_code"])] = int(r["n"])
            kiosks[r["app_version"]] = max(kiosks.get(r["app_version"], 0), int(r["kiosks"]))

        versions = sorted(kiosks)
        if len(versions) < 2:
            return []  # 비교 대상이 없으면 판단하지 않는다

        candidates: list[Candidate] = []
        for funnel in FUNNELS:
            for index in range(len(funnel.steps) - 1):
                frm, to = funnel.steps[index], funnel.steps[index + 1]
                drops = {}
                for version in versions:
                    entered = counts.get((version, frm), 0)
                    arrived = counts.get((version, to), 0)
                    if entered >= self.min_entries:
                        drops[version] = (1 - arrived / entered, entered, arrived)
                if len(drops) < 2:
                    continue

                worst = max(drops, key=lambda v: drops[v][0])
                worst_rate, worst_entered, worst_arrived = drops[worst]
                others = [drops[v][0] for v in drops if v != worst]
                baseline = sum(others) / len(others)

                if worst_rate < self.min_drop:
                    continue
                if worst_rate < self.min_ratio * max(baseline, 0.0001):
                    continue

                candidates.append(
                    Candidate(
                        detector_id=self.id,
                        signal=(
                            f"[{funnel.name}] {frm} → {to} 이탈률이 {worst} 에서 "
                            f"{worst_rate * 100:.1f}퍼센트 "
                            f"(다른 버전 {baseline * 100:.1f}퍼센트)"
                        ),
                        dedupe_key=f"{self.id}:{funnel.name}:{frm}:{to}:{worst}",
                        evidence=Evidence(
                            metric=f"{frm} -> {to} drop rate ({worst})",
                            query_id=self.id,
                            observed=round(worst_rate, 4),
                            baseline=round(baseline, 4),
                            sample_size=worst_entered,
                            window=window.label,
                            affected_kiosks=kiosks.get(worst, 0),
                        ),
                        context={
                            "funnel": funnel.name,
                            "step_from": frm,
                            "step_to": to,
                            "app_version": worst,
                            "entered": worst_entered,
                            "arrived": worst_arrived,
                        },
                    )
                )

        candidates.sort(key=lambda c: c.evidence.observed, reverse=True)
        return candidates[: self.limit]
