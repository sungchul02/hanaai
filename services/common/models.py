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
