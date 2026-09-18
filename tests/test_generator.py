"""계약과 LLM 응답 해석 테스트 (실제 호출 없음).

CLI 생성기는 subprocess 를 가로채서 본다. 실제 호출은 돈이 들고 결과가 매번 달라서
단위 테스트에 쓸 수 없다. 여기서 고정하는 것은 '응답을 어떻게 해석하는가' 다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

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
from agents.contracts.proposal import ContentProposal

WINDOW = "2026-09-11/2026-09-18"


def _cluster(**overrides: Any) -> dict[str, Any]:
    base = {
        "label": "주차장 어디예요?",
        "question_count": 87,
        "unanswered_count": 87,
        "sample_questions": ["주차장 어디예요?", "차 어디에 대면 돼요?"],
        "keywords": ["주차", "주차장"],
        "existing_menu": None,
        "dedupe_key": "주차-주차장",
    }
    base.update(overrides)
    return base


def _valid(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "주차 안내",
        "body": "주차장은 지하 1층에 있습니다. 최초 30분은 무료입니다.",
        "reason": "주차 질문이 87건 있었으나 관련 메뉴가 없다",
        "keywords": ["주차", "주차장"],
        "evidence": {
            "question_count": 87,
            "unanswered_count": 87,
            "window": WINDOW,
            "sample_questions": ["주차장 어디예요?"],
            "existing_menu": None,
        },
        "impact_score": 87.0,
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ 계약


def test_안내_문구가_너무_짧으면_거부된다() -> None:
    """승인 즉시 키오스크에 나가는 문장이라 '초안' 이 아니라 완성문이어야 한다."""
    with pytest.raises(ValidationError):
        ContentProposal.model_validate(_valid(body="지하1층"))


def test_근거_없는_추천은_거부된다() -> None:
    with pytest.raises(ValidationError):
        ContentProposal.model_validate(
            _valid(
                evidence={
                    "question_count": 87,
                    "unanswered_count": 87,
                    "window": WINDOW,
                    "sample_questions": [],
                    "existing_menu": None,
                }
            )
        )


def test_키워드_없는_추천은_거부된다() -> None:
    """키워드가 없으면 승인해도 사용자가 그 메뉴를 찾지 못한다."""
    with pytest.raises(ValidationError):
        ContentProposal.model_validate(_valid(keywords=[]))


def test_계약에_없는_필드는_거부된다() -> None:
    with pytest.raises(ValidationError):
        ContentProposal.model_validate(_valid(priority="P1"))


def test_dedupe_키는_같은_주제에_대해_안정적이다() -> None:
    a = ContentProposal.model_validate(_valid()).compute_dedupe_key()
    b = ContentProposal.model_validate(_valid(title="주차장 안내")).compute_dedupe_key()
    assert a == b


# ------------------------------------------------------------------ 프롬프트


def test_시스템_프롬프트에_유효한_예시가_들어간다() -> None:
    prompt = build_system_prompt()
    assert "확인 후 입력 필요" in prompt  # 모르는 사실을 지어내지 말라는 장치
    assert '"contract_version"' not in prompt


def test_사용자_프롬프트에_주제와_수치가_들어간다() -> None:
    prompt = build_user_prompt([_cluster()], WINDOW)
    assert "주차장 어디예요?" in prompt
    assert "87" in prompt


# ------------------------------------------------------------------ 응답 해석


def test_코드펜스로_감싼_응답도_읽는다() -> None:
    text = "```json\n" + json.dumps([_valid()]) + "\n```"
    proposals, errors = parse_proposals(text)
    assert len(proposals) == 1 and not errors


def test_계약_위반은_버리되_이유를_남긴다() -> None:
    proposals, errors = parse_proposals(json.dumps([_valid(keywords=[])]))
    assert proposals == []
    assert errors and "keywords" in errors[0]


def test_빈_배열은_추천할_것이_없다는_뜻() -> None:
    proposals, errors = parse_proposals("[]")
    assert proposals == [] and not errors


def test_JSON_이_아니면_이유를_남긴다() -> None:
    proposals, errors = parse_proposals("죄송하지만 판단할 수 없습니다.")
    assert proposals == [] and errors


# ------------------------------------------------------------------ CLI


class _Done:
    def __init__(self, stdout: str) -> None:
        self.stdout, self.stderr, self.returncode = stdout, "", 0


def _fake_cli(monkeypatch: pytest.MonkeyPatch, envelope: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> _Done:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        captured["cwd"] = kwargs.get("cwd")
        return _Done(json.dumps(envelope))

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    monkeypatch.setattr(gen.shutil, "which", lambda _: "/usr/bin/claude")
    return captured


def test_주제가_없으면_LLM_을_부르지_않는다() -> None:
    assert ClaudeCliGenerator().generate([], WINDOW) == []


def test_CLI_응답을_추천으로_변환한다(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_cli(
        monkeypatch, {"result": json.dumps([_valid()]), "is_error": False, "total_cost_usd": 0.03}
    )
    generator = ClaudeCliGenerator()
    proposals = generator.generate([_cluster()], WINDOW)
    assert len(proposals) == 1
    assert generator.last_usage is not None
    assert generator.last_usage["cost_usd"] == 0.03


def test_프롬프트는_stdin_으로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """argv 로 넘기면 주제가 늘었을 때 길이 제한에 걸린다.

    시스템 프롬프트에도 예시가 들어 있으므로, 이 입력에만 있는 문자열로 확인한다.
    """
    marker = "고유확인문자열XYZ"
    captured = _fake_cli(monkeypatch, {"result": "[]", "is_error": False})
    ClaudeCliGenerator().generate([_cluster(label=marker)], WINDOW)
    assert marker in captured["input"]
    assert not any(marker in arg for arg in captured["cmd"][1:])


def test_리포_바깥_임시디렉터리에서_실행한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """리포 안에서 돌리면 CLAUDE.md 가 시스템 프롬프트에 섞여 분석이 오염된다."""
    captured = _fake_cli(monkeypatch, {"result": "[]", "is_error": False})
    ClaudeCliGenerator().generate([_cluster()], WINDOW)
    assert "hanaai-analyst-" in str(captured["cwd"])


def test_동적_시스템_프롬프트_섹션을_제외한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """빠지면 Claude Code 의 에이전트 프레이밍이 덧붙어 산문으로 답한다."""
    captured = _fake_cli(monkeypatch, {"result": "[]", "is_error": False})
    ClaudeCliGenerator().generate([_cluster()], WINDOW)
    assert "--exclude-dynamic-system-prompt-sections" in captured["cmd"]


def test_CLI_가_없으면_친절하게_실패한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gen.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="claude login"):
        ClaudeCliGenerator().generate([_cluster()], WINDOW)


# ------------------------------------------------------------------ 백엔드 교체


def test_이미_답하는_주제는_규칙_기반에서도_건너뛴다() -> None:
    out = PassthroughGenerator().generate([_cluster(existing_menu="화장실 안내")], WINDOW)
    assert out == []


def test_백엔드_세_개가_등록되어_있다() -> None:
    assert set(BACKENDS) == {"passthrough", "claude-cli", "claude-api"}


def test_기본_백엔드는_로그인된_CLI() -> None:
    assert get_generator().name == "claude-cli"


def test_백엔드를_바꿔_끼울_수_있다() -> None:
    assert get_generator("passthrough").name == "passthrough"
    assert get_generator("claude-api").name == "claude-api"


def test_모르는_백엔드는_거부한다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 분석 백엔드"):
        get_generator("gpt-9")
