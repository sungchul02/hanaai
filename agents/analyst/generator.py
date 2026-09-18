"""후보[A] → 제안[B] 변환기.

백엔드를 갈아끼울 수 있게 되어 있다. 지금은 로그인된 Claude CLI 를 쓰고,
기업 환경에서는 API 키 기반이나 사내 게이트웨이로 교체한다.

    HANAAI_ANALYST_BACKEND=claude-cli    (기본) 로그인된 claude CLI 사용. 키 불필요.
    HANAAI_ANALYST_BACKEND=claude-api    ANTHROPIC_API_KEY 사용. 기업 교체용.
    HANAAI_ANALYST_BACKEND=passthrough   LLM 없이 규칙 기반. 테스트/오프라인용.

교체 지점이 이 파일 하나로 모여 있는 것이 요점이다. runner 도 orchestrator 도
어느 백엔드인지 모른다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol

import structlog

from agents.contracts.proposal import Candidate, Evidence, FeatureProposal
from agents.detectors.base import Window

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
너는 키오스크 운영 데이터를 읽고 '무엇을 만들어야 하는가'를 정하는 분석가다.
파일을 찾지 마라. 필요한 것은 전부 이 지시문과 입력에 들어 있다.
분석 노트나 머리말을 쓰지 마라. 결과 JSON 만 낸다.

입력으로 탐지기가 이미 좁혀놓은 이상 신호 후보들을 받는다. 각 후보에는 관측값과 비교 기준,
표본 크기, 영향받은 키오스크 수가 붙어 있다.

규칙:
1. 주어진 수치를 넘어서는 사실을 만들어내지 마라. 근거가 없으면 confidence 를 낮춰라.
2. 여러 후보가 같은 원인으로 보이면 하나의 제안으로 합쳐라.
3. acceptance_criteria 는 개발자가 그대로 테스트로 옮길 수 있는 문장이어야 한다.
   "개선한다" 같은 문장은 실패다. "X 화면에서 Y 오류율이 Z% 이하" 처럼 측정 가능해야 한다.
4. 코드를 고쳐서 해결될 문제가 아니면(예: 특정 모델의 물리적 결함) scope 를 그렇게 밝히고
   proposal 에 '교체/펌웨어 대응 필요'라고 적어라. 억지로 기능 제안을 만들지 마라.
5. evidence 는 입력으로 받은 값을 그대로 옮겨라. 새로 계산하거나 반올림하지 마라.

출력 형식 (엄격):
설명이나 머리말 없이 JSON 배열만 출력한다. 마크다운 코드펜스는 써도 된다.
아래 예시의 **키 이름을 글자 그대로** 쓴다. 키를 추가하거나 이름을 바꾸면 거부된다.
proposal_id, priority, id, summary 같은 키는 존재하지 않는다.

출력 예시 (형식만 참고. 내용은 실제 후보에 맞게 채운다):
__EXAMPLE__

필드 규칙:
- scope: kiosk_app | ops_backend | device_driver | config_only 중 하나
- effort_estimate: S | M | L 중 하나
- confidence: 0 이상 1 이하의 소수
- acceptance_criteria: 최소 1개. 측정 가능한 문장
- evidence: 최소 1개. 입력 후보의 evidence 를 그대로 옮긴다
- dedupe_key: 입력 후보의 dedupe_key 를 그대로 옮긴다
"""


class ProposalGenerator(Protocol):
    name: str

    def generate(self, candidates: list[Candidate], window: Window) -> list[FeatureProposal]: ...


class PassthroughGenerator:
    """LLM 없이 후보 하나를 초안 제안 하나로 바꾼다. 자리 표시자다."""

    name = "passthrough"

    def generate(self, candidates: list[Candidate], window: Window) -> list[FeatureProposal]:
        proposals: list[FeatureProposal] = []
        for c in candidates:
            e = c.evidence
            delta = max(e.observed - e.baseline, 0.0)
            proposals.append(
                FeatureProposal(
                    title=c.signal,
                    problem=(
                        f"{e.metric} 관측값 {e.observed:.4f} / 기준 {e.baseline:.4f} "
                        f"(표본 {e.sample_size}, 키오스크 {e.affected_kiosks}대, {e.window})"
                    ),
                    hypothesis="미검증 — 분석 Agent(M4)가 채울 자리",
                    proposal="미작성 — 분석 Agent(M4)가 채울 자리",
                    scope="kiosk_app",
                    acceptance_criteria=[
                        f"{e.metric} 이(가) 기준선 {e.baseline:.4f} 의 1.2배 이하로 내려간다",
                    ],
                    evidence=[e],
                    impact_score=round(e.affected_kiosks * delta * 100, 2),
                    effort_estimate="M",
                    confidence=0.2,
                    risks=["규칙 기반 자동 생성 초안이므로 사람 검토 없이 신뢰하면 안 된다"],
                    dedupe_key=c.dedupe_key,
                )
            )
        return proposals


def _example_json() -> str:
    """예시를 계약 모델로 직접 만들어서 덤프한다.

    손으로 적은 예시는 계약이 바뀌면 조용히 틀린 것을 가르치게 된다.
    여기서 만든 예시는 정의상 항상 유효하다.
    """
    example = FeatureProposal(
        title="Bixolon SRP-330II 인쇄 실패 자동 재시도",
        problem="해당 모델의 print_failed 발생률이 동종 장치 대비 8.4배",
        hypothesis="절단 후 재급지 시퀀스 누락으로 다음 인쇄가 실패한다",
        proposal="인쇄 실패 감지 시 재급지 후 1회 자동 재시도하고 결과를 이벤트로 남긴다",
        scope="kiosk_app",
        acceptance_criteria=[
            "print_failed 발생 후 자동 재시도 성공률이 80% 이상",
            "자동 재시도로 인한 중복 인쇄가 0건",
        ],
        evidence=[
            Evidence(
                metric="printer.print_failed per device activity (Bixolon SRP-330II)",
                query_id="peripheral_error_rate",
                observed=0.1333,
                baseline=0.0158,
                sample_size=320,
                window="2026-09-11/2026-09-18",
                affected_kiosks=6,
            )
        ],
        impact_score=70.5,
        effort_estimate="M",
        confidence=0.7,
        risks=["재시도가 용지 걸림을 악화시킬 수 있어 재시도 횟수를 1회로 제한한다"],
        dedupe_key="peripheral_error_rate:2:1003",
    )
    dumped = example.model_dump(mode="json")
    dumped.pop("contract_version", None)
    return json.dumps([dumped], ensure_ascii=False, indent=2)


def build_system_prompt() -> str:
    """스키마와 예시를 계약 모델에서 직접 뽑아 넣는다.

    프롬프트에 손으로 적어두면 계약이 바뀔 때 조용히 어긋난다.
    """
    # 전체 JSON Schema 는 붙이지 않는다. 붙여봤더니 오히려 모델이 자기 형식으로 이탈했다
    # (proposal_id, source 같은 키를 지어냄). 유효한 예시 하나가 스키마 덤프보다 강하다.
    # % 포맷도 쓰지 않는다. 프롬프트에 퍼센트가 들어 있어 포맷 지정자로 해석된다.
    return SYSTEM_PROMPT.replace("__EXAMPLE__", _example_json())


def build_user_prompt(candidates: list[Candidate], window: Window) -> str:
    payload = [c.model_dump(mode="json") for c in candidates]
    return (
        f"분석 구간: {window.label}\n"
        f"탐지된 후보 {len(candidates)}건:\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "위 후보들을 FeatureProposal JSON 배열로 변환해라."
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_proposals(text: str) -> tuple[list[FeatureProposal], list[str]]:
    """모델 응답에서 제안 목록을 뽑는다. (제안, 버려진 이유들) 을 돌려준다.

    검증 실패 건을 조용히 버리지 않고 이유를 남기는 것이 중요하다.
    프롬프트가 나빠진 것을 알아채는 유일한 신호다.
    """
    errors: list[str] = []
    body = text.strip()

    match = _FENCE.search(body)
    if match:
        body = match.group(1).strip()
    if not body.startswith("["):
        start, end = body.find("["), body.rfind("]")
        if start == -1 or end == -1:
            return [], [f"JSON 배열을 찾지 못함: {text[:200]}"]
        body = body[start : end + 1]

    try:
        raw_items = json.loads(body)
    except json.JSONDecodeError as exc:
        return [], [f"JSON 파싱 실패: {exc}"]
    if not isinstance(raw_items, list):
        return [], ["최상위가 배열이 아님"]

    proposals: list[FeatureProposal] = []
    for index, item in enumerate(raw_items):
        try:
            proposals.append(FeatureProposal.model_validate(item))
        except Exception as exc:  # 계약 위반은 버리되 기록한다
            errors.append(f"[{index}] {type(exc).__name__}: {str(exc)[:300]}")
    return proposals, errors


class ClaudeCliGenerator:
    """로그인된 Claude CLI 를 헤드리스로 호출한다. API 키가 필요 없다.

    - 프롬프트는 argv 가 아니라 stdin 으로 넘긴다. 후보가 늘면 argv 길이 제한에 걸린다.
    - 임시 디렉터리에서 실행한다. 그러지 않으면 이 리포의 CLAUDE.md 가 시스템 프롬프트에
      섞여 들어가서, 분석 결과가 '지금 열려 있는 코드'에 오염된다.
    - 도구를 전부 막는다. 이 호출은 순수 텍스트 변환이어야 한다.
    """

    name = "claude-cli"

    def __init__(
        self,
        model: str = "opus",
        timeout: int = 300,
        executable: str = "claude",
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.executable = executable
        self.last_usage: dict[str, Any] | None = None
        self.last_errors: list[str] = []

    def _resolve_executable(self) -> str:
        """실제 경로로 바꿔서 넘긴다.

        Windows 에서 claude 는 .cmd 라, 이름만 넘기면 CreateProcess 가 PATHEXT 를
        따라가지 않아 FileNotFoundError 가 난다. shell=True 를 쓰는 대신 경로를 푼다.
        """
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise RuntimeError(
                f"'{self.executable}' 실행 파일을 찾을 수 없다. "
                "Claude CLI 를 설치하고 로그인했는지 확인하라 (claude login)."
            )

        path = Path(resolved)
        if path.suffix.lower() in (".cmd", ".bat"):
            # Windows npm 셰임(.cmd)은 cmd.exe 를 거친다. 그 과정에서 개행이 든 인자가
            # 잘려나가, --system-prompt 가 통째로 사라진 채 실행된다. 증상이 고약한 게,
            # 오류가 아니라 '기본 시스템 프롬프트로 조용히 동작' 이라 원인을 찾기 어렵다.
            # npm 패키지가 함께 설치하는 네이티브 실행 파일을 직접 쓴다.
            native = (
                path.parent
                / "node_modules"
                / "@anthropic-ai"
                / "claude-code"
                / "bin"
                / "claude.exe"
            )
            if native.exists():
                return str(native)
        return resolved

    def _command(self, executable: str) -> list[str]:
        return [
            executable,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--system-prompt",
            build_system_prompt(),
            # 이 플래그가 없으면 Claude Code 의 동적 섹션(작업 디렉터리, 에이전트 프레이밍)이
            # 우리 시스템 프롬프트 뒤에 붙는다. 그러면 모델이 자기를 코딩 에이전트로 여기고
            # "스키마 파일을 찾아보겠다" 며 산문으로 답한다. 실제로 그렇게 두 번 실패했다.
            "--exclude-dynamic-system-prompt-sections",
            # 도구 없이 순수 생성만 한다
            "--disallowed-tools",
            "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Task",
        ]

    def _invoke(self, user_prompt: str) -> str:
        executable = self._resolve_executable()
        with tempfile.TemporaryDirectory(prefix="hanaai-analyst-") as workdir:
            completed = subprocess.run(
                self._command(executable),
                input=user_prompt,
                cwd=workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"claude CLI 실패 (exit {completed.returncode}): "
                f"{(completed.stderr or completed.stdout)[:500]}"
            )

        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"CLI 응답이 JSON 이 아니다: {exc}") from exc

        if envelope.get("is_error"):
            raise RuntimeError(f"claude CLI 오류 응답: {str(envelope)[:500]}")

        self.last_usage = {
            "backend": self.name,
            "model": self.model,
            "cost_usd": envelope.get("total_cost_usd"),
            "duration_ms": envelope.get("duration_ms"),
            "usage": envelope.get("usage"),
        }
        result = envelope.get("result")
        if not isinstance(result, str):
            raise RuntimeError("CLI 응답에 result 문자열이 없다")
        return result

    def generate(self, candidates: list[Candidate], window: Window) -> list[FeatureProposal]:
        if not candidates:
            return []
        text = self._invoke(build_user_prompt(candidates, window))
        proposals, errors = parse_proposals(text)
        self.last_errors = errors
        if errors:
            log.warning("proposal_validation_failed", count=len(errors), errors=errors[:3])
        log.info("proposals_generated", backend=self.name, count=len(proposals))
        return proposals


class ClaudeApiGenerator:
    """API 키 기반. 기업 환경 교체용.

    CLI 는 개인 로그인에 묶여 있어 서버에서 무인으로 돌리기 어렵다. 운영에서는 이쪽을 쓴다.
    """

    name = "claude-api"

    def __init__(self, model: str = "claude-opus-5", max_tokens: int = 8000) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.last_usage: dict[str, Any] | None = None
        self.last_errors: list[str] = []

    def generate(self, candidates: list[Candidate], window: Window) -> list[FeatureProposal]:
        if not candidates:
            return []
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - 설치 여부에 따른 분기
            raise RuntimeError(
                "anthropic SDK 가 없다. pip install 'hanaai[agents]' 후 "
                "ANTHROPIC_API_KEY 를 설정하라."
            ) from exc

        client = anthropic.Anthropic()
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=build_system_prompt(),
            messages=[{"role": "user", "content": build_user_prompt(candidates, window)}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        self.last_usage = {
            "backend": self.name,
            "model": self.model,
            "usage": {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        }
        proposals, errors = parse_proposals(text)
        self.last_errors = errors
        if errors:
            log.warning("proposal_validation_failed", count=len(errors), errors=errors[:3])
        return proposals


BACKENDS: dict[str, Any] = {
    "passthrough": PassthroughGenerator,
    "claude-cli": ClaudeCliGenerator,
    "claude-api": ClaudeApiGenerator,
}


def get_generator(backend: str | None = None, model: str | None = None) -> ProposalGenerator:
    """설정에 따라 생성기를 고른다. 호출부는 어느 백엔드인지 몰라도 된다."""
    from services.common.config import get_settings

    settings = get_settings()
    name = backend or settings.analyst_backend
    if name not in BACKENDS:
        raise ValueError(f"알 수 없는 분석 백엔드: {name} (가능: {sorted(BACKENDS)})")

    factory = BACKENDS[name]
    if name == "passthrough":
        return factory()  # type: ignore[no-any-return]

    # 모델 이름은 백엔드마다 표기가 다르다(CLI 는 'opus' 별칭, API 는 'claude-opus-5').
    # 설정이 비어 있으면 각 클래스의 기본값을 쓰게 둔다.
    chosen = model or settings.analyst_model
    kwargs = {"model": chosen} if chosen else {}
    return factory(**kwargs)  # type: ignore[no-any-return]
