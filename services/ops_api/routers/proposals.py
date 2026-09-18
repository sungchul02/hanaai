"""제안 검토 게이트.

이 라우터가 루프의 사람 승인 지점이다. status 를 approved 로 바꾸는 순간
orchestrator 가 개발 Agent 를 띄운다. 즉 여기가 유일한 '발사 버튼'이며,
그래서 Agent 는 이 엔드포인트에 접근할 수 없어야 한다. (설계 문서 0. 원칙 3)
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.common.models import FeatureProposal
from services.ops_api.schemas import ProposalDetail, ProposalSummary, ReviewDecision

router = APIRouter(prefix="/v1/proposals", tags=["proposals"])
DbSession = Annotated[Session, Depends(get_session)]

REVIEWABLE = ("draft", "pending_review")


@router.get("", response_model=list[ProposalSummary])
def list_proposals(
    session: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
) -> list[FeatureProposal]:
    stmt = (
        select(FeatureProposal)
        .order_by(FeatureProposal.impact_score.desc(), FeatureProposal.created_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(FeatureProposal.status == status_filter)
    return list(session.scalars(stmt))


@router.get("/{proposal_id}", response_model=ProposalDetail)
def get_proposal(proposal_id: int, session: DbSession) -> FeatureProposal:
    proposal = session.get(FeatureProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown proposal: {proposal_id}")
    return proposal


def _decide(
    session: Session, proposal_id: int, decision: ReviewDecision, new_status: str
) -> FeatureProposal:
    proposal = session.get(FeatureProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown proposal: {proposal_id}")
    if proposal.status not in REVIEWABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"proposal is already {proposal.status}",
        )
    proposal.status = new_status
    proposal.reviewed_by = decision.reviewer
    proposal.reviewed_at = dt.datetime.now(dt.UTC)
    proposal.review_note = decision.note
    session.commit()
    return proposal


@router.post("/{proposal_id}/approve", response_model=ProposalDetail)
def approve(proposal_id: int, decision: ReviewDecision, session: DbSession) -> FeatureProposal:
    """승인. orchestrator 가 이 상태 전이를 보고 개발 Agent 를 띄운다."""
    return _decide(session, proposal_id, decision, "approved")


@router.post("/{proposal_id}/reject", response_model=ProposalDetail)
def reject(proposal_id: int, decision: ReviewDecision, session: DbSession) -> FeatureProposal:
    return _decide(session, proposal_id, decision, "rejected")
