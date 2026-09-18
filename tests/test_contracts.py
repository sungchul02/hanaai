"""계약 테스트.

분석 Agent 와 개발 Agent 를 잇는 유일한 지점이라, 여기가 조용히 깨지면
두 Agent 가 서로 다른 것을 주고받으면서도 에러 없이 돌아간다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.contracts.proposal import Candidate, Evidence, FeatureProposal


def _evidence(**overrides: object) -> Evidence:
    base = {
        "metric": "printer.paper_jam error rate (Epson TM-T88VI)",
        "query_id": "peripheral_error_rate",
        "observed": 0.08,
        "baseline": 0.02,
        "sample_size": 1200,
        "window": "2026-09-01/2026-09-08",
        "affected_kiosks": 14,
    }
    base.update(overrides)
    return Evidence(**base)  # type: ignore[arg-type]


def _proposal(**overrides: object) -> FeatureProposal:
    base: dict[str, object] = {
        "title": "Epson TM-T88VI 용지 걸림 자동 복구",
        "problem": "해당 모델의 paper_jam 오류율이 동종 대비 4배",
        "hypothesis": "절단 후 재급지 시퀀스 누락",
        "proposal": "인쇄 실패 시 재급지 후 1회 자동 재시도",
        "scope": "kiosk_app",
        "acceptance_criteria": ["paper_jam 발생 후 자동 복구 성공률 80% 이상"],
        "evidence": [_evidence()],
        "impact_score": 67.2,
        "effort_estimate": "M",
        "confidence": 0.7,
    }
    base.update(overrides)
    return FeatureProposal(**base)  # type: ignore[arg-type]


def test_acceptance_criteria_는_비어있을_수_없다() -> None:
    """이게 비면 개발 Agent 가 만들 게 없다. 스키마가 막아야 한다."""
    with pytest.raises(ValidationError):
        _proposal(acceptance_criteria=[])


def test_근거_없는_제안은_거부된다() -> None:
    with pytest.raises(ValidationError):
        _proposal(evidence=[])


def test_모르는_scope_는_거부된다() -> None:
    with pytest.raises(ValidationError):
        _proposal(scope="database_schema")


def test_confidence_범위() -> None:
    with pytest.raises(ValidationError):
        _proposal(confidence=1.5)


def test_계약에_없는_필드는_거부된다() -> None:
    """모델이 멋대로 필드를 늘리면 조용히 흘려보내지 않고 실패시킨다."""
    with pytest.raises(ValidationError):
        _proposal(estimated_hours=8)


def test_dedupe_key_는_같은_근거에_대해_안정적이다() -> None:
    a = _proposal().compute_dedupe_key()
    b = _proposal(title="제목만 다름", impact_score=1.0).compute_dedupe_key()
    assert a == b


def test_dedupe_key_는_근거가_다르면_달라진다() -> None:
    a = _proposal().compute_dedupe_key()
    b = _proposal(evidence=[_evidence(metric="card_reader.read_failed rate")])
    assert a != b.compute_dedupe_key()


def test_명시된_dedupe_key_가_우선한다() -> None:
    assert _proposal(dedupe_key="manual-key").compute_dedupe_key() == "manual-key"


def test_후보는_제안이_아니다() -> None:
    """Candidate 는 '이상 신호'까지만 표현한다. 해석은 분석 Agent 의 몫이다."""
    candidate = Candidate(
        detector_id="peripheral_error_rate",
        signal="Epson TM-T88VI 의 paper_jam 오류율이 4.0배",
        dedupe_key="peripheral_error_rate:1:1001",
        evidence=_evidence(),
    )
    assert not hasattr(candidate, "proposal")
    assert candidate.evidence.affected_kiosks == 14
