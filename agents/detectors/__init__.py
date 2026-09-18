"""탐지기 레지스트리.

새 탐지기를 추가하면 여기에 등록한다. 분석 Agent 는 레지스트리만 보고 전부 돌린다.

탐지기는 LLM 을 쓰지 않는다. SQL 과 통계만 쓴다. 이 계층의 존재 이유는 이벤트 수백만 건을
후보 수십 건으로 줄여서 LLM 에게 판단할 거리만 넘기는 것이다.

모든 탐지기가 지키는 규칙:
- event_rollup_hourly 만 읽는다. 원본 event 를 직접 집계하지 않는다.
- 비교 기준에서 대상 자신을 뺀다. 빼지 않으면 점유율 높은 쪽이 자기 자신과 비교된다.
- 비율의 분모를 명시한다. 횟수만 보면 트래픽 많은 쪽이 늘 1등이 된다.
"""

from __future__ import annotations

from agents.detectors.base import DETECTOR_VERSION, Detector, Window
from agents.detectors.event_spike import EventSpikeDetector
from agents.detectors.funnel_drop import FunnelDropDetector
from agents.detectors.latency_regression import LatencyRegressionDetector
from agents.detectors.peripheral_error_rate import PeripheralErrorRateDetector
from agents.detectors.retry_storm import RetryStormDetector

ALL_DETECTORS: list[Detector] = [
    PeripheralErrorRateDetector(),
    EventSpikeDetector(),
    LatencyRegressionDetector(),
    RetryStormDetector(),
    FunnelDropDetector(),
]

__all__ = ["ALL_DETECTORS", "DETECTOR_VERSION", "Detector", "Window"]
