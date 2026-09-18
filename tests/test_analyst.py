"""분석 계층 테스트 (LLM 호출 없음).

CLI 생성기는 subprocess 를 가로채서 테스트한다. 실제 호출은 돈이 들고 결과가 매번 달라서
단위 테스트에 쓸 수 없다. 여기서 고정하는 것은 '응답을 어떻게 해석하는가' 다.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from agents.analyst import generator as gen
from agents.analyst.generator import (
    BACKENDS,
    ClaudeCliGenerator,
    PassthroughGenerator,
    build_system_prompt,
    build_user_prompt,
    get_generator,
    parse_proposals,
)
from agents.contracts.proposal import Candidate, Evidence
from agents.detectors.base import Window


def _window() -> Window:
    return Window.last_days(7, now=dt.datetime(2026, 9, 18, tzinfo=dt.UTC))


def _candidate(affected: int = 10, observed: float = 0.08) -> Candidate:
    return Candidate(
        detector_id="peripheral_error_rate",
        signal="Epson TM-T88VI 의 printer.paper_jam 발생률이 4.0배",
        dedupe_key="peripheral_error_rate:1:1001",
        evidence=Evidence(
            metric="printer.paper_jam per device activity",
            query_id="peripheral_error_rate",
            observed=observed,
            baseline=0.02,
            sample_size=1200,
            window="2026-09-11/2026-09-18",
            affected_kiosks=affected,
        ),
    )


def _valid_proposal_json() -> list[dict[str, Any]]:
    return [
        {
            "title": "프린터 용지 걸림 자동 복구",
            "problem": "특정 모델의 용지 걸림이 동종 대비 4배",
            "hypothesis": "절단 후 재급지 시퀀스 누락",
            "proposal": "인쇄 실패 시 재급지 후 1회 자동 재시도",
            "scope": "kiosk_app",
            "acceptance_criteria": ["용지 걸림 후 자동 복구 성공률 80% 이상"],
            "evidence": [_candidate().evidence.model_dump(mode="json")],
            "impact_score": 67.2,
            "effort_estimate": "M",
            "confidence": 0.7,
            "risks": [],
        }
    ]


# ------------------------------------------------------------------ 기본


def test_window_라벨() -> None:
    assert _window().label == "2026-09-11/2026-09-18"


def test_passthrough_는_후보를_제안으로_바꾼다() -> None:
    proposals = PassthroughGenerator().generate([_candidate()], _window())
    assert len(proposals) == 1
    assert proposals[0].dedupe_key == "peripheral_error_rate:1:1001"


def test_passthrough_제안은_낮은_confidence_를_갖는다() -> None:
    proposal = PassthroughGenerator().generate([_candidate()], _window())[0]
    assert proposal.confidence <= 0.3
    assert proposal.risks


def test_후보가_없으면_LLM_을_부르지_않는다() -> None:
    """빈 호출로 돈을 쓰지 않는다."""
    assert ClaudeCliGenerator().generate([], _window()) == []


# ------------------------------------------------------------------ 프롬프트


def test_시스템_프롬프트에_계약_스키마가_박혀있다() -> None:
    """스키마를 손으로 적어두면 계약이 바뀔 때 조용히 어긋난다."""
    prompt = build_system_prompt()
    assert "acceptance_criteria" in prompt
    assert "effort_estimate" in prompt
    # 서버가 채우는 필드는 모델에게 알려주지 않는다
    assert '"contract_version"' not in prompt


def test_사용자_프롬프트에_근거_수치가_들어간다() -> None:
    prompt = build_user_prompt([_candidate()], _window())
    assert "2026-09-11/2026-09-18" in prompt
    assert "peripheral_error_rate" in prompt


# ------------------------------------------------------------------ 응답 해석


def test_코드펜스로_감싼_응답도_읽는다() -> None:
    text = "여기 결과입니다:\n```json\n" + json.dumps(_valid_proposal_json()) + "\n```"
    proposals, errors = parse_proposals(text)
    assert len(proposals) == 1
    assert not errors


def test_설명이_앞뒤에_붙어도_배열만_뽑는다() -> None:
    text = "생각해보니 이렇습니다.\n" + json.dumps(_valid_proposal_json()) + "\n끝."
    proposals, errors = parse_proposals(text)
    assert len(proposals) == 1
    assert not errors


def test_계약_위반_제안은_버리되_이유를_남긴다() -> None:
    """조용히 버리면 프롬프트가 나빠진 것을 알아챌 수 없다."""
    bad = _valid_proposal_json()
    bad[0]["acceptance_criteria"] = []  # 계약상 최소 1건
    proposals, errors = parse_proposals(json.dumps(bad))
    assert proposals == []
    assert len(errors) == 1
    assert "acceptance_criteria" in errors[0]


def test_유효한_것과_아닌_것이_섞이면_유효한_것만_남긴다() -> None:
    items = [*_valid_proposal_json(), {"title": "쓰레기"}]
    proposals, errors = parse_proposals(json.dumps(items))
    assert len(proposals) == 1
    assert len(errors) == 1


def test_JSON_이_아니면_빈_결과와_이유() -> None:
    proposals, errors = parse_proposals("죄송하지만 분석할 수 없습니다.")
    assert proposals == []
    assert errors


# ------------------------------------------------------------------ CLI 호출


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_cli(monkeypatch: pytest.MonkeyPatch, envelope: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        captured["cwd"] = kwargs.get("cwd")
        return _FakeCompleted(json.dumps(envelope))

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    monkeypatch.setattr(gen.shutil, "which", lambda _: "/usr/bin/claude")
    return captured


def test_CLI_응답을_제안으로_변환한다(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_cli(
        monkeypatch,
        {
            "result": json.dumps(_valid_proposal_json()),
            "is_error": False,
            "total_cost_usd": 0.02,
            "usage": {"input_tokens": 100, "output_tokens": 200},
        },
    )
    generator = ClaudeCliGenerator()
    proposals = generator.generate([_candidate()], _window())

    assert len(proposals) == 1
    assert proposals[0].scope == "kiosk_app"
    assert generator.last_usage is not None
    assert generator.last_usage["cost_usd"] == 0.02


def test_프롬프트는_stdin_으로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """argv 로 넘기면 후보가 늘었을 때 길이 제한에 걸린다."""
    captured = _fake_cli(
        monkeypatch, {"result": json.dumps(_valid_proposal_json()), "is_error": False}
    )
    ClaudeCliGenerator().generate([_candidate()], _window())

    assert captured["input"] is not None
    assert "Epson TM-T88VI" in captured["input"]
    # 후보 고유 문자열로 확인한다. 시스템 프롬프트에도 예시가 들어 있어서
    # detector_id 같은 공통 단어로는 argv/stdin 구분이 안 된다.
    assert not any("Epson TM-T88VI" in arg for arg in captured["cmd"][1:])
    assert not any("분석 구간" in arg for arg in captured["cmd"][1:])


def test_리포_바깥에서_실행한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """리포 안에서 돌리면 CLAUDE.md 가 시스템 프롬프트에 섞여 분석이 오염된다."""
    captured = _fake_cli(
        monkeypatch, {"result": json.dumps(_valid_proposal_json()), "is_error": False}
    )
    ClaudeCliGenerator().generate([_candidate()], _window())

    cwd = str(captured["cwd"])
    assert "hanaai-analyst-" in cwd
    assert "Desktop" not in cwd


def test_도구를_전부_막는다(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _fake_cli(
        monkeypatch, {"result": json.dumps(_valid_proposal_json()), "is_error": False}
    )
    ClaudeCliGenerator().generate([_candidate()], _window())

    cmd = captured["cmd"]
    blocked = cmd[cmd.index("--disallowed-tools") + 1]
    for tool in ("Bash", "Write", "Edit", "WebFetch"):
        assert tool in blocked


def test_CLI_가_없으면_친절하게_실패한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="claude login"):
        ClaudeCliGenerator().generate([_candidate()], _window())


def test_CLI_오류_응답은_예외로_올린다(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_cli(monkeypatch, {"is_error": True, "result": "rate limited"})
    with pytest.raises(RuntimeError, match="오류 응답"):
        ClaudeCliGenerator().generate([_candidate()], _window())


# ------------------------------------------------------------------ 백엔드 교체


def test_백엔드_세_개가_등록되어_있다() -> None:
    assert set(BACKENDS) == {"passthrough", "claude-cli", "claude-api"}


def test_기본_백엔드는_로그인된_CLI() -> None:
    assert get_generator().name == "claude-cli"


def test_백엔드를_바꿔_끼울_수_있다() -> None:
    """기업 교체 지점. 호출부 코드는 그대로다."""
    assert get_generator("passthrough").name == "passthrough"
    assert get_generator("claude-api").name == "claude-api"


def test_모르는_백엔드는_거부한다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 분석 백엔드"):
        get_generator("gpt-9")


def test_동적_시스템_프롬프트_섹션을_제외한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """이게 빠지면 Claude Code 의 에이전트 프레이밍이 우리 프롬프트를 덮어써서
    모델이 JSON 대신 산문으로 답한다."""
    captured = _fake_cli(
        monkeypatch, {"result": json.dumps(_valid_proposal_json()), "is_error": False}
    )
    ClaudeCliGenerator().generate([_candidate()], _window())
    assert "--exclude-dynamic-system-prompt-sections" in captured["cmd"]
