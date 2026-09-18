from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from services.common.db import get_session
from services.common.models import Event, EventType, Kiosk, Peripheral, Site
from services.ops_api.schemas import (
    EventRow,
    KioskDetail,
    KioskSummary,
    PeripheralSummary,
)

router = APIRouter(prefix="/v1/kiosks", tags=["kiosks"])
DbSession = Annotated[Session, Depends(get_session)]


@router.get("", response_model=list[KioskSummary])
def list_kiosks(
    session: DbSession,
    customer_id: int | None = Query(default=None, description="고객사 단위로 플릿 조회"),
    site_id: int | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    stale_minutes: int | None = Query(
        default=None, description="이 시간 이상 보고가 없는 키오스크만"
    ),
    limit: int = Query(default=100, le=500),
) -> list[Kiosk]:
    stmt = select(Kiosk).order_by(Kiosk.kiosk_id).limit(limit)
    if customer_id is not None:
        # TODO(M2): 지금은 질의 파라미터다. 운영자 인증이 붙으면 이 값을 토큰에서 강제해
        # A고객사 운영자가 B고객사 플릿을 볼 수 없게 해야 한다.
        stmt = stmt.join(Site, Site.site_id == Kiosk.site_id).where(Site.customer_id == customer_id)
    if site_id is not None:
        stmt = stmt.where(Kiosk.site_id == site_id)
    if status_filter is not None:
        stmt = stmt.where(Kiosk.status == status_filter)
    if stale_minutes is not None:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=stale_minutes)
        stmt = stmt.where((Kiosk.last_seen_at.is_(None)) | (Kiosk.last_seen_at < cutoff))
    return list(session.scalars(stmt))


@router.get("/{serial_no}", response_model=KioskDetail)
def get_kiosk(serial_no: str, session: DbSession) -> KioskDetail:
    kiosk = session.scalar(
        select(Kiosk)
        .where(Kiosk.serial_no == serial_no)
        .options(
            selectinload(Kiosk.site).selectinload(Site.customer),
            selectinload(Kiosk.peripherals).selectinload(Peripheral.model),
        )
    )
    if kiosk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown kiosk: {serial_no}")

    active = [p for p in kiosk.peripherals if p.detached_at is None]
    return KioskDetail(
        kiosk_id=kiosk.kiosk_id,
        serial_no=kiosk.serial_no,
        site_id=kiosk.site_id,
        site_code=kiosk.site.code,
        site_name=kiosk.site.name,
        customer_id=kiosk.site.customer_id,
        customer_code=kiosk.site.customer.code,
        customer_name=kiosk.site.customer.name,
        model=kiosk.model,
        status=kiosk.status,
        app_version=kiosk.app_version,
        os_version=kiosk.os_version,
        installed_at=kiosk.installed_at,
        last_seen_at=kiosk.last_seen_at,
        peripherals=[
            PeripheralSummary(
                peripheral_id=p.peripheral_id,
                slot=p.slot,
                kind=p.model.kind,
                vendor=p.model.vendor,
                model_name=p.model.model_name,
                driver_version=p.model.driver_version,
                firmware_version=p.firmware_version,
                attached_at=p.attached_at,
            )
            for p in active
        ],
    )


@router.get("/{serial_no}/events", response_model=list[EventRow])
def search_events(
    serial_no: str,
    session: DbSession,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    event_type: str | None = None,
    min_severity: int = Query(default=0, ge=0, le=50),
    limit: int = Query(default=200, le=2000),
) -> list[EventRow]:
    kiosk = session.scalar(select(Kiosk).where(Kiosk.serial_no == serial_no))
    if kiosk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown kiosk: {serial_no}")

    # occurred_at 조건을 반드시 붙여 파티션 프루닝이 걸리게 한다.
    # 없으면 전체 파티션을 훑는다.
    since = since or dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    stmt = (
        select(Event, EventType.code)
        .join(EventType, EventType.event_type_id == Event.event_type_id)
        .where(
            Event.kiosk_id == kiosk.kiosk_id,
            Event.occurred_at >= since,
            Event.severity >= min_severity,
        )
        .order_by(Event.occurred_at.desc())
        .limit(limit)
    )
    if until is not None:
        stmt = stmt.where(Event.occurred_at < until)
    if event_type is not None:
        stmt = stmt.where(EventType.code == event_type)

    return [
        EventRow(
            occurred_at=e.occurred_at,
            kiosk_serial=serial_no,
            type=code,
            severity=e.severity,
            error_code=e.error_code,
            duration_ms=e.duration_ms,
            session_id=e.session_id,
            payload=e.payload,
        )
        for e, code in session.execute(stmt).all()
    ]
