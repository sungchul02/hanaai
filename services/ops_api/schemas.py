from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

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
    junk_by_llm_7d: int
    menus_published: int
    menus_from_ai: int
    proposals_pending: int
    last_analysis_at: dt.datetime | None
    # 마지막 분석이 실제로 한 일. 화면의 단계 표시는 이 값만 쓴다.
    last_run_stats: dict[str, Any] | None = None
    last_run_questions: int | None = None


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
    verdict_reason: str | None = None


class TopicRow(BaseModel):
    """많이 나온 주제. 분석이 묶어놓은 결과를 그대로 보여준다."""

    cluster_id: int
    label: str
    size: int
    unanswered: int
    keywords: list[str]
    covered_menu_id: int | None
    covered_menu_title: str | None
    has_proposal: bool
    # 상위 Agent 의 판단. None 은 아직 판단 대상이 아니었다는 뜻이다.
    triage_keep: bool | None = None
    triage_reason: str | None = None
    # 하위 Agent 의 조사 결과
    evidence_found: bool | None = None
    evidence_summary: str | None = None
    evidence_missing: list[str] = []


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
    # 같은 주제의 메뉴가 이미 있을 때 무엇을 할지. 화면이 물어보고 관리자가 고른다.
    #   replace: 기존 메뉴를 이 내용으로 교체   merge: 기존 본문 뒤에 덧붙임
    #   new:     별도 메뉴로 추가 (중복을 감수하고 만드는 것)
    on_duplicate: Literal["replace", "merge", "new"] = "replace"
    # 어느 메뉴와 겹치는지. 비우면 서버가 제목으로 찾는다.
    duplicate_of: int | None = None


class DuplicateHint(BaseModel):
    """이 제안과 겹치는 기존 메뉴. 승인 화면이 [추가하기] 전에 보여준다.

    중복 확인을 서버에 두는 이유: 관리자가 어느 메뉴와 겹치는지 눈으로 찾게 두면
    빠뜨린다. 실제로 '증명서 발급 수수료' 메뉴가 네 개까지 늘었다.
    """

    menu_id: int
    title: str
    body: str
    keywords: list[str]
    similarity: float


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


class DocumentRow(BaseModel):
    """근거 문서 한 건. AI 가 안내문을 쓸 때 참고하는 원본이다."""

    document_id: int
    title: str
    url: str | None
    note: str | None
    fetched_at: dt.datetime | None
    chunk_count: int


class EvidenceRow(BaseModel):
    """제안이 근거로 삼은 문서 조각. 출처를 눌러 원문을 확인할 수 있어야 한다."""

    chunk_id: int
    document_title: str
    document_url: str | None
    heading: str | None
    score: Decimal | None
    quote: str | None


class TodoSummary(BaseModel):
    """관리자가 지금 눌러야 할 것. 행동으로 이어지는 숫자만 담는다."""

    pending_review: int
    needs_document: int
    duplicate_menus: int
    unanalyzed: int
    # 승인된 제안 중 AI 초안을 그대로 쓴 것과 손본 것.
    # 이 비율이 초안 품질 지표다. 손보는 비율이 높아지면 프롬프트를 고쳐야 한다.
    approved_as_is: int = 0
    approved_edited: int = 0


class RunRow(BaseModel):
    """분석 이력 한 줄. 비용까지 함께 보여 버튼의 무게를 알게 한다."""

    analysis_run_id: int
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: str
    model: str | None
    questions_seen: int
    clusters_found: int
    proposals_made: int
    stats: dict[str, Any]
    llm_calls: int | None = None
    cost_usd: float | None = None
    error: str | None = None
