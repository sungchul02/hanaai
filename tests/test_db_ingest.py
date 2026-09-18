"""실제 PostgreSQL 이 필요한 수집 테스트.

  docker compose up -d && alembic upgrade head && python scripts/seed.py
  pytest -m db

DB 가 없으면 자동으로 skip 된다. 여기서 확인하는 것은 스키마 문서가 아니라 실제 동작이다:
재전송 멱등성, 미등록 이벤트 격리, 인벤토리 diff.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.common.db import get_engine, get_sessionmaker
from services.common.models import Customer, EventQuarantine, Kiosk, Peripheral, Site
from services.ingest import service
from services.ingest.schemas import EventBatchIn, EventIn, PeripheralIn, PeripheralSyncIn

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL 없음: {exc}")

    with get_sessionmaker()() as s:
        yield s


@pytest.fixture
def kiosk(session: Session) -> Iterator[Kiosk]:
    """테스트 전용 지점/키오스크. 끝나면 지운다."""
    serial = f"TEST-{uuid.uuid4().hex[:8]}"
    customer = Customer(code=f"CUST-{serial}", name="테스트 고객사")
    session.add(customer)
    session.flush()
    site = Site(customer_id=customer.customer_id, code=f"SITE-{serial}", name="테스트 지점")
    session.add(site)
    session.flush()
    k = Kiosk(serial_no=serial, site_id=site.site_id, model="TEST-K", status="active")
    session.add(k)
    session.commit()

    yield k

    session.execute(text("DELETE FROM event WHERE kiosk_id = :id"), {"id": k.kiosk_id})
    session.execute(text("DELETE FROM peripheral WHERE kiosk_id = :id"), {"id": k.kiosk_id})
    session.execute(
        text("DELETE FROM event_quarantine WHERE kiosk_serial = :s"), {"s": k.serial_no}
    )
    session.delete(k)
    session.flush()
    session.delete(site)
    session.flush()
    session.delete(customer)
    session.commit()


# 같은 source_seq 는 언제 보내든 같은 occurred_at 을 가져야 한다.
# 멱등성 키가 (occurred_at, kiosk_id, source_seq) 이므로, 재전송 시 키오스크가
# 원래 시각을 그대로 실어 보내야 중복으로 걸러진다. 여기서 now() 를 쓰면
# 재전송을 흉내내는 게 아니라 '새 이벤트'를 만드는 것이 된다.
_ORIGIN = dt.datetime(2026, 9, 1, 0, 0, tzinfo=dt.UTC)


def _batch(count: int, start_seq: int = 1) -> EventBatchIn:
    return EventBatchIn(
        events=[
            EventIn(
                occurred_at=_ORIGIN + dt.timedelta(seconds=start_seq + i),
                source_seq=start_seq + i,
                type="app.started",
            )
            for i in range(count)
        ]
    )


def test_재전송은_중복으로_흡수된다(session: Session, kiosk: Kiosk) -> None:
    """키오스크는 오프라인 후 같은 배치를 다시 보낸다. 에러가 아니라 정상 흐름이다."""
    batch = _batch(10)

    first = service.ingest_events(session, kiosk, batch)
    assert first.accepted == 10
    assert first.duplicates == 0

    second = service.ingest_events(session, kiosk, batch)
    assert second.accepted == 0
    assert second.duplicates == 10


def test_겹치는_재전송도_새것만_들어간다(session: Session, kiosk: Kiosk) -> None:
    service.ingest_events(session, kiosk, _batch(10, start_seq=1))
    result = service.ingest_events(session, kiosk, _batch(10, start_seq=6))
    assert result.accepted == 5
    assert result.duplicates == 5


def test_미등록_이벤트는_격리된다(session: Session, kiosk: Kiosk) -> None:
    batch = EventBatchIn(
        events=[
            EventIn(
                occurred_at=dt.datetime.now(dt.UTC),
                source_seq=9001,
                type="printer.does_not_exist",
            )
        ]
    )
    result = service.ingest_events(session, kiosk, batch)
    assert result.accepted == 0
    assert result.quarantined == 1

    row = session.scalar(
        select(EventQuarantine).where(EventQuarantine.kiosk_serial == kiosk.serial_no)
    )
    assert row is not None
    assert "unknown event_type" in row.reason


def test_미래_시각_이벤트는_격리된다(session: Session, kiosk: Kiosk) -> None:
    batch = EventBatchIn(
        events=[
            EventIn(
                occurred_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
                source_seq=9100,
                type="app.started",
            )
        ]
    )
    result = service.ingest_events(session, kiosk, batch)
    assert result.quarantined == 1


def _peripheral(slot: str, model_name: str, serial: str) -> PeripheralIn:
    return PeripheralIn(
        slot=slot,
        kind="printer",
        vendor="TestVendor",
        model_name=model_name,
        driver_version="1.0.0",
        serial_no=serial,
    )


def test_인벤토리_diff_가_장착_이력을_만든다(session: Session, kiosk: Kiosk) -> None:
    first = service.sync_peripherals(
        session, kiosk, PeripheralSyncIn(peripherals=[_peripheral("USB1", "P-100", "SN-1")])
    )
    assert first.attached == 1

    # 같은 보고를 다시 하면 아무 일도 없어야 한다
    again = service.sync_peripherals(
        session, kiosk, PeripheralSyncIn(peripherals=[_peripheral("USB1", "P-100", "SN-1")])
    )
    assert (again.attached, again.detached, again.unchanged) == (0, 0, 1)

    # 같은 슬롯에 다른 개체를 꽂으면 탈착 + 장착
    swapped = service.sync_peripherals(
        session, kiosk, PeripheralSyncIn(peripherals=[_peripheral("USB1", "P-100", "SN-2")])
    )
    assert (swapped.attached, swapped.detached) == (1, 1)

    rows = list(session.scalars(select(Peripheral).where(Peripheral.kiosk_id == kiosk.kiosk_id)))
    assert len(rows) == 2
    assert sum(1 for r in rows if r.detached_at is None) == 1


def test_보고에서_빠진_장치는_탈착된다(session: Session, kiosk: Kiosk) -> None:
    service.sync_peripherals(
        session, kiosk, PeripheralSyncIn(peripherals=[_peripheral("USB1", "P-100", "SN-1")])
    )
    result = service.sync_peripherals(session, kiosk, PeripheralSyncIn(peripherals=[]))
    assert result.detached == 1
