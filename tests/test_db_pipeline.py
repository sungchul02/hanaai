"""수집 → 집계 → 탐지 파이프라인 end-to-end 테스트 (PostgreSQL 필요).

이 테스트가 존재하는 이유:
초기 탐지기는 같은 이벤트 타입 안에서 errors/events 를 계산했는데, 그러면
printer.print_failed 처럼 심각도가 error 인 타입은 모든 모델의 비율이 1.0 이 되어
**절대 발화하지 않았다.** 찾으려던 대상이 바로 그런 하드웨어 장애였는데도 그랬다.
스키마도 SQL 구문도 멀쩡했기 때문에 실제 데이터를 흘려보내기 전까지 드러나지 않았다.

그래서 여기서는 "나쁜 모델"을 일부러 심어두고 탐지기가 그걸 집어내는지 본다.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agents.detectors.base import Window
from agents.detectors.peripheral_error_rate import PeripheralErrorRateDetector
from services.common.db import get_engine, get_sessionmaker
from services.common.models import Customer, Kiosk, Peripheral, PeripheralModel, Site
from services.common.rollup import rollup_range
from services.ingest import service
from services.ingest.schemas import EventBatchIn, EventIn

pytestmark = pytest.mark.db

# 구간은 반드시 '현재 기준 과거' 여야 한다. 수집기가 미래 시각 이벤트를 격리하기
# 때문에 날짜를 고정하면 그 날이 지나기 전까지 테스트가 통째로 죽는다.
# 파티션도 현재 달 기준으로 만들어지므로 고정 날짜는 두 번 위험하다.
_NOW = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
WINDOW_END = _NOW
WINDOW_START = WINDOW_END - dt.timedelta(days=1)

BAD_MODEL = ("BadVendor", "JAM-9000")
GOOD_MODEL = ("GoodVendor", "SOLID-100")
FAIL_RATE = {BAD_MODEL: 0.12, GOOD_MODEL: 0.02}
ATTEMPTS = 400


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL 없음: {exc}")
    with get_sessionmaker()() as s:
        yield s


def _purge_leftovers(session: Session) -> None:
    """이전 실행이 남긴 PIPE-% 데이터를 치운다.

    공유 DB 라서 teardown 이 한 번 실패하면 그 잔여 모델이 다음 실행의 '비교 기준' 에
    섞여 들어가 엉뚱한 실패를 만든다. 실제로 그렇게 한 번 당했다.
    """
    session.execute(
        text("""
        DELETE FROM event WHERE kiosk_id IN (
            SELECT kiosk_id FROM kiosk WHERE serial_no LIKE 'PIPE-%'
        )""")
    )
    session.execute(
        text("""
        DELETE FROM event_rollup_hourly WHERE kiosk_id IN (
            SELECT kiosk_id FROM kiosk WHERE serial_no LIKE 'PIPE-%'
        )""")
    )
    session.execute(
        text("""
        DELETE FROM peripheral WHERE kiosk_id IN (
            SELECT kiosk_id FROM kiosk WHERE serial_no LIKE 'PIPE-%'
        )""")
    )
    session.execute(text("DELETE FROM kiosk WHERE serial_no LIKE 'PIPE-%'"))
    session.execute(
        text("DELETE FROM peripheral_model WHERE vendor IN ('BadVendor', 'GoodVendor')")
    )
    session.execute(
        text("""
        DELETE FROM site WHERE customer_id IN (
            SELECT customer_id FROM customer WHERE code LIKE 'PIPE-%'
        )""")
    )
    session.execute(text("DELETE FROM customer WHERE code LIKE 'PIPE-%'"))
    session.commit()


@pytest.fixture(scope="module")
def fleet(session: Session) -> Iterator[dict[str, int]]:
    """두 벤더의 프린터를 절반씩 깔아둔 소형 플릿."""
    _purge_leftovers(session)
    tag = uuid.uuid4().hex[:8]
    customer = Customer(code=f"PIPE-{tag}", name="파이프라인 테스트")
    session.add(customer)
    session.flush()
    site = Site(customer_id=customer.customer_id, code=f"S-{tag}", name="테스트 지점")
    session.add(site)
    session.flush()

    model_ids: dict[tuple[str, str], int] = {}
    for vendor, model_name in (BAD_MODEL, GOOD_MODEL):
        model = PeripheralModel(
            kind="printer", vendor=vendor, model_name=f"{model_name}-{tag}", driver_version="1.0"
        )
        session.add(model)
        session.flush()
        model_ids[(vendor, model_name)] = model.peripheral_model_id

    kiosk_ids: list[int] = []
    for i in range(4):
        kiosk = Kiosk(
            serial_no=f"PIPE-{tag}-K{i:02d}",
            site_id=site.site_id,
            model="TEST-K",
            status="active",
        )
        session.add(kiosk)
        session.flush()
        session.add(
            Peripheral(
                kiosk_id=kiosk.kiosk_id,
                # 짝수는 나쁜 모델, 홀수는 좋은 모델
                peripheral_model_id=model_ids[BAD_MODEL if i % 2 == 0 else GOOD_MODEL],
                slot="USB1",
                serial_no=f"PIPE-{tag}-K{i:02d}-USB1",
            )
        )
        kiosk_ids.append(kiosk.kiosk_id)
    session.commit()

    yield {"bad": model_ids[BAD_MODEL], "good": model_ids[GOOD_MODEL]}

    # text() 의 :ids 는 튜플로 확장되지 않는다. Postgres 에서는 = ANY 가 가장 단순하다.
    ids = list(kiosk_ids)
    session.execute(text("DELETE FROM event WHERE kiosk_id = ANY(:ids)"), {"ids": ids})
    session.execute(
        text("DELETE FROM event_rollup_hourly WHERE kiosk_id = ANY(:ids)"), {"ids": ids}
    )
    session.execute(text("DELETE FROM peripheral WHERE kiosk_id = ANY(:ids)"), {"ids": ids})
    session.execute(text("DELETE FROM kiosk WHERE kiosk_id = ANY(:ids)"), {"ids": ids})
    session.execute(
        text("DELETE FROM peripheral_model WHERE peripheral_model_id = ANY(:ids)"),
        {"ids": list(model_ids.values())},
    )
    session.execute(text("DELETE FROM site WHERE site_id = :id"), {"id": site.site_id})
    session.execute(
        text("DELETE FROM customer WHERE customer_id = :id"), {"id": customer.customer_id}
    )
    session.commit()


@pytest.fixture(scope="module")
def ingested(session: Session, fleet: dict[str, int]) -> dict[str, int]:
    """플릿에 이벤트를 흘려보내고 집계까지 돌린다."""
    random.seed(7)
    for kiosk in session.scalars(select(Kiosk).where(Kiosk.serial_no.like("PIPE-%"))):
        model_id = session.scalar(
            select(Peripheral.peripheral_model_id).where(
                Peripheral.kiosk_id == kiosk.kiosk_id, Peripheral.detached_at.is_(None)
            )
        )
        rate = FAIL_RATE[BAD_MODEL] if model_id == fleet["bad"] else FAIL_RATE[GOOD_MODEL]
        events = [
            EventIn(
                occurred_at=WINDOW_START + dt.timedelta(seconds=i * 30),
                source_seq=i + 1,
                type="printer.print_failed" if random.random() < rate else "order.created",
                peripheral_slot="USB1",
            )
            for i in range(ATTEMPTS)
        ]
        for start in range(0, len(events), 200):
            service.ingest_events(session, kiosk, EventBatchIn(events=events[start : start + 200]))

    rows = rollup_range(session, WINDOW_START, WINDOW_END)
    return {"rollup_rows": rows}


def test_집계가_채워진다(session: Session, ingested: dict[str, int]) -> None:
    assert ingested["rollup_rows"] > 0


def test_탐지기가_나쁜_모델을_잡는다(
    session: Session, fleet: dict[str, int], ingested: dict[str, int]
) -> None:
    detector = PeripheralErrorRateDetector(min_events=20, min_activity=200, ratio=2.0)
    candidates = detector.run(session, Window(start=WINDOW_START, end=WINDOW_END))

    flagged = {c.context["peripheral_model_id"] for c in candidates}
    assert fleet["bad"] in flagged, f"나쁜 모델을 놓쳤다: {[c.signal for c in candidates]}"
    assert fleet["good"] not in flagged, "멀쩡한 모델을 이상치로 잡았다"


def test_근거_수치가_실제_비율과_맞는다(
    session: Session, fleet: dict[str, int], ingested: dict[str, int]
) -> None:
    """제안의 근거로 쓰이는 값이라 눈대중이 아니라 실제 비율이어야 한다."""
    detector = PeripheralErrorRateDetector(min_events=20, min_activity=200, ratio=2.0)
    candidate = next(
        c
        for c in detector.run(session, Window(start=WINDOW_START, end=WINDOW_END))
        if c.context["peripheral_model_id"] == fleet["bad"]
    )
    evidence = candidate.evidence
    assert evidence.observed == pytest.approx(FAIL_RATE[BAD_MODEL], abs=0.04)
    assert evidence.baseline == pytest.approx(FAIL_RATE[GOOD_MODEL], abs=0.02)
    assert evidence.observed > evidence.baseline * 2
    assert evidence.affected_kiosks == 2


def test_error_severity_타입이_아니어도_동작한다(
    session: Session, fleet: dict[str, int], ingested: dict[str, int]
) -> None:
    """같은 타입 안의 errors/events 로 계산하던 시절엔 이 경우가 항상 1.0 이 되어
    아무것도 잡히지 않았다. 분모가 '장치 전체 활동량' 이어야 하는 이유다."""
    detector = PeripheralErrorRateDetector(min_events=20, min_activity=200, ratio=2.0)
    candidate = next(
        c
        for c in detector.run(session, Window(start=WINDOW_START, end=WINDOW_END))
        if c.context["peripheral_model_id"] == fleet["bad"]
    )
    # print_failed 는 default_sev=40 이라 errors == events 다. 그래도 1.0 이 아니어야 한다.
    assert candidate.evidence.observed < 0.5
    assert candidate.context["device_activity"] > candidate.context["event_count"]
