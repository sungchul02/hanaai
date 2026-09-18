"""'분석 실행' 버튼.

문서: "처음에는 자동 스케줄링 대신 버튼 방식으로 구현해도 전체 개념을 충분히 시연할 수 있다."
나중에 cron 으로 돌릴 때도 같은 함수를 부르면 된다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from agents.analyst.generator import get_generator
from agents.analyst.runner import Window, run_analysis
from services.common.db import get_session
from services.common.models import AnalysisRun
from services.ops_api.schemas import RunAnalysisIn, RunAnalysisOut

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
    )
