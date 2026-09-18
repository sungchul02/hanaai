from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CustomerRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: int
    code: str
    name: str


class DashboardSummary(BaseModel):
    """화면 상단 카드. 문서의 '최근 질문 수 / 유효·불필요 질문 수 / 많이 나온 주제'."""

    questions_7d: int
    questions_today: int
    answered_7d: int
    unanswered_7d: int
    answer_rate_7d: float | None
    junk_7d: int
    menus_published: int
    menus_from_ai: int
    proposals_pending: int
    last_analysis_at: dt.datetime | None


class MenuRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    menu_id: int
    code: str
    title: str
    body: str
    keywords: list[str]
    status: str
    origin: str
    updated_at: dt.datetime


class QuestionRow(BaseModel):
    question_id: int
    asked_at: dt.datetime
    kiosk_serial: str
    question_text: str
    answer_source: str
    matched_menu_title: str | None
    verdict: str


class TopicRow(BaseModel):
    """많이 나온 주제. 분석이 묶어놓은 결과를 그대로 보여준다."""

    cluster_id: int
    label: str
    size: int
    unanswered: int
    keywords: list[str]
    covered_menu_title: str | None
    has_proposal: bool


class ProposalRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proposal_id: int
    created_at: dt.datetime
    title: str
    body: str
    reason: str
    keywords: list[str]
    sample_questions: list[str]
    question_count: int
    impact_score: Decimal
    confidence: Decimal
    status: str
    reviewed_by: str | None
    reviewed_at: dt.datetime | None
    applied_menu_id: int | None


class ApproveIn(BaseModel):
    """[추가하기] 또는 [수정] 후 추가.

    title/body/keywords 를 주면 관리자가 고친 것으로 보고 status 를 edited 로 남긴다.
    AI 초안을 그대로 썼는지 손봤는지가 나중에 품질 지표가 된다.
    """

    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=2, max_length=40)
    body: str | None = Field(default=None, min_length=10, max_length=500)
    keywords: list[str] | None = None
    note: str | None = None


class IgnoreIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1)
    note: str | None = None


class RunAnalysisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int
    days: int = Field(default=7, ge=1, le=90)
    backend: str | None = None


class RunAnalysisOut(BaseModel):
    analysis_run_id: int
    backend: str
    questions_seen: int
    clusters_found: int
    proposals_made: int
    error: str | None
