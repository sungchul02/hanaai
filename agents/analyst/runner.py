"""분석 실행기.

탐지기 전부 실행 → 후보 수집 → 제안 생성 → DB 기록. 여기까지가 과제 2의 범위다.
제안은 pending_review 로만 들어간다. approved 로 만드는 것은 사람뿐이다.
"""

from __future__ import annotations

import datetime as dt

import structlog
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agents.analyst.generator import ProposalGenerator, get_generator
from agents.contracts.proposal import Candidate
from agents.detectors import ALL_DETECTORS, DETECTOR_VERSION, Window
from services.common.models import AnalysisRun, FeatureProposal

log = structlog.get_logger(__name__)


def collect_candidates(session: Session, window: Window) -> list[Candidate]:
    candidates: list[Candidate] = []
    for detector in ALL_DETECTORS:
        found = detector.run(session, window)
        log.info("detector_done", detector=detector.id, candidates=len(found))
        candidates.extend(found)
    return candidates


def run_analysis(
    session: Session,
    window: Window,
    generator: ProposalGenerator | None = None,
) -> int:
    """분석 1회 실행. analysis_run_id 를 돌려준다."""
    # 백엔드 선택은 설정이 한다. 여기서는 어느 LLM 인지 알 필요가 없다.
    generator = generator or get_generator()

    run = AnalysisRun(
        window_start=window.start,
        window_end=window.end,
        detector_version=DETECTOR_VERSION,
        model=generator.name,
        status="running",
    )
    session.add(run)
    session.commit()

    try:
        candidates = collect_candidates(session, window)
        run.candidate_count = len(candidates)
        proposals = generator.generate(candidates, window) if candidates else []

        stored = 0
        for proposal in proposals:
            dedupe_key = proposal.compute_dedupe_key()
            # 부분 유니크 인덱스가 "검토 대기/승인 상태의 같은 문제"를 막는다.
            # 이미 있으면 조용히 건너뛴다. 매일 같은 제안이 쌓이는 것을 막기 위함이다.
            result = session.execute(
                pg_insert(FeatureProposal)
                .values(
                    analysis_run_id=run.analysis_run_id,
                    title=proposal.title,
                    body=proposal.model_dump(mode="json"),
                    scope=proposal.scope,
                    impact_score=proposal.impact_score,
                    status="pending_review",
                    dedupe_key=dedupe_key,
                )
                .on_conflict_do_nothing(
                    index_elements=["dedupe_key"],
                    # 부분 인덱스는 WHERE 절까지 알려줘야 Postgres 가 추론할 수 있다.
                    # 0002_agent_loop_up.sql 의 feature_proposal_dedupe_uq 와 같아야 한다.
                    index_where=text(
                        "dedupe_key IS NOT NULL AND status IN ('pending_review', 'approved')"
                    ),
                )
                .returning(FeatureProposal.proposal_id)
            ).scalar_one_or_none()
            if result is not None:
                stored += 1

        run.token_usage = getattr(generator, "last_usage", None)
        rejected = getattr(generator, "last_errors", [])
        if rejected:
            # 계약 위반으로 버려진 응답은 조용히 넘기지 않는다.
            run.error = f"검증 실패 {len(rejected)}건: " + " | ".join(rejected)[:2000]
        run.status = "succeeded"
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()
        log.info(
            "analysis_done",
            run_id=run.analysis_run_id,
            candidates=len(candidates),
            proposals=len(proposals),
            stored=stored,
            backend=generator.name,
        )
    except Exception as exc:
        session.rollback()
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()
        raise

    return run.analysis_run_id
