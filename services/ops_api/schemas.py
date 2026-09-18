from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KioskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kiosk_id: int
    serial_no: str
    site_id: int
    model: str
    status: str
    app_version: str | None
    last_seen_at: dt.datetime | None


class PeripheralSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    peripheral_id: int
    slot: str
    kind: str
    vendor: str
    model_name: str
    driver_version: str
    firmware_version: str | None
    attached_at: dt.datetime


class KioskDetail(KioskSummary):
    customer_id: int
    customer_code: str
    customer_name: str
    site_code: str
    site_name: str
    os_version: str | None
    installed_at: dt.date | None
    peripherals: list[PeripheralSummary]


class EventRow(BaseModel):
    occurred_at: dt.datetime
    kiosk_serial: str
    type: str
    severity: int
    error_code: str | None
    duration_ms: int | None
    session_id: uuid.UUID | None
    payload: dict[str, Any]


class PeripheralHealthRow(BaseModel):
    """장치 '모델' 단위 집계. 개체 단위로는 답할 수 없는 질문에 답한다."""

    peripheral_model_id: int
    kind: str
    vendor: str
    model_name: str
    driver_version: str
    installed_count: int
    event_count: int
    error_count: int
    error_rate: float | None


class FleetSummary(BaseModel):
    """대시보드 상단 카드용 집계. 화면 한 번에 필요한 수치를 한 쿼리로 모은다."""

    customers: int
    sites: int
    kiosks: int
    kiosks_stale: int
    events_24h: int
    errors_24h: int
    quarantined_24h: int
    proposals_pending: int
    proposals_approved: int


class CustomerRow(BaseModel):
    customer_id: int
    code: str
    name: str
    status: str
    sites: int
    kiosks: int


class ProposalSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proposal_id: int
    created_at: dt.datetime
    title: str
    scope: str
    impact_score: Decimal
    status: str


class ProposalDetail(ProposalSummary):
    body: dict[str, Any]
    analysis_run_id: int
    reviewed_by: str | None
    reviewed_at: dt.datetime | None
    review_note: str | None


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, description="승인자 식별자")
    note: str | None = None
