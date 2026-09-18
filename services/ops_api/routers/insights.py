"""대시보드 조회 — 요약 카드, 질문 목록, 많이 나온 주제."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.common.models import Customer
from services.ops_api.schemas import CustomerRow, DashboardSummary, QuestionRow, TopicRow

router = APIRouter(prefix="/v1", tags=["insights"])
DbSession = Annotated[Session, Depends(get_session)]

# 카드 한 줄에 필요한 수치를 왕복 한 번에 모은다.
_SUMMARY_SQL = text(
    """
    WITH scope AS (
        SELECT k.kiosk_id
        FROM kiosk k JOIN site s ON s.site_id = k.site_id
        WHERE s.customer_id = :customer_id
    ),
    recent AS (
        SELECT q.*
        FROM question_log q
        WHERE q.kiosk_id IN (SELECT kiosk_id FROM scope)
          AND q.asked_at >= now() - interval '7 days'
    )
    SELECT
      (SELECT count(*) FROM recent)                                         AS questions_7d,
      (SELECT count(*) FROM recent WHERE asked_at >= date_trunc('day', now()))
                                                                            AS questions_today,
      (SELECT count(*) FROM recent WHERE answer_source = 'cms_menu')         AS answered_7d,
      (SELECT count(*) FROM recent WHERE answer_source <> 'cms_menu')        AS unanswered_7d,
      (SELECT count(*) FROM recent WHERE verdict IN ('abusive', 'too_short')) AS junk_7d,
      (SELECT count(*) FROM cms_menu
        WHERE customer_id = :customer_id AND status = 'published')           AS menus_published,
      (SELECT count(*) FROM cms_menu
        WHERE customer_id = :customer_id AND origin = 'ai_proposal')         AS menus_from_ai,
      (SELECT count(*) FROM content_proposal
        WHERE customer_id = :customer_id AND status = 'pending_review')      AS proposals_pending,
      (SELECT max(finished_at) FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded')           AS last_analysis_at
    """
)


@router.get("/customers", response_model=list[CustomerRow])
def list_customers(session: DbSession) -> list[Customer]:
    return list(session.scalars(select(Customer).order_by(Customer.code)))


@router.get("/dashboard/summary", response_model=DashboardSummary)
def summary(session: DbSession, customer_id: int) -> DashboardSummary:
    row = session.execute(_SUMMARY_SQL, {"customer_id": customer_id}).mappings().one()
    data = dict(row)
    total = data["questions_7d"]
    data["answer_rate_7d"] = round(data["answered_7d"] / total, 4) if total else None
    return DashboardSummary(**data)


_QUESTIONS_SQL = text(
    """
    SELECT q.question_id, q.asked_at, k.serial_no AS kiosk_serial, q.question_text,
           q.answer_source::text AS answer_source, m.title AS matched_menu_title,
           q.verdict::text AS verdict
    FROM question_log q
    JOIN kiosk k ON k.kiosk_id = q.kiosk_id
    JOIN site s  ON s.site_id = k.site_id
    LEFT JOIN cms_menu m ON m.menu_id = q.matched_menu_id
    WHERE s.customer_id = :customer_id
      AND (:only_unanswered = FALSE OR q.answer_source <> 'cms_menu')
    ORDER BY q.asked_at DESC
    LIMIT :limit
    """
)


@router.get("/questions", response_model=list[QuestionRow])
def list_questions(
    session: DbSession,
    customer_id: int,
    only_unanswered: bool = Query(default=False, description="답하지 못한 질문만"),
    limit: int = Query(default=50, le=500),
) -> list[QuestionRow]:
    rows = session.execute(
        _QUESTIONS_SQL,
        {"customer_id": customer_id, "only_unanswered": only_unanswered, "limit": limit},
    ).mappings()
    return [QuestionRow(**dict(r)) for r in rows]


# 가장 최근 분석의 주제만 보여준다. 옛 분석 결과가 섞이면 무엇이 최신인지 알 수 없다.
_TOPICS_SQL = text(
    """
    SELECT c.cluster_id, c.label, c.size, c.unanswered, c.keywords,
           m.title AS covered_menu_title,
           EXISTS (SELECT 1 FROM content_proposal p WHERE p.cluster_id = c.cluster_id)
               AS has_proposal
    FROM question_cluster c
    LEFT JOIN cms_menu m ON m.menu_id = c.covered_menu_id
    WHERE c.analysis_run_id = (
        SELECT max(analysis_run_id) FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded'
    )
    ORDER BY c.size DESC
    LIMIT :limit
    """
)


@router.get("/topics", response_model=list[TopicRow])
def list_topics(
    session: DbSession, customer_id: int, limit: int = Query(default=20, le=200)
) -> list[TopicRow]:
    rows = session.execute(_TOPICS_SQL, {"customer_id": customer_id, "limit": limit}).mappings()
    return [TopicRow(**dict(r)) for r in rows]
