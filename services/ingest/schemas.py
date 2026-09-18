from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# DDL 의 peripheral_kind ENUM 과 같은 목록이어야 한다. (db/migrations/sql/0001_initial_up.sql)
PeripheralKindLiteral = Literal[
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
]


class EventIn(BaseModel):
    """키오스크가 올리는 이벤트 1건.

    peripheral 은 id 가 아니라 slot 으로 보낸다. 키오스크는 서버의 peripheral_id 를 모르고,
    알 필요도 없다. 서버가 현재 장착 상태를 보고 해석한다.
    """

    model_config = ConfigDict(extra="forbid")

    occurred_at: AwareDatetime
    source_seq: int = Field(
        ge=0,
        description=(
            "키오스크 로컬 단조증가 시퀀스. 재전송 시 occurred_at 과 함께 "
            "원래 값을 그대로 보내야 중복으로 걸러진다."
        ),
    )
    type: str = Field(min_length=1, max_length=120, description="event_type.code")
    severity: int | None = Field(default=None, description="없으면 사전의 default_sev 사용")
    peripheral_slot: str | None = Field(default=None, max_length=64)
    session_id: uuid.UUID | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EventIn] = Field(min_length=1)
    app_version: str | None = None
    os_version: str | None = None


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    quarantined: int
    partitions_ensured: list[str] = Field(default_factory=list)


class PeripheralIn(BaseModel):
    """부팅 시 보고하는 장착 장치 1개."""

    model_config = ConfigDict(extra="forbid")

    slot: str = Field(min_length=1, max_length=64)
    kind: PeripheralKindLiteral
    vendor: str
    model_name: str
    driver_name: str | None = None
    driver_version: str = ""
    protocol: str | None = None
    serial_no: str | None = None
    firmware_version: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class PeripheralSyncIn(BaseModel):
    """현재 장착 상태 '전체'. 서버가 기존 활성 레코드와 diff 한다."""

    model_config = ConfigDict(extra="forbid")

    peripherals: list[PeripheralIn]
    reported_at: AwareDatetime | None = None


class PeripheralSyncResult(BaseModel):
    attached: int
    detached: int
    unchanged: int
    models_created: int


class HealthResult(BaseModel):
    status: str
    env: str
    database: str
    checked_at: dt.datetime
