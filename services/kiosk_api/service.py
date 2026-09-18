"""가상 키오스크의 질문 응답 + 로그 적재.

답변 로직은 일부러 단순하다. 키워드 매칭으로 CMS 메뉴를 찾고, 못 찾으면 "안내할 수 없다"
고 답한다. 이 '못 찾음' 이 쌓이는 것이 이 프로젝트의 원재료다.

여기에 생성형 답변을 붙이면 당장은 그럴듯해 보이지만, 답을 지어내기 시작하는 순간
"어떤 주제에 콘텐츠가 없는가" 를 알 수 없게 된다. CMS 를 채우는 시스템의 목적과 어긋난다.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agents.analyst import textutil
from services.common.models import CmsMenu, Kiosk, QuestionLog, Site

FALLBACK_ANSWER = "죄송합니다. 해당 내용은 아직 안내해 드릴 수 없습니다. 직원에게 문의해 주세요."

# 이 점수 아래면 매칭 실패로 본다. 낮추면 엉뚱한 메뉴가 답하고, 높이면 있는 메뉴도 못 찾는다.
MATCH_THRESHOLD = 0.34


def score_menu(question: str, menu: CmsMenu) -> float:
    """질문과 메뉴의 관련도. 키워드 적중과 제목 유사도 중 큰 값을 쓴다."""
    normalized = textutil.normalize(question)
    hits = sum(1 for keyword in menu.keywords if textutil.normalize(keyword) in normalized)
    keyword_score = hits / max(len(menu.keywords), 1)
    if hits:
        # 하나라도 정확히 맞으면 충분히 신뢰한다. 키워드를 많이 달았다고 불리해지면 안 된다.
        keyword_score = max(keyword_score, 0.5)
    return max(keyword_score, textutil.similarity(question, menu.title))


def find_answer(question: str, menus: list[CmsMenu]) -> tuple[CmsMenu | None, float]:
    best: CmsMenu | None = None
    best_score = 0.0
    for menu in menus:
        score = score_menu(question, menu)
        if score > best_score:
            best, best_score = menu, score
    if best_score < MATCH_THRESHOLD:
        return None, best_score
    return best, best_score


def customer_of(session: Session, kiosk: Kiosk) -> int:
    return session.execute(
        select(Site.customer_id).where(Site.site_id == kiosk.site_id)
    ).scalar_one()


def published_menus(session: Session, customer_id: int) -> list[CmsMenu]:
    return list(
        session.scalars(
            select(CmsMenu)
            .where(CmsMenu.customer_id == customer_id, CmsMenu.status == "published")
            .order_by(CmsMenu.menu_id)
        )
    )


def ask(
    session: Session,
    kiosk: Kiosk,
    question_text: str,
    session_id: uuid.UUID | None = None,
    input_mode: str = "touch",
) -> QuestionLog:
    """질문을 받아 답하고, 반드시 기록한다.

    답을 못 했더라도 기록한다. 오히려 그 기록이 제일 값지다.
    """
    started = time.perf_counter()
    menus = published_menus(session, customer_of(session, kiosk))
    menu, _score = find_answer(question_text, menus)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    now = dt.datetime.now(dt.UTC)

    entry = QuestionLog(
        asked_at=now,
        kiosk_id=kiosk.kiosk_id,
        session_id=session_id,
        question_text=question_text.strip(),
        normalized_text=textutil.normalize(question_text),
        answer_text=menu.body if menu else FALLBACK_ANSWER,
        answer_source="cms_menu" if menu else "fallback",
        matched_menu_id=menu.menu_id if menu else None,
        response_ms=elapsed_ms,
        input_mode=input_mode,
    )
    session.add(entry)
    session.execute(update(Kiosk).where(Kiosk.kiosk_id == kiosk.kiosk_id).values(last_seen_at=now))
    session.commit()
    return entry
