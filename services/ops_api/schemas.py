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
    weak_7d: int
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
    match_score: float | None
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
    # Agent 가 초안을 실제 매처에 넣어본 결과. 관리자가 승인 여부를 판단할 근거다.
    verified_coverage: float | None
    verified_matched: int | None
    verified_total: int | None
    revisions: int
    remaining_misses: list[str]


class MenuCreateIn(BaseModel):
    """관리자가 직접 만드는 메뉴.

    AI 추천을 기다리지 않고 바로 쓸 수 있어야 한다. 이미 아는 안내는 그냥 적으면 되고,
    AI 는 '관리자가 미처 몰랐던 공백' 을 찾는 데 쓴다. 둘은 경쟁 관계가 아니다.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: int
    title: str = Field(min_length=2, max_length=40)
    body: str = Field(min_length=10, max_length=500)
    keywords: list[str] = Field(min_length=1, max_length=10)
    code: str | None = Field(default=None, description="비우면 제목에서 만든다")
    status: str = Field(default="published", pattern="^(draft|published|archived)$")


class MenuUpdateIn(BaseModel):
    """메뉴 수정. 준 항목만 바뀐다.

    키워드 추가가 특히 중요하다. "차 세울 데 있나요?" 처럼 콘텐츠는 있는데 말이 안 겹쳐
    못 잡는 질문은, 새 메뉴가 아니라 키워드 한 줄로 해결된다.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=2, max_length=40)
    body: str | None = Field(default=None, min_length=10, max_length=500)
    keywords: list[str] | None = Field(default=None, max_length=10)
    status: str | None = Field(default=None, pattern="^(draft|published|archived)$")


class MenuPreviewIn(BaseModel):
    """저장하기 전에 '이 메뉴가 어떤 질문을 잡는지' 미리 본다.

    AI 추천에는 검증 수치가 붙는데 관리자가 직접 쓴 것에는 없으면 앞뒤가 안 맞는다.
    같은 도구로 같은 숫자를 보여준다.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: int
    title: str = Field(min_length=1, max_length=40)
    body: str = Field(default="", max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    menu_id: int | None = Field(default=None, description="수정 중이면 자기 자신은 제외")
    days: int = Field(default=7, ge=1, le=90)


class MenuPreviewOut(BaseModel):
    total: int
    by_draft: int
    by_existing: int
    coverage: float
    samples: list[str]
    misses: list[str]


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
    # 추천이 0건일 때 "왜" 를 화면이 설명할 수 있어야 한다.
    stats: dict[str, int]
