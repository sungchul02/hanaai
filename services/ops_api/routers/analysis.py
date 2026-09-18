"""'분석 실행' 버튼.

문서: "처음에는 자동 스케줄링 대신 버튼 방식으로 구현해도 전체 개념을 충분히 시연할 수 있다."
나중에 cron 으로 돌릴 때도 같은 함수를 부르면 된다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.analyst.generator import get_generator
from agents.analyst.runner import Window, run_analysis
from services.common.db import get_session
from services.common.models import AnalysisRun
from services.ops_api.schemas import RunAnalysisIn, RunAnalysisOut, RunRow

router = APIRouter(prefix="/v1/analysis", tags=["analysis"])
DbSession = Annotated[Session, Depends(get_session)]


@router.post("/run", response_model=RunAnalysisOut)
def run(payload: RunAnalysisIn, session: DbSession) -> RunAnalysisOut:
    try:
        generator = get_generator(payload.backend)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    run_id = run_analysis(
        session,
        Window.last_days(payload.days),
        customer_id=payload.customer_id,
        generator=generator,
    )
    record = session.get(AnalysisRun, run_id)
    assert record is not None
    return RunAnalysisOut(
        analysis_run_id=run_id,
        backend=generator.name,
        questions_seen=record.questions_seen or 0,
        clusters_found=record.clusters_found or 0,
        proposals_made=record.proposals_made or 0,
        error=record.error,
        stats={key: int(value) for key, value in (record.stats or {}).items()},
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
