"""주제 묶음[1단계] → 추천 콘텐츠[2단계] 변환기.

백엔드를 갈아끼울 수 있게 되어 있다. 지금은 로그인된 Claude CLI 를 쓰고,
기업 환경에서는 API 키 기반이나 사내 게이트웨이로 교체한다.

    HANAAI_ANALYST_BACKEND=claude-cli    (기본) 로그인된 claude CLI 사용. 키 불필요.
    HANAAI_ANALYST_BACKEND=claude-api    ANTHROPIC_API_KEY 사용. 기업 교체용.
    HANAAI_ANALYST_BACKEND=passthrough   LLM 없이 규칙 기반. 테스트/오프라인용.

교체 지점이 이 파일 하나로 모여 있는 것이 요점이다. runner 도 API 도 어느 백엔드인지 모른다.
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

from agents.contracts.proposal import ClusterEvidence, ContentProposal

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
너는 안내 키오스크의 CMS 콘텐츠 담당자다.
사용자들이 키오스크에 실제로 던진 질문을 주제별로 묶은 목록을 받는다.
그중 '새 안내 메뉴를 만들 가치가 있는 주제' 만 골라 메뉴 초안을 쓴다.

파일을 찾지 마라. 필요한 것은 전부 이 지시문과 입력에 들어 있다.
분석 노트나 머리말을 쓰지 마라. 결과 JSON 만 낸다.

고르는 기준:
1. 안내에 필요한 질문인가. 장난('너 몇 살이야'), 잡담, 욕설 주제는 제외한다.
2. 이미 답하고 있는가. existing_menu 가 있고 미응답이 적으면 제외한다.
   그런 주제는 콘텐츠가 없는 게 아니라 매칭이 안 되는 것이라 다른 문제다.
3. 표본이 너무 적으면(2~3건) 제외한다. 우연일 수 있다.

**한 주제를 여러 메뉴로 쪼개라.** 이게 가장 중요하다.
사용자는 "주차 요금 얼마예요" 와 "주차장 몇 시까지 해요" 를 따로 묻는다.
그런데 '주차 안내' 메뉴 하나에 면수·시간·요금·결제를 다 담으면, 요금만 묻는 사람에게도
344면 이야기부터 통째로 읽힌다. 키오스크 앞에 선 사람은 그걸 끝까지 읽지 않는다.

  나쁨: [주차 안내] 총 344면입니다. 평일 08시부터 18시까지 운영합니다.
                  최초 2시간 무료이고 30분당 500원입니다. 카드결제 정산기에서...
  좋음: [주차 요금]     최초 2시간 무료, 이후 30분당 500원, 1일 최대 8,000원입니다.
        [주차 운영시간] 평일 08시부터 18시까지 운영합니다.
        [주차장 규모]   총 344면이며 지상 241면, 지하 103면입니다.

나누는 기준은 '사용자가 따로 물어보는가' 다.
근거 문서의 heading 이 이미 그 단위로 나뉘어 있으니 그것을 따라가면 된다.
근거 조각 하나가 메뉴 하나라고 생각해도 대체로 맞다.

다만 억지로 쪼개지는 마라. 질문이 그 항목을 따로 묻지 않으면 하나로 둔다.
'지상 241면' 과 '지하 103면' 을 따로 묻는 사람은 없다.

**쪼갠 메뉴에 옆 메뉴 이야기를 쓰지 마라.** 이게 자주 틀린다.
'주차장 운영시간' 메뉴에 "주차 요금은 (확인 후 입력 필요)" 라고 쓰면 안 된다.
요금은 모르는 것이 아니라 '주차 요금' 메뉴가 따로 다루는 것이다.
'(확인 후 입력 필요)' 는 **근거 문서 어디에도 없는 사실**에만 쓴다.
다른 메뉴로 넘긴 내용에는 쓰지 않는다. 각 메뉴는 자기 항목만 말하고 끝낸다.

메뉴 초안을 쓸 때:
- title 은 키오스크 화면에 뜨는 짧은 메뉴명이다. **무엇에 대한 답인지가 제목에 드러나야 한다.**
  '주차 안내' 보다 '주차 요금' 이 낫다. 앞은 무엇이 들었는지 열어봐야 알고, 뒤는 바로 안다.
- body 는 사용자가 그대로 읽고 끝낼 수 있는 완성된 안내 문장이다.
  **묻지 않은 것을 덧붙이지 마라.** 요금 메뉴에 운영시간을 끼워 넣으면 쪼갠 의미가 없다.
  **evidence_from_documents 에 적힌 내용만 사실로 써라.** 그것이 기관이 공개한 원문이다.
  주소·시간·요금·층수 같은 숫자는 원문 그대로 옮긴다. 반올림하거나 바꿔 쓰지 마라.
  **거기 없는 사실은 지어내지 마라.** 근거 문서 전체를 뒤져도 없는 항목만
  '(확인 후 입력 필요)' 로 남겨 관리자가 채우게 한다. 틀린 안내가 나가는 것이 최악이다.
  반대로, 근거에 있는데 다른 메뉴가 다루는 내용이라면 그냥 쓰지 않고 넘어간다.
- keywords 는 이 메뉴가 **무엇에 대한 것인지** 나타내는 낱말이다. 질문에 나온 표현을 쓰되,
  '어디', '알려주세요', '몇 층', '가능' 같은 **말투는 넣지 마라.** 어느 주제에나 붙는 말이라
  넣으면 상관없는 질문까지 이 메뉴로 답해진다. '화장실 어디야' 가 무인발급기 안내로
  답해진 적이 있고, 원인이 키워드의 '어디' 였다.
- evidence 는 입력으로 받은 숫자를 그대로 옮긴다. 새로 계산하지 마라.
  (evidence 는 질문 통계이고, evidence_from_documents 는 근거 문서다. 서로 다른 것이다.)
- source_labels 에는 이 추천이 나온 주제의 label 을 **글자 그대로** 옮긴다.
  여러 주제를 하나로 합쳤으면 합친 주제를 전부 적는다. 이 값으로 추천과 질문을 잇는다.
  한 주제를 여러 메뉴로 쪼갰다면 각 메뉴에 같은 label 을 적으면 된다.

출력:
JSON 배열만 출력한다. 아래 예시의 키 이름을 글자 그대로 쓴다.
키를 더하거나 이름을 바꾸면 거부된다. id, menu_id, priority 같은 키는 존재하지 않는다.
제안할 주제가 하나도 없으면 빈 배열 [] 을 출력한다.

__EXAMPLE__

값 제약:
- title: 2~40자
- body: 10~500자
- keywords: 1~10개
- confidence: 0 이상 1 이하
- evidence.sample_questions: 최소 1개
"""


class ProposalGenerator(Protocol):
    name: str

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]: ...


def _example_json() -> str:
    """예시를 계약 모델로 직접 만들어서 덤프한다.

    손으로 적은 예시는 계약이 바뀌면 조용히 틀린 것을 가르치게 된다.
    여기서 만든 예시는 정의상 항상 유효하다.

    예시를 둘 넣는 이유: 주제 하나('주차') 가 메뉴 둘로 쪼개지는 모습을 보여주기 위해서다.
    말로만 "쪼개라" 고 하면 모델은 여전히 통짜 메뉴 하나를 낸다. 실제로 그랬다.
    """
    reason = "최근 7일간 주차 관련 질문이 87건 있었으나 관련 메뉴가 없어 전부 답하지 못했다"
    labels = ["주차장 어디예요?", "차 어디에 대면 돼요?"]
    evidence = ClusterEvidence(
        question_count=87,
        unanswered_count=87,
        window="2026-09-11/2026-09-18",
        sample_questions=["주차 요금 얼마예요?", "주차장 몇 시까지 해요?", "주차 가능한가요?"],
        existing_menu=None,
    )
    examples = [
        ContentProposal(
            title="주차 요금",
            body=(
                "천안시청 주차 요금은 최초 2시간 무료입니다. "
                "2시간을 초과하면 30분당 500원이며, 1일 최대 8,000원입니다."
            ),
            reason=reason,
            keywords=["주차요금", "주차비", "주차 무료", "주차 유료"],
            source_labels=labels,
            evidence=evidence,
            impact_score=87.0,
            confidence=0.8,
            dedupe_key="parking-fee",
        ),
        ContentProposal(
            title="주차장 운영시간",
            body="천안시청 주차장은 평일 08시부터 18시까지 운영합니다.",
            reason=reason,
            keywords=["주차 운영시간", "주차장 시간", "주차 몇시"],
            source_labels=labels,
            evidence=evidence,
            impact_score=87.0,
            confidence=0.8,
            dedupe_key="parking-hours",
        ),
    ]
    dumped = []
    for example in examples:
        row = example.model_dump(mode="json")
        row.pop("contract_version", None)
        dumped.append(row)
    return json.dumps(dumped, ensure_ascii=False, indent=2)


def build_system_prompt() -> str:
    # 전체 JSON Schema 는 붙이지 않는다. 붙이면 오히려 모델이 자기 형식으로 이탈한다.
    # 유효한 예시 하나가 스키마 덤프보다 강하다.
    # 퍼센트 포맷도 쓰지 않는다. 프롬프트 본문에 퍼센트가 들어 있으면 터진다.
    return SYSTEM_PROMPT.replace("__EXAMPLE__", _example_json())


def build_user_prompt(clusters: list[dict[str, Any]], window: str) -> str:
    # '_' 로 시작하는 키는 내부용이다. 검증에 쓸 질문 원본 같은 것이 여기 들어가는데,
    # 수백 건을 프롬프트에 실으면 비용만 늘고 판단에는 도움이 안 된다.
    visible = [
        {key: value for key, value in cluster.items() if not key.startswith("_")}
        for cluster in clusters
    ]
    return (
        f"분석 구간: {window}\n"
        f"질문 주제 {len(visible)}건:\n\n"
        f"{json.dumps(visible, ensure_ascii=False, indent=2)}\n\n"
        "위 주제 중 새 안내 메뉴가 필요한 것만 골라 ContentProposal JSON 배열로 만들어라."
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_proposals(text: str) -> tuple[list[ContentProposal], list[str]]:
    """모델 응답에서 제안 목록을 뽑는다. (제안, 버려진 이유들) 을 돌려준다.

    검증 실패를 조용히 버리지 않고 이유를 남긴다. 프롬프트가 나빠진 것을 알아채는 유일한 신호다.
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

    proposals: list[ContentProposal] = []
    for index, item in enumerate(raw_items):
        try:
            proposals.append(ContentProposal.model_validate(item))
        except Exception as exc:  # 계약 위반은 버리되 기록한다
            errors.append(f"[{index}] {type(exc).__name__}: {str(exc)[:300]}")
    return proposals, errors


class PassthroughGenerator:
    """LLM 없이 주제 하나를 초안 하나로 바꾼다. 오프라인·테스트용 자리 표시자."""

    name = "passthrough"

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]:
        proposals: list[ContentProposal] = []
        for cluster in clusters:
            if cluster.get("existing_menu"):
                continue
            count = int(cluster["question_count"])
            unanswered = int(cluster["unanswered_count"])
            label = str(cluster["label"])
            proposals.append(
                ContentProposal(
                    title=label[:40],
                    body="(LLM 미연결 — 관리자가 안내 문구를 직접 작성해야 합니다)",
                    reason=(
                        f"'{label}' 주제로 {count}건이 접수되었고 {unanswered}건이 답하지 못했다"
                    ),
                    keywords=list(cluster.get("keywords") or [label[:10]])[:10] or [label[:10]],
                    source_labels=[label],
                    evidence=ClusterEvidence(
                        question_count=count,
                        unanswered_count=unanswered,
                        window=window,
                        sample_questions=list(cluster["sample_questions"])[:5],
                        existing_menu=cluster.get("existing_menu"),
                    ),
                    impact_score=float(unanswered),
                    confidence=0.2,
                    dedupe_key=cluster.get("dedupe_key"),
                )
            )
        return proposals


class ClaudeCliGenerator:
    """로그인된 Claude CLI 를 헤드리스로 호출한다. API 키가 필요 없다.

    여기 세 가지는 전부 실제로 당한 뒤에 넣은 것이다. 빠지면 조용히 이상하게 동작한다.
    - 프롬프트는 argv 가 아니라 stdin 으로 넘긴다. 주제가 늘면 argv 길이 제한에 걸린다.
    - 임시 디렉터리에서 실행한다. 리포 안에서 돌리면 CLAUDE.md 가 섞여 들어간다.
    - 도구를 전부 막는다. 이 호출은 순수 텍스트 변환이어야 한다.
    """

    name = "claude-cli"

    def __init__(self, model: str = "opus", timeout: int = 300, executable: str = "claude") -> None:
        self.model = model
        self.timeout = timeout
        self.executable = executable
        self.last_usage: dict[str, Any] | None = None
        self.last_errors: list[str] = []
        # 한 번의 분석에서 LLM 은 여러 번 불린다. 분류 · 근거 조사 · 초안 · 수정.
        # 마지막 호출만 기록하면 비용이 실제보다 훨씬 적게 보인다.
        self.total_usage: dict[str, Any] = {"calls": 0, "cost_usd": 0.0, "duration_ms": 0}

    def _accumulate(self, cost: float | None, duration: int | None) -> None:
        self.total_usage["calls"] += 1
        self.total_usage["cost_usd"] = round(
            float(self.total_usage["cost_usd"]) + float(cost or 0), 6
        )
        self.total_usage["duration_ms"] += int(duration or 0)
        self.total_usage["backend"] = self.name
        self.total_usage["model"] = self.model

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.executable)
        if resolved is None:
            raise RuntimeError(
                f"'{self.executable}' 실행 파일을 찾을 수 없다. "
                "Claude CLI 를 설치하고 로그인했는지 확인하라 (claude login)."
            )
        path = Path(resolved)
        if path.suffix.lower() in (".cmd", ".bat"):
            # Windows npm 셰임(.cmd)은 cmd.exe 를 거치면서 개행이 든 인자를 잘라먹는다.
            # --system-prompt 가 통째로 사라진 채 '기본 프롬프트로 정상 동작' 해서 찾기 어렵다.
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
            # 없으면 Claude Code 의 에이전트 프레이밍이 덧붙어, 모델이 "스키마 파일을
            # 찾아보겠다" 며 산문으로 답한다.
            "--exclude-dynamic-system-prompt-sections",
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
        self._accumulate(envelope.get("total_cost_usd"), envelope.get("duration_ms"))
        result = envelope.get("result")
        if not isinstance(result, str):
            raise RuntimeError("CLI 응답에 result 문자열이 없다")
        return result

    def complete(self, user_prompt: str) -> str:
        """프롬프트 하나를 보내고 원문 응답을 받는다. 콘텐츠 Agent 의 수정 요청이 쓴다."""
        return self._invoke(user_prompt)

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]:
        if not clusters:
            return []
        text = self._invoke(build_user_prompt(clusters, window))
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

    def complete(self, user_prompt: str) -> str:
        """프롬프트 하나를 보내고 원문 응답을 받는다. 콘텐츠 Agent 의 수정 요청이 쓴다."""
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
            messages=[{"role": "user", "content": user_prompt}],
        )
        self.last_usage = {
            "backend": self.name,
            "model": self.model,
            "usage": {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        }
        return "".join(block.text for block in message.content if block.type == "text")

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]:
        if not clusters:
            return []
        text = self.complete(build_user_prompt(clusters, window))
        proposals, errors = parse_proposals(text)
        self.last_errors = errors
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
    chosen = model or settings.analyst_model
    kwargs = {"model": chosen} if chosen else {}
    return factory(**kwargs)  # type: ignore[no-any-return]
