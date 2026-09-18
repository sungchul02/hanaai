"""추천 콘텐츠 검토 — 문서의 [추가하기] [수정] [무시].

여기가 이 시스템의 유일한 '발사 버튼' 이다. AI 는 제안만 하고, 키오스크에 내보내는 것은
사람만 할 수 있다. 그래서 분석 쪽 코드는 이 라우터에 접근하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.analyst import textutil
from services.common.db import get_session
from services.common.models import CmsMenu, ContentProposal
from services.ops_api.schemas import (
    ApproveIn,
    DuplicateHint,
    IgnoreIn,
    MenuRow,
    ProposalRow,
)

router = APIRouter(prefix="/v1", tags=["review"])
DbSession = Annotated[Session, Depends(get_session)]

REVIEWABLE = ("pending_review",)


@router.get("/proposals", response_model=list[ProposalRow])
def list_proposals(
    session: DbSession,
    customer_id: int,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200),
) -> list[ContentProposal]:
    stmt = (
        select(ContentProposal)
        .where(ContentProposal.customer_id == customer_id)
        .order_by(ContentProposal.impact_score.desc(), ContentProposal.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(ContentProposal.status == status_filter)
    return list(session.scalars(stmt))


@router.get("/proposals/{proposal_id}", response_model=ProposalRow)
def get_proposal(proposal_id: int, session: DbSession) -> ContentProposal:
    return _load(session, proposal_id)


def _slugify(title: str, proposal_id: int) -> str:
    """메뉴 코드. 한글 제목이 대부분이라 영숫자만 남기면 비는 일이 잦다.

    그래서 제안 번호를 항상 뒤에 붙여 고객사 안에서 유일함을 보장한다.
    """
    base = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", title).strip("-").lower()
    return f"{base or 'menu'}-{proposal_id}"


# 제목이 이만큼 닮았으면 같은 주제로 본다. '증명서 발급 수수료' 와 '증명서 수수료' 같은 경우.
DUPLICATE_THRESHOLD = 0.7


def _find_duplicate(
    session: Session, customer_id: int, title: str, menu_id: int | None
) -> CmsMenu | None:
    """같은 주제를 이미 다루는 메뉴. 관리자가 지목했으면 그것을 쓴다."""
    if menu_id is not None:
        return session.get(CmsMenu, menu_id)
    menus = session.scalars(
        select(CmsMenu).where(CmsMenu.customer_id == customer_id, CmsMenu.status == "published")
    )
    best, best_score = None, DUPLICATE_THRESHOLD
    for menu in menus:
        score = textutil.similarity(title, menu.title)
        if menu.title == title:
            score = 1.0
        if score >= best_score:
            best, best_score = menu, score
    return best


@router.get("/proposals/{proposal_id}/duplicate", response_model=DuplicateHint | None)
def check_duplicate(proposal_id: int, session: DbSession) -> DuplicateHint | None:
    """이 제안과 겹치는 기존 메뉴가 있는지. 화면이 [추가하기] 전에 물어본다."""
    proposal = _load(session, proposal_id)
    existing = _find_duplicate(session, proposal.customer_id, proposal.title, None)
    if existing is None:
        return None
    return DuplicateHint(
        menu_id=existing.menu_id,
        title=existing.title,
        body=existing.body,
        keywords=list(existing.keywords),
        similarity=round(textutil.similarity(proposal.title, existing.title), 3),
    )


def _normalize(value: str) -> str:
    """공백 차이만으로 '고쳤다' 고 기록하지 않기 위한 다듬기.

    화면이 입력칸 값을 보낼 때 앞뒤 공백이나 줄바꿈이 섞인다. 그건 관리자가
    내용을 고친 것이 아니다.
    """
    return " ".join(value.split())


def _differs(proposal: ContentProposal, title: str, body: str, keywords: list[str]) -> bool:
    """관리자가 AI 초안에서 실제로 무언가를 바꿨는가."""
    if _normalize(title) != _normalize(proposal.title):
        return True
    if _normalize(body) != _normalize(proposal.body):
        return True
    # 키워드는 순서가 바뀐 것을 수정으로 보지 않는다. 매칭 결과가 같기 때문이다.
    return sorted(_normalize(k) for k in keywords) != sorted(
        _normalize(k) for k in proposal.keywords
    )


@router.post("/proposals/{proposal_id}/approve", response_model=MenuRow)
def approve(proposal_id: int, decision: ApproveIn, session: DbSession) -> CmsMenu:
    """[추가하기]. 승인 즉시 cms_menu 에 들어가고 키오스크가 답하기 시작한다.

    AI 초안을 그대로 썼으면 approved, 손봤으면 edited 로 남긴다.
    이 둘의 비율이 곧 초안 품질 지표다. 손보는 비율이 높아지면 프롬프트를 고쳐야 한다.
    """
    proposal = _load(session, proposal_id)
    if proposal.status not in REVIEWABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 처리된 제안이다: {proposal.status}")

    title = decision.title or proposal.title
    body = decision.body or proposal.body
    keywords = decision.keywords if decision.keywords is not None else list(proposal.keywords)

    # 값이 왔는지가 아니라 값이 달라졌는지를 본다.
    # 화면은 입력칸 값을 항상 보내므로, 왔는지만 보면 전부 edited 가 된다.
    # 실제로 그랬고 26건이 모두 edited 로 기록돼 품질 지표가 무의미해졌다.
    edited = _differs(proposal, title, body, keywords)

    existing = _find_duplicate(session, proposal.customer_id, title, decision.duplicate_of)
    if existing is not None and decision.on_duplicate != "new":
        # 승인할 때마다 같은 제목의 메뉴가 새로 생겼다. '증명서 발급 수수료' 가 네 개까지 늘었다.
        # 키오스크는 그중 하나만 답하므로 나머지는 관리 목록만 어지럽힌다.
        if decision.on_duplicate == "merge":
            existing.body = existing.body + "\n\n" + body
            existing.keywords = sorted(set(existing.keywords) | set(keywords))
        else:
            existing.body = body
            existing.keywords = keywords
            existing.title = title
        existing.origin = "ai_proposal"
        existing.updated_at = dt.datetime.now(dt.UTC)
        menu = existing
    else:
        menu = CmsMenu(
            customer_id=proposal.customer_id,
            code=_slugify(title, proposal.proposal_id),
            title=title,
            body=body,
            keywords=keywords,
            status="published",
            origin="ai_proposal",
        )
        session.add(menu)
    session.flush()

    proposal.status = "edited" if edited else "approved"
    proposal.reviewed_by = decision.reviewer
    proposal.reviewed_at = dt.datetime.now(dt.UTC)
    proposal.review_note = decision.note
    proposal.applied_menu_id = menu.menu_id
    session.commit()
    return menu


@router.post("/proposals/{proposal_id}/ignore", response_model=ProposalRow)
def ignore(proposal_id: int, decision: IgnoreIn, session: DbSession) -> ContentProposal:
    """[무시]. 거절 이유를 남기는 것이 중요하다.

    왜 안 썼는지가 쌓여야 프롬프트를 고칠 수 있다. 그냥 사라지면 개선할 근거가 없다.
    """
    proposal = _load(session, proposal_id)
    if proposal.status not in REVIEWABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"이미 처리된 제안이다: {proposal.status}")
    proposal.status = "ignored"
    proposal.reviewed_by = decision.reviewer
    proposal.reviewed_at = dt.datetime.now(dt.UTC)
    proposal.review_note = decision.note
    session.commit()
    return proposal


def _load(session: Session, proposal_id: int) -> ContentProposal:
    proposal = session.get(ContentProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown proposal: {proposal_id}")
    return proposal
