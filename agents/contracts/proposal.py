"""분석 Agent 와 개발 Agent 사이의 유일한 계약.

이 모듈이 흔들리면 두 Agent 가 조용히 어긋난 채로 돌아간다.
변경할 때는 feature_proposal.body 에 이미 저장된 JSON 도 읽을 수 있는지 반드시 확인한다.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Scope = Literal["kiosk_app", "ops_backend", "device_driver", "config_only"]
Effort = Literal["S", "M", "L"]

CONTRACT_VERSION = "1.0"


class Evidence(BaseModel):
    """제안의 근거가 된 관측값. 사람이 같은 쿼리로 재현할 수 있어야 한다."""

    model_config = ConfigDict(extra="forbid")

    metric: str
    query_id: str = Field(description="agents/detectors 의 탐지기 id. 재현 경로.")
    observed: float
    baseline: float
    sample_size: int = Field(ge=0)
    window: str = Field(description="예: 2026-09-01/2026-09-15")
    affected_kiosks: int = Field(ge=0)


class Candidate(BaseModel):
    """탐지기[A]의 출력. 아직 '제안'이 아니라 '이상 신호'다."""

    model_config = ConfigDict(extra="forbid")

    detector_id: str
    signal: str = Field(description="한 줄 요약. 예: printer.paper_jam 급증")
    dedupe_key: str
    evidence: Evidence
    context: dict[str, str | int | float | None] = Field(default_factory=dict)


class FeatureProposal(BaseModel):
    """분석 Agent[B]의 출력. 개발 Agent 의 입력.

    acceptance_criteria 가 이 스키마의 핵심이다. 여기가 부실하면 개발 Agent 가 만들 게 없다.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    title: str = Field(min_length=1, max_length=200)
    problem: str = Field(min_length=1, description="관측된 문제")
    hypothesis: str = Field(min_length=1, description="원인 가설")
    proposal: str = Field(min_length=1, description="제안하는 기능/변경")
    scope: Scope
    acceptance_criteria: list[str] = Field(
        min_length=1,
        description="개발 Agent 가 그대로 테스트로 옮길 수 있는 형태여야 한다",
    )
    evidence: list[Evidence] = Field(min_length=1)
    impact_score: float = Field(ge=0)
    effort_estimate: Effort
    confidence: float = Field(ge=0, le=1)
    risks: list[str] = Field(default_factory=list)
    dedupe_key: str | None = None

    def compute_dedupe_key(self) -> str:
        """같은 문제를 매일 다시 제안하는 것을 막기 위한 키."""
        if self.dedupe_key:
            return self.dedupe_key
        seed = "|".join(sorted({e.query_id + ":" + e.metric for e in self.evidence}))
        return hashlib.sha256(f"{self.scope}|{seed}".encode()).hexdigest()[:32]
