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
      -- 약하게 맞은 것은 답을 보여줬어도 '답한 것' 으로 치지 않는다.
      -- 스치듯 걸린 답변이 응답 완료로 잡히면 진짜 공백이 묻힌다.
      (SELECT count(*) FROM recent WHERE answer_source = 'low_confidence')    AS weak_7d,
      -- 규칙이 거른 것(abusive/too_short)과 LLM 이 거른 것(irrelevant)을 함께 센다.
      -- LLM 판정을 빼면 "AI 가 15건을 걸렀는데 화면에는 3건" 이 되어 숫자를 믿을 수 없다.
      (SELECT count(*) FROM recent
        WHERE verdict IN ('abusive', 'too_short', 'irrelevant'))            AS junk_7d,
      (SELECT count(*) FROM recent WHERE verdict = 'irrelevant')            AS junk_by_llm_7d,
      (SELECT count(*) FROM cms_menu
        WHERE customer_id = :customer_id AND status = 'published')           AS menus_published,
      (SELECT count(*) FROM cms_menu
        WHERE customer_id = :customer_id AND origin = 'ai_proposal')         AS menus_from_ai,
      (SELECT count(*) FROM content_proposal
        WHERE customer_id = :customer_id AND status = 'pending_review')      AS proposals_pending,
      (SELECT max(finished_at) FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded')           AS last_analysis_at,
      -- 마지막 분석이 실제로 무엇을 했는지. 이게 없으면 화면의 '단계' 표시가
      -- 분석과 무관한 숫자를 지어내게 된다. 실제로 그랬다.
      (SELECT stats FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded'
        ORDER BY analysis_run_id DESC LIMIT 1)                               AS last_run_stats,
      (SELECT questions_seen FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded'
        ORDER BY analysis_run_id DESC LIMIT 1)                               AS last_run_questions
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
           q.match_score,
           q.verdict::text AS verdict, q.verdict_reason
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
           c.covered_menu_id,
           c.triage_keep, c.triage_reason,
           c.evidence_found, c.evidence_summary, c.evidence_missing,
           m.title AS covered_menu_title,
           EXISTS (SELECT 1 FROM content_proposal p WHERE p.cluster_id = c.cluster_id)
               AS has_proposal
    FROM question_cluster c
    LEFT JOIN cms_menu m ON m.menu_id = c.covered_menu_id
    WHERE c.analysis_run_id = (
        SELECT max(analysis_run_id) FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded'
    )
    -- 관리자가 조치할 수 있는 것부터 보여준다.
    -- 크기순으로만 두면 Agent 가 판단한 주제(근거 없음 · AI가 제외)가
    -- 판단조차 안 된 주제들 사이에 흩어져 묻힌다. 실제로 "화장실 물어봤는데
    -- 어떻게 됐는지 안 보인다" 는 말을 들었고, 16번째 줄에 있었다.
    ORDER BY
      (c.triage_keep IS NOT NULL) DESC,       -- Agent 가 판단한 것 먼저
      (c.evidence_found IS FALSE) DESC,       -- 그중 문서 보강이 필요한 것 먼저
      c.size DESC
    LIMIT :limit
    """
)


@router.get("/topics", response_model=list[TopicRow])
def list_topics(
    session: DbSession, customer_id: int, limit: int = Query(default=20, le=200)
) -> list[TopicRow]:
    rows = session.execute(_TOPICS_SQL, {"customer_id": customer_id, "limit": limit}).mappings()
    return [TopicRow(**dict(r)) for r in rows]
