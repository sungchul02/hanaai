"""탐지기 4종 회귀 테스트 (PostgreSQL 필요).

집계 테이블에 '정답을 아는' 행을 직접 심고, 탐지기가 그걸 찾는지 본다.
수집→집계 경로는 test_db_pipeline 이 이미 덮으므로 여기서는 탐지 로직만 격리해서 본다.
그래야 실패했을 때 "탐지기가 틀렸다" 를 바로 알 수 있다.

각 탐지기마다 확인하는 것은 둘이다.
  1. 심어둔 신호를 찾는가
  2. 멀쩡한 쪽을 잘못 지목하지 않는가  ← 이게 더 중요하다
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agents.detectors.base import Window
from agents.detectors.event_spike import EventSpikeDetector
from agents.detectors.funnel_drop import FunnelDropDetector
from agents.detectors.latency_regression import LatencyRegressionDetector
from agents.detectors.retry_storm import RetryStormDetector
from services.common.db import get_engine, get_sessionmaker
from services.common.models import Customer, EventType, Kiosk, Site

pytestmark = pytest.mark.db

NOW = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
WINDOW = Window(start=NOW - dt.timedelta(days=5), end=NOW)
RECENT_FROM = NOW - dt.timedelta(hours=24)

OLD, NEW = "9.0.0-test", "9.1.0-test"  # 실데이터와 겹치지 않는 버전 문자열


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL 없음: {exc}")
    with get_sessionmaker()() as s:
        yield s


def _type_id(session: Session, code: str) -> int:
    value = session.scalar(select(EventType.event_type_id).where(EventType.code == code))
    assert value is not None, f"이벤트 사전에 {code} 가 없다. seed.py 를 돌렸는가?"
    return value


@pytest.fixture(scope="module")
def fleet(session: Session) -> Iterator[list[int]]:
    """탐지기 테스트 전용 키오스크 4대."""
    session.execute(text("DELETE FROM event_rollup_hourly WHERE app_version LIKE '9.%-test'"))
    session.execute(
        text("""
        DELETE FROM event_rollup_hourly WHERE kiosk_id IN (
            SELECT kiosk_id FROM kiosk WHERE serial_no LIKE 'DET-%'
        )""")
    )
    session.execute(text("DELETE FROM kiosk WHERE serial_no LIKE 'DET-%'"))
    session.execute(
        text("""
        DELETE FROM site WHERE customer_id IN (
            SELECT customer_id FROM customer WHERE code LIKE 'DET-%'
        )""")
    )
    session.execute(text("DELETE FROM customer WHERE code LIKE 'DET-%'"))
    session.commit()

    tag = uuid.uuid4().hex[:6]
    customer = Customer(code=f"DET-{tag}", name="탐지기 테스트")
    session.add(customer)
    session.flush()
    site = Site(customer_id=customer.customer_id, code=f"S-{tag}", name="테스트 지점")
    session.add(site)
    session.flush()

    ids: list[int] = []
    for i in range(4):
        kiosk = Kiosk(
            serial_no=f"DET-{tag}-K{i}", site_id=site.site_id, model="MAXIVIEW", status="active"
        )
        session.add(kiosk)
        session.flush()
        ids.append(kiosk.kiosk_id)
    session.commit()

    yield ids

    session.execute(
        text("DELETE FROM event_rollup_hourly WHERE kiosk_id = ANY(:ids)"), {"ids": ids}
    )
    session.execute(text("DELETE FROM kiosk WHERE kiosk_id = ANY(:ids)"), {"ids": ids})
    session.execute(text("DELETE FROM site WHERE site_id = :id"), {"id": site.site_id})
    session.execute(
        text("DELETE FROM customer WHERE customer_id = :id"), {"id": customer.customer_id}
    )
    session.commit()


def _put(
    session: Session,
    kiosk_id: int,
    code: str,
    bucket: dt.datetime,
    *,
    count: int,
    version: str = "",
    p95: int | None = None,
    sessions: int | None = None,
    errors: int = 0,
) -> None:
    session.execute(
        text("""
        INSERT INTO event_rollup_hourly
            (bucket, kiosk_id, event_type_id, peripheral_model_id, app_version,
             event_count, error_count, duration_p50_ms, duration_p95_ms, distinct_sessions)
        VALUES (:bucket, :kiosk, :type_id, 0, :version, :count, :errors, :p50, :p95, :sessions)
        ON CONFLICT (bucket, kiosk_id, event_type_id, peripheral_model_id, app_version)
        DO UPDATE SET event_count = EXCLUDED.event_count,
                      duration_p95_ms = EXCLUDED.duration_p95_ms,
                      distinct_sessions = EXCLUDED.distinct_sessions
        """),
        {
            "bucket": bucket,
            "kiosk": kiosk_id,
            "type_id": _type_id(session, code),
            "version": version,
            "count": count,
            "errors": errors,
            "p50": p95 // 2 if p95 else None,
            "p95": p95,
            "sessions": sessions,
        },
    )


def _hours(count: int) -> list[dt.datetime]:
    return [WINDOW.start + dt.timedelta(hours=h) for h in range(count)]


# ------------------------------------------------------------------ 급증


@pytest.fixture(scope="module")
def spike_data(session: Session, fleet: list[int]) -> None:
    """rag.no_result 가 마지막 24시간에만 치솟는다. 다른 이벤트는 평탄하다."""
    kiosk = fleet[0]
    for bucket in _hours(120):
        recent = bucket >= RECENT_FROM
        _put(session, kiosk, "rag.no_result", bucket, count=30 if recent else 2)
        _put(session, kiosk, "rag.query", bucket, count=100)  # 평탄한 대조군
    session.commit()


def test_급증을_잡는다(session: Session, spike_data: None) -> None:
    found = EventSpikeDetector().run(session, WINDOW)
    codes = {c.context["event_code"] for c in found}
    assert "rag.no_result" in codes, [c.signal for c in found]


def test_평탄한_이벤트는_급증으로_잡지_않는다(session: Session, spike_data: None) -> None:
    found = EventSpikeDetector().run(session, WINDOW)
    assert "rag.query" not in {c.context["event_code"] for c in found}


# ------------------------------------------------------------------ 지연 회귀


@pytest.fixture(scope="module")
def latency_data(session: Session, fleet: list[int]) -> None:
    """같은 구간에 두 버전이 공존하고, 새 버전만 3배 느리다."""
    for bucket in _hours(48):
        _put(session, fleet[1], "inference.completed", bucket, count=20, version=OLD, p95=400)
        _put(session, fleet[2], "inference.completed", bucket, count=20, version=NEW, p95=1200)
        # 지연이 같은 이벤트. 버전만으로 잡히면 안 된다.
        _put(session, fleet[1], "stt.recognized", bucket, count=20, version=OLD, p95=500)
        _put(session, fleet[2], "stt.recognized", bucket, count=20, version=NEW, p95=510)
    session.commit()


def test_버전별_지연_회귀를_잡는다(session: Session, latency_data: None) -> None:
    found = LatencyRegressionDetector().run(session, WINDOW)
    hits = [c for c in found if c.context["event_code"] == "inference.completed"]
    assert hits, [c.signal for c in found]
    assert hits[0].context["slow_version"] == NEW
    assert hits[0].context["ratio"] >= 2.5


def test_지연이_같으면_잡지_않는다(session: Session, latency_data: None) -> None:
    found = LatencyRegressionDetector().run(session, WINDOW)
    assert "stt.recognized" not in {c.context["event_code"] for c in found}


# ------------------------------------------------------------------ 재시도 폭주


@pytest.fixture(scope="module")
def retry_data(session: Session, fleet: list[int]) -> None:
    """K3 만 세션당 되물음이 4배. 나머지 3대는 정상."""
    for bucket in _hours(48):
        for index, kiosk in enumerate(fleet):
            _put(session, kiosk, "session.started", bucket, count=10, sessions=10)
            heavy = index == 3
            _put(
                session,
                kiosk,
                "intent.clarify_requested",
                bucket,
                count=16 if heavy else 4,
            )
    session.commit()


def test_재시도_폭주_키오스크를_잡는다(
    session: Session, retry_data: None, fleet: list[int]
) -> None:
    found = RetryStormDetector().run(session, WINDOW)
    flagged = {c.dedupe_key.split(":")[1] for c in found}
    assert str(fleet[3]) in flagged, [c.signal for c in found]


def test_정상_키오스크는_잡지_않는다(session: Session, retry_data: None, fleet: list[int]) -> None:
    found = RetryStormDetector().run(session, WINDOW)
    flagged = {c.dedupe_key.split(":")[1] for c in found}
    for kiosk_id in fleet[:3]:
        assert str(kiosk_id) not in flagged


# ------------------------------------------------------------------ 퍼널 이탈


@pytest.fixture(scope="module")
def funnel_data(session: Session, fleet: list[int]) -> None:
    """새 버전에서만 높이조절 다음 단계 이탈이 커진다."""
    for bucket in _hours(24):
        # 구버전: 100 진입 → 95 조절완료 → 90 완료 (이탈 5퍼센트)
        _put(session, fleet[1], "access.mode_entered", bucket, count=10, version=OLD)
        _put(session, fleet[1], "access.height_adjust_completed", bucket, count=10, version=OLD)
        _put(session, fleet[1], "access.mode_completed", bucket, count=9, version=OLD)
        # 신버전: 같은 진입에서 완료가 절반 (이탈 50퍼센트)
        _put(session, fleet[2], "access.mode_entered", bucket, count=10, version=NEW)
        _put(session, fleet[2], "access.height_adjust_completed", bucket, count=10, version=NEW)
        _put(session, fleet[2], "access.mode_completed", bucket, count=5, version=NEW)
    session.commit()


def test_퍼널_이탈_구간을_잡는다(session: Session, funnel_data: None) -> None:
    found = FunnelDropDetector(min_entries=50).run(session, WINDOW)
    hits = [
        c
        for c in found
        if c.context["step_from"] == "access.height_adjust_completed"
        and c.context["app_version"] == NEW
    ]
    assert hits, [c.signal for c in found]
    assert hits[0].evidence.observed > hits[0].evidence.baseline * 1.5


def test_비교할_버전이_하나뿐이면_판단하지_않는다(session: Session) -> None:
    """한 버전만 있으면 '높다/낮다' 를 말할 기준이 없다. 억지로 잡지 않는다."""
    empty = Window(start=NOW - dt.timedelta(days=400), end=NOW - dt.timedelta(days=399))
    assert FunnelDropDetector().run(session, empty) == []
