"""분석 실행 — 두 단계로 나뉘어 있다.

    [1단계 분류]  질문을 묶고 LLM 이 가치를 판단하고 갈래를 나눈다.  싸다.
         ↓
    관리자가 카테고리별로 훑어보고 무엇을 답할지 정한다.            ← 사람
         ↓
    [2단계 생성]  승인된 주제만 근거를 찾고 답변 초안을 만든다.      비싸다.
         ↓
    관리자가 초안을 검토하고 CMS 에 반영한다.                       ← 사람

버튼 하나로 끝까지 가던 것을 나눈 이유는 비용과 통제다.
관리자가 원하지 않는 주제에 근거 조사와 생성 비용을 쓸 이유가 없고,
다 끝난 뒤에 결과를 보여주면 되돌릴 방법이 없다.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from agents.analyst.generation import run_generation
from agents.analyst.generator import get_generator
from agents.analyst.runner import Window, run_triage
from services.common.db import get_session
from services.common.models import AnalysisRun, QuestionCluster
from services.ops_api.schemas import (
    ClusterReviewIn,
    RunGenerationIn,
    RunGenerationOut,
    RunRow,
    RunTriageIn,
    RunTriageOut,
    TriageReport,
    TriageTopic,
)

router = APIRouter(prefix="/v1/analysis", tags=["analysis"])
DbSession = Annotated[Session, Depends(get_session)]


@router.post("/triage", response_model=RunTriageOut)
def triage(payload: RunTriageIn, session: DbSession) -> RunTriageOut:
    """[1단계 분류]. 답변은 만들지 않는다. 무엇을 답할지는 사람이 정한다."""
    try:
        generator = get_generator(payload.backend)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    run_id = run_triage(
        session,
        Window.last_days(payload.days),
        customer_id=payload.customer_id,
        generator=generator,
    )
    record = session.get(AnalysisRun, run_id)
    assert record is not None
    stats = record.stats or {}
    return RunTriageOut(
        analysis_run_id=run_id,
        backend=generator.name,
        questions_seen=record.questions_seen or 0,
        clusters_found=record.clusters_found or 0,
        kept=int(stats.get("triage_kept", 0)),
        dropped=int(stats.get("triage_dropped", 0)),
        stats={key: int(value) for key, value in stats.items()},
        error=record.error,
    )


@router.get("/{run_id}/report", response_model=TriageReport)
def report(run_id: int, session: DbSession) -> TriageReport:
    """중간 보고. 관리자가 이 화면을 보고 무엇을 답할지 정한다.

    카테고리별로 묶어서 돌려준다. 주제 단위로 하나씩 보면 판단이 안 된다 —
    '시설 이용' 이 여섯 개 있다는 것을 봐야 우선순위가 잡힌다.
    """
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown run: {run_id}")

    rows = session.scalars(
        select(QuestionCluster)
        .where(
            QuestionCluster.analysis_run_id == run_id,
            QuestionCluster.review_status.isnot(None),
        )
        .order_by(QuestionCluster.category, QuestionCluster.size.desc())
    )
    topics = [
        TriageTopic(
            cluster_id=row.cluster_id,
            label=row.label,
            category=row.category or "기타",
            size=row.size,
            unanswered=row.unanswered,
            keywords=list(row.keywords),
            triage_keep=row.triage_keep,
            triage_reason=row.triage_reason,
            review_status=row.review_status or "pending_review",
            sample_questions=[],
            evidence_found=row.evidence_found,
            evidence_missing=list(row.evidence_missing or []),
        )
        for row in rows
    ]
    return TriageReport(
        analysis_run_id=run_id,
        status=run.status,
        questions_seen=run.questions_seen or 0,
        clusters_found=run.clusters_found or 0,
        stats={key: int(value) for key, value in (run.stats or {}).items()},
        topics=topics,
    )


@router.post("/{run_id}/review")
def review_clusters(run_id: int, decision: ClusterReviewIn, session: DbSession) -> dict[str, int]:
    """관리자가 어떤 주제에 답을 만들지 정한다. 이 관문이 2단계 비용을 가른다."""
    if decision.action not in ("approved", "rejected"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "action 은 approved 또는 rejected")

    result = session.execute(
        update(QuestionCluster)
        .where(
            QuestionCluster.analysis_run_id == run_id,
            QuestionCluster.cluster_id.in_(decision.cluster_ids),
            # 이미 답이 만들어진 주제는 되돌리지 않는다
            QuestionCluster.review_status.in_(["pending_review", "approved", "rejected"]),
        )
        .values(
            review_status=decision.action,
            reviewed_by=decision.reviewer,
            reviewed_at=dt.datetime.now(dt.UTC),
        )
    )
    session.commit()
    return {"updated": result.rowcount}  # type: ignore[attr-defined]


@router.post("/{run_id}/generate", response_model=RunGenerationOut)
def generate(run_id: int, payload: RunGenerationIn, session: DbSession) -> RunGenerationOut:
    """[2단계 생성]. 승인된 주제만 근거를 찾고 답변 초안을 만든다."""
    try:
        generator = get_generator(payload.backend)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    approved = session.scalar(
        select(func.count())
        .select_from(QuestionCluster)
        .where(
            QuestionCluster.analysis_run_id == run_id,
            QuestionCluster.review_status == "approved",
        )
    )
    if not approved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "승인된 주제가 없다. 중간 보고에서 답을 만들 주제를 먼저 고른다.",
        )

    stored = run_generation(session, run_id, generator)
    record = session.get(AnalysisRun, run_id)
    assert record is not None
    stats = record.stats or {}
    return RunGenerationOut(
        analysis_run_id=run_id,
        backend=generator.name,
        approved_topics=int(stats.get("approved_topics", 0)),
        evidence_found=int(stats.get("evidence_found", 0)),
        evidence_missing=int(stats.get("evidence_missing", 0)),
        proposals_made=stored,
        stats={key: int(value) for key, value in stats.items()},
        error=record.error,
    )


@router.get("/runs", response_model=list[RunRow])
def runs(session: DbSession, customer_id: int, limit: int = 20) -> list[RunRow]:
    """분석 이력. 무엇이 언제 어떻게 돌았는지가 남아야 결과를 믿을 수 있다.

    비용도 함께 보여준다. LLM 을 몇 번 불렀고 얼마를 썼는지 모르면
    '분석 실행' 버튼이 얼마짜리인지 알 수 없다.
    """
    rows = session.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.customer_id == customer_id)
        .order_by(AnalysisRun.analysis_run_id.desc())
        .limit(limit)
    )
    return [
        RunRow(
            analysis_run_id=r.analysis_run_id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=r.status,
            model=r.model,
            questions_seen=r.questions_seen or 0,
            clusters_found=r.clusters_found or 0,
            proposals_made=r.proposals_made or 0,
            stats=r.stats or {},
            llm_calls=(r.token_usage or {}).get("calls"),
            cost_usd=(r.token_usage or {}).get("cost_usd"),
            error=r.error,
        )
        for r in rows
    ]
