"""수집 API.

키오스크 → 서버 방향만 담당한다. 조회/관리는 services/ops_api 가 맡는다.
두 서비스를 나눈 이유: 수집은 가용성이, 운영은 권한 통제가 중요해서 요구사항이 다르다.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, status

from services.common.config import get_settings
from services.common.health import build_health_router
from services.common.logging import configure_logging
from services.ingest import service
from services.ingest.deps import CurrentKiosk, DbSession
from services.ingest.schemas import (
    EventBatchIn,
    IngestResult,
    PeripheralSyncIn,
    PeripheralSyncResult,
)

configure_logging()

app = FastAPI(
    title="HANAAI Ingest API",
    version="0.1.0",
    description="키오스크 이벤트 로그 및 주변장치 인벤토리 수집",
)
app.include_router(build_health_router())


@app.post("/v1/ingest/events", response_model=IngestResult)
def ingest_events(batch: EventBatchIn, kiosk: CurrentKiosk, session: DbSession) -> IngestResult:
    """이벤트 배치 수집. 재전송으로 인한 중복은 정상 응답으로 흡수된다."""
    max_batch = get_settings().ingest_max_batch
    if len(batch.events) > max_batch:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"batch too large: {len(batch.events)} > {max_batch}",
        )
    return service.ingest_events(session, kiosk, batch)


@app.put("/v1/ingest/peripherals", response_model=PeripheralSyncResult)
def sync_peripherals(
    payload: PeripheralSyncIn, kiosk: CurrentKiosk, session: DbSession
) -> PeripheralSyncResult:
    """부팅 시 현재 장착 상태 '전체'를 보고한다. 서버가 diff 해서 장착 이력을 만든다."""
    return service.sync_peripherals(session, kiosk, payload)
