"""수집 로직.

설계상 중요한 두 가지가 여기 있다.
1. 멱등성 — 키오스크는 네트워크가 끊기면 로컬에 쌓았다가 재전송한다. 중복은 예외가 아니라
   정상이다. (occurred_at, kiosk_id, source_seq) PK + ON CONFLICT DO NOTHING 으로 흡수한다.

   여기서 키오스크 쪽에 걸리는 계약이 하나 있다: **재전송 시 원래 occurred_at 을 그대로
   실어 보내야 한다.** 보낼 때 시각을 다시 찍으면 같은 source_seq 라도 다른 행이 되어
   중복이 걸러지지 않는다. (kiosk_id, source_seq) 만으로 유니크를 걸면 좋겠지만,
   파티션 테이블의 유니크 인덱스는 파티션 키를 포함해야 해서 불가능하다.
2. 격리 — 사전에 없는 event_type 은 버리지도, 통과시키지도 않고 event_quarantine 으로 보낸다.
   이 테이블이 "키오스크 앱이 우리가 모르는 로그를 남기기 시작했다"를 발견하는 유일한 통로다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from services.common.models import (
    Event,
    EventQuarantine,
    EventType,
    Kiosk,
    Peripheral,
    PeripheralModel,
)
from services.ingest.schemas import (
    EventBatchIn,
    EventIn,
    IngestResult,
    PeripheralSyncIn,
    PeripheralSyncResult,
)

log = structlog.get_logger(__name__)

# 미래 시각 허용 폭. 키오스크 시계가 어긋나는 일은 흔하지만, 한계는 둔다.
FUTURE_TOLERANCE = dt.timedelta(hours=1)

# 프로세스 수명 동안 "이 달 파티션은 확인했다"를 기억한다.
_ensured_months: set[str] = set()


def ensure_partitions(session: Session, moments: set[dt.datetime]) -> list[str]:
    """이벤트가 속한 달의 파티션을 보장한다 (멱등).

    cron 이 없어도 수집이 스스로 파티션을 만들게 해서, 월초에 조용히 적재가 멈추는
    흔한 사고를 막는다. scripts/ensure_partitions.py 는 미리 만들어두는 용도다.
    """
    created: list[str] = []
    months = {m.date().replace(day=1) for m in moments}
    for month in sorted(months):
        key = month.strftime("%Y%m")
        if key in _ensured_months:
            continue
        name = session.execute(select(func.ensure_event_partition(month))).scalar_one()
        _ensured_months.add(key)
        created.append(str(name))
    return created


def _active_peripheral_ids(session: Session, kiosk_id: int, slots: set[str]) -> dict[str, int]:
    if not slots:
        return {}
    rows = session.execute(
        select(Peripheral.slot, Peripheral.peripheral_id).where(
            Peripheral.kiosk_id == kiosk_id,
            Peripheral.detached_at.is_(None),
            Peripheral.slot.in_(slots),
        )
    ).all()
    return {slot: pid for slot, pid in rows}


def _event_types(session: Session, codes: set[str]) -> dict[str, tuple[int, int]]:
    rows = session.execute(
        select(EventType.code, EventType.event_type_id, EventType.default_sev).where(
            EventType.code.in_(codes)
        )
    ).all()
    return {code: (type_id, sev) for code, type_id, sev in rows}


def ingest_events(session: Session, kiosk: Kiosk, batch: EventBatchIn) -> IngestResult:
    now = dt.datetime.now(dt.UTC)
    # 배치가 버전을 실어 보내면 그 값을, 아니면 마지막으로 알려진 값을 쓴다.
    app_version = batch.app_version or kiosk.app_version
    type_map = _event_types(session, {e.type for e in batch.events})
    slot_map = _active_peripheral_ids(
        session,
        kiosk.kiosk_id,
        {e.peripheral_slot for e in batch.events if e.peripheral_slot},
    )

    rows: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []

    def reject(event: EventIn, reason: str) -> None:
        quarantine.append(
            {
                "kiosk_serial": kiosk.serial_no,
                "reason": reason,
                "raw": event.model_dump(mode="json"),
            }
        )

    for event in batch.events:
        entry = type_map.get(event.type)
        if entry is None:
            reject(event, f"unknown event_type: {event.type}")
            continue
        if event.occurred_at > now + FUTURE_TOLERANCE:
            reject(event, "occurred_at is in the future")
            continue

        type_id, default_sev = entry
        peripheral_id = slot_map.get(event.peripheral_slot) if event.peripheral_slot else None
        if event.peripheral_slot and peripheral_id is None:
            # 이벤트를 버리지는 않는다. 인벤토리 보고가 아직 안 온 경우가 대부분이다.
            log.warning(
                "unknown_peripheral_slot", kiosk=kiosk.serial_no, slot=event.peripheral_slot
            )

        rows.append(
            {
                "occurred_at": event.occurred_at,
                "kiosk_id": kiosk.kiosk_id,
                "source_seq": event.source_seq,
                "peripheral_id": peripheral_id,
                "event_type_id": type_id,
                "severity": event.severity if event.severity is not None else default_sev,
                "session_id": event.session_id,
                "duration_ms": event.duration_ms,
                "error_code": event.error_code,
                "app_version": app_version,
                "payload": event.payload,
            }
        )

    partitions: list[str] = []
    accepted = 0
    if rows:
        partitions = ensure_partitions(session, {r["occurred_at"] for r in rows})
        stmt = (
            pg_insert(Event)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["occurred_at", "kiosk_id", "source_seq"])
            .returning(Event.source_seq)
        )
        accepted = len(session.execute(stmt).all())

    if quarantine:
        session.execute(pg_insert(EventQuarantine).values(quarantine))

    session.execute(
        update(Kiosk)
        .where(Kiosk.kiosk_id == kiosk.kiosk_id)
        .values(
            last_seen_at=now,
            app_version=batch.app_version or kiosk.app_version,
            os_version=batch.os_version or kiosk.os_version,
        )
    )
    session.commit()

    return IngestResult(
        accepted=accepted,
        duplicates=len(rows) - accepted,
        quarantined=len(quarantine),
        partitions_ensured=partitions,
    )


def _resolve_model_ids(session: Session, payload: PeripheralSyncIn) -> tuple[dict[str, int], int]:
    """보고된 장치들의 peripheral_model 을 보장하고 slot -> model_id 를 돌려준다."""
    created = 0
    result: dict[str, int] = {}
    for item in payload.peripherals:
        key = (item.vendor, item.model_name, item.driver_version)
        model_id = session.execute(
            select(PeripheralModel.peripheral_model_id).where(
                PeripheralModel.vendor == key[0],
                PeripheralModel.model_name == key[1],
                PeripheralModel.driver_version == key[2],
            )
        ).scalar_one_or_none()

        if model_id is None:
            model_id = session.execute(
                pg_insert(PeripheralModel)
                .values(
                    kind=item.kind,
                    vendor=item.vendor,
                    model_name=item.model_name,
                    driver_name=item.driver_name,
                    driver_version=item.driver_version,
                    protocol=item.protocol,
                    capabilities=item.capabilities,
                )
                .on_conflict_do_nothing(index_elements=["vendor", "model_name", "driver_version"])
                .returning(PeripheralModel.peripheral_model_id)
            ).scalar_one_or_none()
            if model_id is None:
                # 다른 요청이 먼저 넣었다. ON CONFLICT DO NOTHING 은 아무 행도 돌려주지 않는다.
                model_id = session.execute(
                    select(PeripheralModel.peripheral_model_id).where(
                        PeripheralModel.vendor == key[0],
                        PeripheralModel.model_name == key[1],
                        PeripheralModel.driver_version == key[2],
                    )
                ).scalar_one()
            else:
                created += 1

        result[item.slot] = model_id
    return result, created


def sync_peripherals(
    session: Session, kiosk: Kiosk, payload: PeripheralSyncIn
) -> PeripheralSyncResult:
    """부팅 시 보고된 '현재 장착 상태 전체'와 DB 를 맞춘다.

    diff 방식이라 장착 이력(attached_at / detached_at)이 별도 조작 없이 쌓인다.
    """
    now = payload.reported_at or dt.datetime.now(dt.UTC)
    model_ids, models_created = _resolve_model_ids(session, payload)
    reported = {item.slot: item for item in payload.peripherals}

    current = {
        row.slot: row
        for row in session.scalars(
            select(Peripheral).where(
                Peripheral.kiosk_id == kiosk.kiosk_id,
                Peripheral.detached_at.is_(None),
            )
        )
    }

    attached = detached = unchanged = 0

    for slot, existing in current.items():
        item = reported.get(slot)
        same_device = (
            item is not None
            and existing.peripheral_model_id == model_ids[slot]
            and existing.serial_no == item.serial_no
        )
        if same_device:
            assert item is not None  # same_device 가 참이면 item 은 None 이 아니다
            if existing.firmware_version != item.firmware_version:
                existing.firmware_version = item.firmware_version
            unchanged += 1
            continue
        # 사라졌거나, 같은 슬롯에 다른 장치가 꽂혔다
        existing.detached_at = now
        detached += 1

    # 탈착 UPDATE 를 장착 INSERT 보다 먼저 보낸다.
    # 부분 유니크 인덱스 (kiosk_id, slot) WHERE detached_at IS NULL 때문에 순서가 중요하다.
    # SQLAlchemy 의 기본 unit-of-work 는 INSERT 를 UPDATE 보다 먼저 보내므로 여기서 끊어준다.
    session.flush()

    for slot, item in reported.items():
        previous = current.get(slot)
        if previous is not None and previous.detached_at is None:
            continue  # 위 루프에서 unchanged 로 판정된 슬롯
        session.add(
            Peripheral(
                kiosk_id=kiosk.kiosk_id,
                peripheral_model_id=model_ids[slot],
                slot=slot,
                serial_no=item.serial_no,
                firmware_version=item.firmware_version,
                attached_at=now,
            )
        )
        attached += 1

    session.execute(update(Kiosk).where(Kiosk.kiosk_id == kiosk.kiosk_id).values(last_seen_at=now))
    session.commit()

    return PeripheralSyncResult(
        attached=attached,
        detached=detached,
        unchanged=unchanged,
        models_created=models_created,
    )
