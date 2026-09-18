"""ORM 모델.

주의: 스키마의 원본은 db/migrations/sql/*.sql 이다. 이 파일은 그 DDL 을 따라가는 매핑이며,
여기서 컬럼을 바꾼다고 DB 가 바뀌지 않는다. 스키마를 바꾸려면 새 마이그레이션을 추가하고
이 파일을 맞춰라.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# DDL 이 이미 만든 타입이므로 create_type=False. SQLAlchemy 가 중복 생성하지 않게 한다.
KioskStatus = ENUM(
    "provisioning", "active", "maintenance", "retired", name="kiosk_status", create_type=False
)
QuestionVerdict = ENUM(
    "pending",
    "useful",
    "irrelevant",
    "abusive",
    "too_short",
    name="question_verdict",
    create_type=False,
)
AnswerSource = ENUM(
    "cms_menu",
    "low_confidence",
    "fallback",
    "none",
    name="answer_source",
    create_type=False,
)
MenuStatus = ENUM("draft", "published", "archived", name="menu_status", create_type=False)
ClusterReviewStatus = ENUM(
    "pending_review",
    "approved",
    "rejected",
    "answered",
    name="cluster_review_status",
    create_type=False,
)
DocumentKind = ENUM("web", "manual", "upload", name="document_kind", create_type=False)
ProposalStatus = ENUM(
    "pending_review",
    "approved",
    "edited",
    "ignored",
    "superseded",
    name="proposal_status",
    create_type=False,
)

_NOW = text("now()")
TZDateTime = DateTime(timezone=True)
TextArray = ARRAY(Text)


# ------------------------------------------------------------------ 플릿


class Customer(Base):
    """키오스크를 납품받는 고객사. CMS 콘텐츠도 이 단위로 갈린다."""

    __tablename__ = "customer"

    customer_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    # 고객사별 조정값. 등록부는 agents/analyst/tuning.py
    tuning: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))

    sites: Mapped[list[Site]] = relationship(back_populates="customer")


class Site(Base):
    __tablename__ = "site"

    site_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.customer_id"))
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    customer: Mapped[Customer] = relationship(back_populates="sites")
    kiosks: Mapped[list[Kiosk]] = relationship(back_populates="site")


class Kiosk(Base):
    __tablename__ = "kiosk"

    kiosk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial_no: Mapped[str] = mapped_column(Text, unique=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.site_id"))
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(KioskStatus, server_default=text("'active'"))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    site: Mapped[Site] = relationship(back_populates="kiosks")


# ------------------------------------------------------------------ CMS


class CmsMenu(Base):
    """지금 키오스크가 답할 수 있는 안내 콘텐츠.

    분석에서 '이미 있는 것' 의 기준이 된다. 여기 없는 주제가 반복해서 물어지면
    그게 곧 새 메뉴 후보다.
    """

    __tablename__ = "cms_menu"

    menu_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.customer_id"))
    code: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(MenuStatus, server_default=text("'published'"))
    origin: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)


# ------------------------------------------------------------------ 지식 저장소


class SourceDocument(Base):
    """근거 문서. 출처(url)를 반드시 남긴다.

    관리자가 "이 안내가 맞는지" 확인할 수 있어야 하고, 원문이 바뀌었을 때
    다시 가져올 곳도 필요하다.
    """

    __tablename__ = "source_document"

    document_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.customer_id"))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(DocumentKind, server_default=text("'web'"))
    note: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """검색 단위. 문서를 통째로 LLM 에 넣으면 비용도 정확도도 나빠진다."""

    __tablename__ = "document_chunk"

    chunk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.document_id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    text_body: Mapped[str] = mapped_column("text", Text)
    keywords: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    document: Mapped[SourceDocument] = relationship(back_populates="chunks")


class ProposalEvidence(Base):
    """제안이 어느 근거에서 나왔는지. 관리자가 출처를 눌러 확인한다."""

    __tablename__ = "proposal_evidence"

    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("content_proposal.proposal_id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("document_chunk.chunk_id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    quote: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------------ 질문 로그


class QuestionLog(Base):
    """이 시스템의 원재료. 사용자가 던진 질문과 그때 나간 답."""

    __tablename__ = "question_log"

    question_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asked_at: Mapped[dt.datetime] = mapped_column(TZDateTime)
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosk.kiosk_id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    question_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str | None] = mapped_column(Text)
    answer_source: Mapped[str] = mapped_column(AnswerSource, server_default=text("'none'"))
    matched_menu_id: Mapped[int | None] = mapped_column(ForeignKey("cms_menu.menu_id"))
    # 매칭 확신도. 임계값을 조정할 때 과거 데이터로 검증하려면 남겨둬야 한다.
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    response_ms: Mapped[int | None] = mapped_column(Integer)
    input_mode: Mapped[str] = mapped_column(Text, server_default=text("'touch'"))
    verdict: Mapped[str] = mapped_column(QuestionVerdict, server_default=text("'pending'"))
    verdict_reason: Mapped[str | None] = mapped_column(Text)
    cluster_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)


# ------------------------------------------------------------------ 분석


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    analysis_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    window_start: Mapped[dt.datetime] = mapped_column(TZDateTime)
    window_end: Mapped[dt.datetime] = mapped_column(TZDateTime)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customer.customer_id"))
    analyzer_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    questions_seen: Mapped[int | None] = mapped_column(Integer)
    clusters_found: Mapped[int | None] = mapped_column(Integer)
    proposals_made: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 무엇을 왜 건너뛰었는지. 추천이 0건일 때 화면이 이유를 설명하는 데 쓴다.
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    error: Mapped[str | None] = mapped_column(Text)


class QuestionCluster(Base):
    """비슷한 질문을 묶은 주제."""

    __tablename__ = "question_cluster"

    cluster_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.analysis_run_id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.customer_id"))
    label: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    unanswered: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    covered_menu_id: Mapped[int | None] = mapped_column(ForeignKey("cms_menu.menu_id"))
    coverage_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    keywords: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    # 상위 Agent 의 판단. NULL 은 분류 대상이 아니었다는 뜻이다.
    triage_keep: Mapped[bool | None] = mapped_column(Boolean)
    triage_reason: Mapped[str | None] = mapped_column(Text)
    # 하위 Agent 의 조사 결과
    evidence_found: Mapped[bool | None] = mapped_column(Boolean)
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    evidence_missing: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
    # 관리자 승인 관문. 1단계(분류)와 2단계(생성) 사이에 있다.
    category: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str | None] = mapped_column(ClusterReviewStatus)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)


class QuestionClusterMember(Base):
    __tablename__ = "question_cluster_member"

    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("question_cluster.cluster_id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("question_log.question_id", ondelete="CASCADE"), primary_key=True
    )
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))


# ------------------------------------------------------------------ 추천 콘텐츠


class ContentProposal(Base):
    """AI 가 만든 새 FAQ/메뉴 초안. 승인 전까지 키오스크에 나가지 않는다."""

    __tablename__ = "content_proposal"

    proposal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.analysis_run_id"))
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("question_cluster.cluster_id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.customer_id"))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
    sample_questions: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
    question_count: Mapped[int] = mapped_column(Integer)
    impact_score: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    contract: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(ProposalStatus, server_default=text("'pending_review'"))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    review_note: Mapped[str | None] = mapped_column(Text)
    applied_menu_id: Mapped[int | None] = mapped_column(ForeignKey("cms_menu.menu_id"))

    # 콘텐츠 작성 Agent 가 초안을 실제 매처에 넣어본 결과.
    # revisions 가 1 이상이면 Agent 가 검증 후 스스로 고친 것이다.
    verified_coverage: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    verified_matched: Mapped[int | None] = mapped_column(Integer)
    verified_total: Mapped[int | None] = mapped_column(Integer)
    revisions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    remaining_misses: Mapped[list[str]] = mapped_column(TextArray, server_default=text("'{}'"))
