"""수집 스키마 테스트. DB 없이 검증 규칙만 본다."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from services.ingest.schemas import EventBatchIn, EventIn, PeripheralIn, PeripheralSyncIn

NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.UTC)


def test_시간대_없는_시각은_거부된다() -> None:
    """키오스크가 로컬 시각을 그냥 보내면 나중에 시계열 비교가 전부 어긋난다."""
    with pytest.raises(ValidationError):
        EventIn(occurred_at=dt.datetime(2026, 9, 18, 12, 0), source_seq=1, type="app.started")


def test_모르는_필드는_거부된다() -> None:
    with pytest.raises(ValidationError):
        EventIn(occurred_at=NOW, source_seq=1, type="app.started", extra_field="x")


def test_음수_시퀀스는_거부된다() -> None:
    with pytest.raises(ValidationError):
        EventIn(occurred_at=NOW, source_seq=-1, type="app.started")


def test_빈_배치는_거부된다() -> None:
    with pytest.raises(ValidationError):
        EventBatchIn(events=[])


def test_severity_는_생략_가능하다() -> None:
    """생략하면 서버가 사전의 default_sev 를 쓴다. 키오스크가 매번 정할 필요 없다."""
    event = EventIn(occurred_at=NOW, source_seq=1, type="app.started")
    assert event.severity is None


def test_주변장치는_슬롯으로_보고한다() -> None:
    """키오스크는 서버의 peripheral_id 를 모른다. 서버가 슬롯으로 해석한다."""
    event = EventIn(occurred_at=NOW, source_seq=1, type="printer.paper_jam", peripheral_slot="USB1")
    assert event.peripheral_slot == "USB1"
    assert not hasattr(event, "peripheral_id")


def test_모르는_장치_종류는_거부된다() -> None:
    with pytest.raises(ValidationError):
        PeripheralIn(slot="USB1", kind="coffee_machine", vendor="X", model_name="Y")


def test_인벤토리는_전체_상태를_보고한다() -> None:
    """빈 목록도 정상 입력이다. '아무 장치도 없다'는 뜻이고, 서버는 전부 탈착 처리한다."""
    payload = PeripheralSyncIn(peripherals=[])
    assert payload.peripherals == []
