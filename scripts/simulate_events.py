"""MAXIVIEW 이벤트 시뮬레이터.

실제 키오스크가 붙기 전까지 탐지기를 만들고 검증하기 위한 도구다.
실제 수집 경로(services.ingest.service)를 그대로 통과시키므로, 수집 로직까지 같이 돌려본다.

  python scripts/simulate_events.py --days 7 --sessions 40
  python scripts/simulate_events.py --days 7 --sessions 40 --reset

## 심어둔 신호

탐지기가 '찾아낼 것이 있어야' 만들면서 검증할 수 있다. 네 가지를 의도적으로 넣는다.

| 신호 | 내용 | 겨냥하는 탐지기 |
|---|---|---|
| 앱 버전 지연 회귀 | 플릿 절반이 1.1.0 으로 올라간 뒤 추론 p95 가 2.5배 | latency_regression |
| 되물음 폭주 | 특정 지점에서 intent.clarify_requested 가 세션당 3배 | retry_storm |
| RAG 공백 | 최근 하루 rag.no_result 가 급증 | event_type_spike |
| 접근성 이탈 | 1.1.0 에서 높이조절 직후 이탈률 상승 | session_funnel_drop |

정답을 아는 데이터라서, 탐지기가 이걸 못 찾으면 탐지기가 틀린 것이다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text

from services.common.db import get_sessionmaker
from services.common.models import Event, Kiosk
from services.ingest.schemas import EventBatchIn, EventIn
from services.ingest.service import ingest_events

OLD_VERSION = "1.0.0"
NEW_VERSION = "1.1.0"

# 배포 시점: 관측 창의 끝에서 이만큼 전. 절반의 키오스크가 이때 올라간다.
CUTOVER_BEFORE_END = dt.timedelta(days=2)
# RAG 공백이 시작되는 시점
RAG_GAP_BEFORE_END = dt.timedelta(days=1)

BATCH_SIZE = 500


@dataclass
class Profile:
    """키오스크 한 대의 성격. 심어둔 신호는 전부 여기서 갈린다."""

    kiosk_id: int
    serial_no: str
    upgrades: bool  # 절반만 새 버전으로 올라간다
    clarify_heavy: bool  # 되물음 폭주 지점
    accessibility_ratio: float

    def version_at(self, when: dt.datetime, cutover: dt.datetime) -> str:
        if self.upgrades and when >= cutover:
            return NEW_VERSION
        return OLD_VERSION


class Emitter:
    """한 세션 분량의 이벤트를 만든다. source_seq 는 키오스크별로 단조증가."""

    def __init__(self, rng: random.Random, profile: Profile, start_seq: int) -> None:
        self.rng = rng
        self.profile = profile
        self.seq = start_seq
        self.events: list[EventIn] = []

    def emit(
        self,
        when: dt.datetime,
        code: str,
        session_id: uuid.UUID | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        self.events.append(
            EventIn(
                occurred_at=when,
                source_seq=self.seq,
                type=code,
                session_id=session_id,
                duration_ms=duration_ms,
                error_code=error_code,
            )
        )
        self.seq += 1


def _inference_latency(rng: random.Random, version: str) -> int:
    """1.1.0 에서 추론이 느려진다. 이것이 지연 회귀 탐지기의 정답이다."""
    base = rng.gauss(420, 110)
    if version == NEW_VERSION:
        base *= 2.5
    return max(50, int(base))


def simulate_session(
    emitter: Emitter,
    start: dt.datetime,
    cutover: dt.datetime,
    rag_gap_from: dt.datetime,
) -> None:
    rng = emitter.rng
    profile = emitter.profile
    session_id = uuid.uuid4()
    version = profile.version_at(start, cutover)
    t = start

    def step(seconds: float) -> dt.datetime:
        nonlocal t
        t = t + dt.timedelta(seconds=seconds)
        return t

    emitter.emit(t, "access.approach_detected", session_id)

    # ---- 배리어프리 경로
    accessible = rng.random() < profile.accessibility_ratio
    if accessible:
        emitter.emit(step(1.5), "access.mode_entered", session_id)
        emitter.emit(step(0.5), "access.height_adjust_started", session_id)
        if rng.random() < 0.03:
            emitter.emit(step(4), "access.height_adjust_failed", session_id, error_code="E-MOTOR-3")
            emitter.emit(step(1), "access.mode_abandoned", session_id)
            emitter.emit(step(1), "session.abandoned", session_id)
            return
        emitter.emit(step(3.5), "access.height_adjust_completed", session_id)
        emitter.emit(step(0.5), "access.voice_guide_started", session_id)

        # 심어둔 신호: 새 버전에서 높이조절 직후 이탈이 늘어난다
        drop = 0.30 if version == NEW_VERSION else 0.08
        if rng.random() < drop:
            emitter.emit(step(9), "access.mode_abandoned", session_id)
            emitter.emit(step(1), "session.abandoned", session_id)
            return

    emitter.emit(step(1.0), "session.started", session_id)
    emitter.emit(step(0.5), "voice.session_started", session_id)

    # ---- 대화 턴
    turns = rng.randint(1, 4)
    for _ in range(turns):
        # 음성 인식
        roll = rng.random()
        if roll < 0.05:
            emitter.emit(step(3), "stt.failed", session_id, error_code="E-STT-01")
            continue
        if roll < 0.08:
            emitter.emit(step(6), "stt.timeout", session_id)
            continue
        if roll < 0.12:
            emitter.emit(step(3), "stt.low_confidence", session_id)
        emitter.emit(step(2.2), "stt.recognized", session_id, duration_ms=rng.randint(300, 1200))

        # 의도 파악 — 심어둔 신호: 특정 지점에서 되물음이 잦다
        clarify_rate = 0.35 if profile.clarify_heavy else 0.10
        if rng.random() < clarify_rate:
            emitter.emit(step(1.2), "intent.clarify_requested", session_id)
            if profile.clarify_heavy and rng.random() < 0.5:
                emitter.emit(step(2.5), "intent.clarify_requested", session_id)
        if rng.random() < 0.06:
            emitter.emit(step(1.0), "intent.unresolved", session_id)
            continue
        emitter.emit(step(0.9), "intent.resolved", session_id)

        # 지식 검색 — 심어둔 신호: 최근 하루 결과 없음이 급증
        emitter.emit(step(0.4), "rag.query", session_id)
        no_result_rate = 0.30 if t >= rag_gap_from else 0.04
        if rng.random() < no_result_rate:
            emitter.emit(step(0.6), "rag.no_result", session_id)
            emitter.emit(step(0.3), "rag.fallback_generic", session_id)
        elif rng.random() < 0.07:
            emitter.emit(step(0.6), "rag.low_confidence", session_id)

        # 온디바이스 추론 — 심어둔 신호: 새 버전에서 느려짐
        latency = _inference_latency(rng, version)
        if rng.random() < 0.02:
            emitter.emit(step(1.5), "inference.failed", session_id, error_code="E-NPU-7")
            emitter.emit(step(0.5), "avatar.response_failed", session_id)
            continue
        emitter.emit(step(latency / 1000), "inference.completed", session_id, duration_ms=latency)
        if latency > 900:
            emitter.emit(t, "inference.slow", session_id, duration_ms=latency)
        if version == NEW_VERSION and rng.random() < 0.05:
            emitter.emit(t, "npu.throttled", session_id)

        # 아바타 응답
        emitter.emit(step(0.2), "avatar.response_started", session_id)
        total = latency + rng.randint(200, 700)
        if rng.random() < 0.04:
            emitter.emit(step(1.0), "avatar.interrupted", session_id)
            continue
        emitter.emit(step(total / 1000), "avatar.response_completed", session_id, duration_ms=total)
        emitter.emit(step(0.3), "tts.played", session_id)

    # ---- 마무리
    if rng.random() < 0.72:
        emitter.emit(step(2.0), "order.created", session_id)
        emitter.emit(step(1.0), "payment.requested", session_id)
        if rng.random() < 0.94:
            emitter.emit(step(3.5), "payment.approved", session_id)
            emitter.emit(step(1.0), "session.completed", session_id)
        else:
            emitter.emit(step(8.0), "payment.timeout", session_id, error_code="E-PAY-TO")
            emitter.emit(step(1.0), "session.abandoned", session_id)
    else:
        emitter.emit(step(2.0), "session.abandoned", session_id)

    if accessible:
        emitter.emit(step(0.2), "access.mode_completed", session_id)


def build_profiles(kiosks: list[Kiosk], rng: random.Random) -> list[Profile]:
    profiles: list[Profile] = []
    for index, kiosk in enumerate(kiosks):
        profiles.append(
            Profile(
                kiosk_id=kiosk.kiosk_id,
                serial_no=kiosk.serial_no,
                upgrades=index % 2 == 0,  # 절반만 새 버전
                clarify_heavy="BUSAN" in kiosk.serial_no,  # 한 지점만 되물음 폭주
                accessibility_ratio=0.25,
            )
        )
    return profiles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="관측 창 길이")
    parser.add_argument("--sessions", type=int, default=40, help="키오스크·하루당 세션 수")
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--reset", action="store_true", help="기존 이벤트/집계를 지우고 시작")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    end = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=args.days)
    cutover = end - CUTOVER_BEFORE_END
    rag_gap_from = end - RAG_GAP_BEFORE_END

    factory = get_sessionmaker()
    with factory() as session:
        if args.reset:
            session.execute(text("DELETE FROM event_rollup_hourly"))
            session.execute(text("DELETE FROM event"))
            session.commit()
            print("기존 이벤트/집계 삭제")

        kiosks = list(session.scalars(select(Kiosk).order_by(Kiosk.kiosk_id)))
        if not kiosks:
            print("키오스크가 없다. 먼저 python scripts/seed.py --demo 를 돌려라.")
            return
        profiles = build_profiles(kiosks, rng)

        total = 0
        for profile in profiles:
            # 재실행해도 source_seq 가 겹치지 않게 이어서 매긴다
            next_seq = (
                session.scalar(
                    select(func.max(Event.source_seq)).where(Event.kiosk_id == profile.kiosk_id)
                )
                or 0
            ) + 1
            emitter = Emitter(rng, profile, next_seq)

            for day in range(args.days):
                day_start = start + dt.timedelta(days=day)
                for _ in range(args.sessions):
                    # 영업시간(09~21시)에 몰리게 한다
                    hour = rng.triangular(9, 21, 13)
                    begin = day_start + dt.timedelta(hours=hour, minutes=rng.uniform(0, 59))
                    if begin >= end:
                        continue
                    simulate_session(emitter, begin, cutover, rag_gap_from)

            kiosk = session.get(Kiosk, profile.kiosk_id)
            assert kiosk is not None
            events = sorted(emitter.events, key=lambda e: e.occurred_at)
            for offset in range(0, len(events), BATCH_SIZE):
                chunk = events[offset : offset + BATCH_SIZE]
                version = profile.version_at(chunk[-1].occurred_at, cutover)
                result = ingest_events(
                    session, kiosk, EventBatchIn(events=chunk, app_version=version)
                )
                total += result.accepted
            print(
                f"  {profile.serial_no:26s} {len(events):6,}건 "
                f"{'(신버전 전환)' if profile.upgrades else ''}"
                f"{' (되물음 폭주)' if profile.clarify_heavy else ''}"
            )

        print(f"\n적재 {total:,}건 · 구간 {start:%m-%d %H시} ~ {end:%m-%d %H시}")
        print(f"배포 시점 {cutover:%m-%d %H시} · RAG 공백 시작 {rag_gap_from:%m-%d %H시}")
        print("\n다음: python scripts/rollup.py --hours", args.days * 24 + 2)


if __name__ == "__main__":
    main()
