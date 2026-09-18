"""ORM 모델.

주의: 스키마의 원본은 db/migrations/sql/*.sql 이다. 이 파일은 그 DDL 을 따라가는 매핑이며,
여기서 컬럼을 바꾼다고 DB 가 바뀌지 않는다. 스키마를 바꾸려면 새 마이그레이션을 추가하고
이 파일을 맞춰라. 어긋나면 tests/test_db_ingest.py 같은 -m db 테스트에서 드러난다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# DDL 이 이미 만든 타입이므로 create_type=False. SQLAlchemy 가 중복 생성하지 않게 한다.
KioskStatus = ENUM(
    "provisioning",
    "active",
    "maintenance",
    "retired",
    name="kiosk_status",
    create_type=False,
)
PeripheralKind = ENUM(
    "printer",
    "scanner",
    "card_reader",
    "cash_acceptor",
    "nfc",
    "pinpad",
    "camera",
    "display",
    "speaker",
    "other",
    name="peripheral_kind",
    create_type=False,
)
ProposalStatus = ENUM(
    "draft",
    "pending_review",
    "approved",
    "rejected",
    "superseded",
    "implemented",
    name="proposal_status",
    create_type=False,
)
RunStatus = ENUM(
    "running",
    "succeeded",
    "failed",
    "abandoned",
    name="run_status",
    create_type=False,
)

_NOW = text("now()")
TZDateTime = DateTime(timezone=True)


# ------------------------------------------------------------------ 데이터 레이어


class Customer(Base):
    """키오스크를 납품받는 고객사. 플릿은 이 단위로 갈린다."""

    __tablename__ = "customer"

    customer_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    contact: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    sites: Mapped[list[Site]] = relationship(back_populates="customer")


class Site(Base):
    __tablename__ = "site"

    site_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.customer_id"))
    # 지점 코드는 고객사 안에서만 유일하다 (DDL 의 UNIQUE (customer_id, code))
    code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, server_default=text("'Asia/Seoul'"))
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    customer: Mapped[Customer] = relationship(back_populates="sites")
    kiosks: Mapped[list[Kiosk]] = relationship(back_populates="site")


class Kiosk(Base):
    __tablename__ = "kiosk"

    kiosk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    serial_no: Mapped[str] = mapped_column(Text, unique=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.site_id"))
    model: Mapped[str] = mapped_column(Text)
    os_version: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(KioskStatus, server_default=text("'provisioning'"))
    installed_at: Mapped[dt.date | None] = mapped_column(Date)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    updated_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)

    site: Mapped[Site] = relationship(back_populates="kiosks")
    peripherals: Mapped[list[Peripheral]] = relationship(back_populates="kiosk")


class PeripheralModel(Base):
    """주변장치 모델 카탈로그. 개체(Peripheral)와 분리되어 있다."""

    __tablename__ = "peripheral_model"

    peripheral_model_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(PeripheralKind)
    vendor: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(Text)
    driver_name: Mapped[str | None] = mapped_column(Text)
    driver_version: Mapped[str] = mapped_column(Text, server_default=text("''"))
    protocol: Mapped[str | None] = mapped_column(Text)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    eol_date: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)


class Peripheral(Base):
    """특정 키오스크에 장착된 개체. detached_at 으로 이력이 남는다."""

    __tablename__ = "peripheral"

    peripheral_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosk.kiosk_id"))
    peripheral_model_id: Mapped[int] = mapped_column(
        ForeignKey("peripheral_model.peripheral_model_id")
    )
    slot: Mapped[str] = mapped_column(Text)
    serial_no: Mapped[str | None] = mapped_column(Text)
    firmware_version: Mapped[str | None] = mapped_column(Text)
    attached_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    detached_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))

    kiosk: Mapped[Kiosk] = relationship(back_populates="peripherals")
    model: Mapped[PeripheralModel] = relationship()


class EventType(Base):
    """이벤트 사전. 여기 없는 code 는 수집되지 않고 격리된다."""

    __tablename__ = "event_type"

    event_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    category: Mapped[str] = mapped_column(Text)
    default_sev: Mapped[int] = mapped_column(SmallInteger)
    description: Mapped[str] = mapped_column(Text)
    is_actionable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Event(Base):
    """append-only. 실제 테이블은 occurred_at 기준 월 RANGE 파티션이다."""

    __tablename__ = "event"

    occurred_at: Mapped[dt.datetime] = mapped_column(TZDateTime, primary_key=True)
    kiosk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    peripheral_id: Mapped[int | None] = mapped_column(BigInteger)
    event_type_id: Mapped[int] = mapped_column(ForeignKey("event_type.event_type_id"))
    severity: Mapped[int] = mapped_column(SmallInteger)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
    # 이벤트 시점의 앱 버전. kiosk.app_version 은 현재 값이라 과거를 설명하지 못한다.
    app_version: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))


class EventQuarantine(Base):
    """검증 실패한 원본. 버리지 않고 남겨서 사전 누락을 발견하는 통로로 쓴다."""

    __tablename__ = "event_quarantine"

    quarantine_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    received_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    kiosk_serial: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)


class EventRollupHourly(Base):
    """분석 Agent 의 실제 입력. peripheral_model_id = 0 은 '장치 무관'."""

    __tablename__ = "event_rollup_hourly"

    bucket: Mapped[dt.datetime] = mapped_column(TZDateTime, primary_key=True)
    kiosk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peripheral_model_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("0")
    )
    app_version: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("''"))
    event_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    duration_p50_ms: Mapped[int | None] = mapped_column(Integer)
    duration_p95_ms: Mapped[int | None] = mapped_column(Integer)
    distinct_sessions: Mapped[int | None] = mapped_column(Integer)


# ------------------------------------------------------------------ Agent 루프


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    analysis_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    window_start: Mapped[dt.datetime] = mapped_column(TZDateTime)
    window_end: Mapped[dt.datetime] = mapped_column(TZDateTime)
    detector_version: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    candidate_count: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(RunStatus, server_default=text("'running'"))
    error: Mapped[str | None] = mapped_column(Text)

    proposals: Mapped[list[FeatureProposal]] = relationship(back_populates="analysis_run")


class FeatureProposal(Base):
    """분석 Agent 의 산출물이자 개발 Agent 의 입력. 두 Agent 사이의 유일한 계약."""

    __tablename__ = "feature_proposal"

    proposal_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.analysis_run_id"))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    scope: Mapped[str] = mapped_column(Text)
    impact_score: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(ProposalStatus, server_default=text("'draft'"))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    review_note: Mapped[str | None] = mapped_column(Text)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="proposals")
    dev_runs: Mapped[list[DevRun]] = relationship(back_populates="proposal")


class DevRun(Base):
    __tablename__ = "dev_run"

    dev_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("feature_proposal.proposal_id"))
    started_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
    branch: Mapped[str | None] = mapped_column(Text)
    pr_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(RunStatus, server_default=text("'running'"))
    files_changed: Mapped[int | None] = mapped_column(Integer)
    lines_changed: Mapped[int | None] = mapped_column(Integer)
    test_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    guardrail_hits: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'"))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    proposal: Mapped[FeatureProposal] = relationship(back_populates="dev_runs")


class Deployment(Base):
    __tablename__ = "deployment"

    deployment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("feature_proposal.proposal_id"))
    dev_run_id: Mapped[int | None] = mapped_column(ForeignKey("dev_run.dev_run_id"))
    version: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(TZDateTime, server_default=_NOW)
    kiosk_count: Mapped[int | None] = mapped_column(Integer)
    baseline: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    observed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verdict: Mapped[str | None] = mapped_column(Text)
    rolled_back_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)


class DeploymentTarget(Base):
    """카나리 그룹과 대조군. 배포 후 지표 비교의 기준이 된다."""

    __tablename__ = "deployment_target"

    deployment_id: Mapped[int] = mapped_column(
        ForeignKey("deployment.deployment_id", ondelete="CASCADE"), primary_key=True
    )
    kiosk_id: Mapped[int] = mapped_column(ForeignKey("kiosk.kiosk_id"), primary_key=True)
    is_control: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    applied_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime)
