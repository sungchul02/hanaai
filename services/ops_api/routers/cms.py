"""관리자가 직접 메뉴를 만들고 고치는 곳.

AI 추천만으로는 CMS 가 아니다. 이미 아는 안내는 그냥 적으면 되고, AI 는 '관리자가 미처
몰랐던 공백' 을 찾는 데 쓴다. 둘은 경쟁 관계가 아니라 서로 다른 일을 한다.

특히 **키워드 수정**이 중요하다. "차 세울 데 있나요?" 처럼 콘텐츠는 있는데 말이 안 겹쳐
못 잡는 질문은 새 메뉴가 아니라 키워드 한 줄로 해결된다. 그 경로가 없으면 관리자는
AI 가 중복 메뉴를 제안할 때까지 기다리는 수밖에 없다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.analyst.verify import as_menu, verify_draft
from services.common.db import get_session
from services.common.models import CmsMenu, Kiosk, QuestionLog, Site
from services.ops_api.schemas import (
    MenuCreateIn,
    MenuPreviewIn,
    MenuPreviewOut,
    MenuRow,
    MenuUpdateIn,
)

router = APIRouter(prefix="/v1/menus", tags=["cms"])
DbSession = Annotated[Session, Depends(get_session)]


def _slugify(title: str) -> str:
    base = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", title).strip("-").lower()
    return base or "menu"


def _unique_code(session: Session, customer_id: int, wanted: str) -> str:
    """고객사 안에서 유일한 코드를 만든다. 겹치면 숫자를 붙인다."""
    code = wanted
    for suffix in range(1, 50):
        exists = session.scalar(
            select(CmsMenu.menu_id).where(CmsMenu.customer_id == customer_id, CmsMenu.code == code)
        )
        if exists is None:
            return code
        code = f"{wanted}-{suffix}"
    raise HTTPException(status.HTTP_409_CONFLICT, "코드를 만들 수 없다. 제목을 바꿔라.")


@router.get("", response_model=list[MenuRow])
def list_menus(session: DbSession, customer_id: int) -> list[CmsMenu]:
    return list(
        session.scalars(
            select(CmsMenu)
            .where(CmsMenu.customer_id == customer_id)
            .order_by(CmsMenu.status, CmsMenu.menu_id)
        )
    )


@router.post("", response_model=MenuRow, status_code=status.HTTP_201_CREATED)
def create_menu(payload: MenuCreateIn, session: DbSession) -> CmsMenu:
    menu = CmsMenu(
        customer_id=payload.customer_id,
        code=_unique_code(session, payload.customer_id, payload.code or _slugify(payload.title)),
        title=payload.title,
        body=payload.body,
        keywords=payload.keywords,
        status=payload.status,
        origin="manual",
    )
    session.add(menu)
    session.commit()
    return menu


@router.patch("/{menu_id}", response_model=MenuRow)
def update_menu(menu_id: int, payload: MenuUpdateIn, session: DbSession) -> CmsMenu:
    menu = session.get(CmsMenu, menu_id)
    if menu is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown menu: {menu_id}")

    if payload.title is not None:
        menu.title = payload.title
    if payload.body is not None:
        menu.body = payload.body
    if payload.keywords is not None:
        menu.keywords = payload.keywords
    if payload.status is not None:
        menu.status = payload.status
    session.commit()
    return menu


@router.post("/preview", response_model=MenuPreviewOut)
def preview_menu(payload: MenuPreviewIn, session: DbSession) -> MenuPreviewOut:
    """저장 전에 이 메뉴가 실제로 어떤 질문을 잡는지 돌려본다.

    AI 추천에 붙는 검증 수치와 **같은 도구**를 쓴다. 관리자가 직접 쓴 것에는 숫자가 없으면
    앞뒤가 안 맞고, 키워드를 고칠 때 효과를 바로 볼 수 없다.
    """
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=payload.days)
    kiosk_ids = [
        row[0]
        for row in session.execute(
            select(Kiosk.kiosk_id)
            .join(Site, Site.site_id == Kiosk.site_id)
            .where(Site.customer_id == payload.customer_id)
        )
    ]
    # 아직 답하지 못한 질문만 본다. 이미 답하고 있는 것은 이 메뉴의 성과가 아니다.
    questions = [
        row[0]
        for row in session.execute(
            select(QuestionLog.question_text)
            .where(
                QuestionLog.kiosk_id.in_(kiosk_ids or [0]),
                QuestionLog.asked_at >= since,
                QuestionLog.answer_source != "cms_menu",
                # 분석 전 질문은 verdict 가 pending 이다. useful 만 보면 방금 들어온
                # 질문이 미리보기에서 통째로 빠진다. 쓰레기만 제외하는 것이 맞다.
                QuestionLog.verdict.notin_(("abusive", "too_short")),
            )
            .order_by(QuestionLog.asked_at.desc())
            .limit(2000)
        )
    ]

    others = list(
        session.scalars(
            select(CmsMenu).where(
                CmsMenu.customer_id == payload.customer_id,
                CmsMenu.status == "published",
                CmsMenu.menu_id != (payload.menu_id or -1),
            )
        )
    )
    draft = as_menu(payload.title, payload.body, payload.keywords)
    result = verify_draft(draft, questions, others, miss_limit=8)

    # 이 초안이 새로 잡게 되는 질문을 보여준다. 관리자가 "맞다/아니다" 를 바로 판단한다.
    samples: list[str] = []
    for question in questions:
        if len(samples) >= 8:
            break
        menu, _score, verdict = verify_one(question, others, draft)
        if verdict == "cms_menu" and menu is draft:
            samples.append(question)

    return MenuPreviewOut(
        total=result.total,
        by_draft=result.by_draft,
        by_existing=result.by_existing,
        coverage=round(result.coverage, 4),
        samples=samples,
        misses=result.misses,
    )


def verify_one(
    question: str, others: list[CmsMenu], draft: CmsMenu
) -> tuple[CmsMenu | None, float, str]:
    from services.kiosk_api.service import find_answer

    return find_answer(question, [*others, draft])
