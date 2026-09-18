"""탐지기[A] 공통 뼈대.

탐지기는 LLM 을 쓰지 않는다. SQL 과 통계만 쓴다. 이 계층의 존재 이유는
"이벤트 수백만 건"을 "후보 수십 건"으로 줄여서 LLM 에게 판단할 거리만 넘기는 것이다.
탐지기가 없으면 분석 Agent 는 환각한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from agents.contracts.proposal import Candidate

DETECTOR_VERSION = "0.2.0"


@dataclass(frozen=True)
class Window:
    start: dt.datetime
    end: dt.datetime

    @property
    def label(self) -> str:
        return f"{self.start.date().isoformat()}/{self.end.date().isoformat()}"

    @classmethod
    def last_days(cls, days: int, now: dt.datetime | None = None) -> Window:
        end = now or dt.datetime.now(dt.UTC)
        return cls(start=end - dt.timedelta(days=days), end=end)


@runtime_checkable
class Detector(Protocol):
    id: str
    description: str

    def run(self, session: Session, window: Window) -> list[Candidate]: ...
