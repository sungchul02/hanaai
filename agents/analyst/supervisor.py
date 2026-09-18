"""상위 Agent — 무엇을 조사할지 정하고 하위에게 명령한다.

    주제 목록
      ↓  [LLM] 분류: 안내 가치가 있는 질문인가
    가치 있는 주제만
      ↓  명령: "이 주제의 근거를 찾아와라"
    하위 Agent (EvidenceAgent)
      ↓  근거 + 답할 수 있는지 판정
    답변 생성 → 검증 → 사람 검토

왜 상위가 먼저 거르는가:
  주제 100개를 전부 조사시키면 비용이 100배가 된다. '너 몇 살이야' 같은 잡담에
  문서를 뒤지게 할 이유가 없다. 싼 판단(분류)을 먼저 하고 비싼 일(조사·생성)을 뒤에 둔다.

왜 근거를 찾은 뒤에 생성하는가:
  근거 없이 쓰면 지어낸다. 실제로 그랬고, 그래서 안내 문구가 전부
  "(확인 후 입력 필요)" 였다. 문서에서 찾아온 것만 쓰게 하면 그 자리가 사실로 채워진다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from sqlalchemy.orm import Session

from agents.analyst.tuning import DEFAULT_TUNING, Tuning
from agents.knowledge.evidence_agent import EvidenceAgent, EvidenceReport
from agents.knowledge.retriever import has_documents

log = structlog.get_logger(__name__)

TRIAGE_PROMPT = """\
너는 안내 키오스크 운영자다. 사용자 질문을 주제별로 묶은 목록을 받는다.
각 주제에 대해 두 가지를 한다.

  1. **안내 콘텐츠로 만들 가치가 있는지** 판단한다.
  2. 가치가 있으면 **어느 갈래의 업무인지** 분류한다.

파일을 찾지 마라. 설명이나 머리말을 쓰지 마라. JSON 만 낸다.

가치 있음(keep)의 기준:
- 이 기관을 찾는 사람이 실제로 알아야 하는 것인가. 위치, 시간, 절차, 준비물, 연락처 등.
- 반복해서 물어보는가. 한 번 답을 만들어두면 계속 쓰이는가.

가치 없음(drop)의 기준와 이유 예:
- 잡담·장난: "너 몇 살이야", "심심해"
- 욕설·비방
- 개인 신상이나 특정 민원의 개별 처리 상황 — 안내문으로 만들 수 없다
- 이 기관 업무와 무관한 것

category 는 관리자가 한 번에 훑어보고 판단할 수 있게 묶는 단위다.
비슷한 성격의 주제에는 **같은 이름을 글자 그대로** 붙여라. 아래 갈래를 우선 쓰고,
어디에도 안 맞을 때만 새 이름을 만든다. 새로 만들 때도 짧은 명사구로 쓴다.

  시설 이용    주차, 화장실, 수유실, 엘리베이터, 와이파이, 흡연구역 등 청사 편의시설
  부서 안내    부서 위치, 층별 배치, 담당 창구
  민원 처리    증명서 발급, 여권, 전입신고, 준비물, 수수료
  운영 시간    업무시간, 휴무일, 야간 운영
  찾아오는 길  주소, 대중교통, 주차장 진입
  연락처       전화번호, 문의처

출력 형식. 입력의 label 을 글자 그대로 옮긴다. drop 인 주제는 category 를 비운다:
[
  {"label": "주차장 어디예요?", "keep": true, "category": "시설 이용",
   "reason": "청사 이용자가 반복해서 묻는 위치 안내"},
  {"label": "세정과 몇 층이에요?", "keep": true, "category": "부서 안내",
   "reason": "방문 전 반드시 필요한 정보"},
  {"label": "너 몇 살이야?", "keep": false, "category": "", "reason": "잡담"}
]
"""

# 분류가 비었을 때 쓸 이름. 빈칸으로 두면 화면에서 묶이지 않고 흩어진다.
DEFAULT_CATEGORY = "기타"


class Completer(Protocol):
    name: str

    def complete(self, user_prompt: str) -> str: ...


@dataclass
class Verdict:
    label: str
    keep: bool
    reason: str
    category: str = DEFAULT_CATEGORY


@dataclass
class Assignment:
    """상위가 하위에게 내린 명령과 그 결과."""

    label: str
    keep_reason: str
    report: EvidenceReport
    category: str = DEFAULT_CATEGORY


@dataclass
class SupervisionResult:
    kept: list[Assignment] = field(default_factory=list)
    dropped: list[Verdict] = field(default_factory=list)
    unanswerable: list[Assignment] = field(default_factory=list)
    # 근거 문서를 가진 고객사인가. false 면 근거 없이도 초안을 만든다.
    grounded: bool = False

    def as_stats(self) -> dict[str, int | bool]:
        return {
            "triage_kept": len(self.kept) + len(self.unanswerable),
            "triage_dropped": len(self.dropped),
            "evidence_found": sum(1 for a in self.kept if a.report.answerable),
            "evidence_missing": len(self.unanswerable),
            "grounded": self.grounded,
        }


def _parse_verdicts(text: str) -> list[Verdict]:
    body = text.strip()
    start, end = body.find("["), body.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        rows = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return []
    verdicts: list[Verdict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or "label" not in row:
            continue
        verdicts.append(
            Verdict(
                label=str(row["label"]),
                keep=bool(row.get("keep")),
                reason=str(row.get("reason") or ""),
                category=str(row.get("category") or "").strip() or DEFAULT_CATEGORY,
            )
        )
    return verdicts


class SupervisorAgent:
    """분류 → 명령 → 취합. 답변 문구는 쓰지 않는다. 그건 다음 단계의 일이다."""

    name = "supervisor"

    def __init__(
        self,
        session: Session,
        customer_id: int,
        completer: Completer | None = None,
        tuning: Tuning = DEFAULT_TUNING,
    ) -> None:
        self.session = session
        self.customer_id = customer_id
        self.completer = completer
        self.tuning = tuning
        self.worker = EvidenceAgent(session, customer_id, completer, tuning)
        # 근거 문서가 있는 고객사에서만 "근거 없으면 생성 안 함" 규칙을 적용한다.
        self.grounded = has_documents(session, customer_id)

    def triage(self, clusters: list[dict[str, Any]]) -> list[Verdict]:
        """안내 가치가 있는 주제만 고른다. 싼 판단을 먼저 한다."""
        if not clusters:
            return []
        if self.completer is None:
            # LLM 이 없으면 전부 통과시킨다. 규칙이 이미 욕설·짧은 입력은 걸렀다.
            return [Verdict(str(c["label"]), True, "LLM 미연결") for c in clusters]

        payload = [
            {
                "label": c["label"],
                "question_count": c["question_count"],
                "sample_questions": c.get("sample_questions", [])[:4],
            }
            for c in clusters
        ]
        raw = self.completer.complete(
            TRIAGE_PROMPT + "\n\n주제 목록:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        verdicts = _parse_verdicts(raw)
        if not verdicts:
            # 판단에 실패하면 버리지 않고 전부 통과시킨다. 조용히 사라지는 것이 더 나쁘다.
            log.warning("triage_parse_failed", clusters=len(clusters))
            return [Verdict(str(c["label"]), True, "분류 실패 — 통과") for c in clusters]
        return verdicts

    def run(self, clusters: list[dict[str, Any]]) -> SupervisionResult:
        by_label = {str(c["label"]): c for c in clusters}
        result = SupervisionResult()
        result.grounded = self.grounded

        for verdict in self.triage(clusters):
            cluster = by_label.get(verdict.label)
            if cluster is None:
                continue
            if not verdict.keep:
                result.dropped.append(verdict)
                continue

            # 하위 Agent 에게 명령을 내린다
            questions = list(cluster.get("_questions") or cluster.get("sample_questions") or [])
            report = self.worker.collect(verdict.label, questions)
            assignment = Assignment(verdict.label, verdict.reason, report, verdict.category)
            # 근거 문서를 아직 하나도 안 채운 고객사라면 근거를 요구하지 않는다.
            # 요구하면 지식 저장소를 채우기 전까지 제안이 한 건도 안 나온다.
            if report.answerable or not self.grounded:
                result.kept.append(assignment)
            else:
                result.unanswerable.append(assignment)

        log.info(
            "supervision_done",
            kept=len(result.kept),
            dropped=len(result.dropped),
            unanswerable=len(result.unanswerable),
        )
        return result
