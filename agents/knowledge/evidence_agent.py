"""근거 수집 하위 Agent.

상위 Agent 가 "이 주제의 근거를 찾아와라" 고 명령하면 실행된다.

하는 일은 둘이다.
  1. [도구] retriever.search_many — 문서 저장소에서 관련 조각을 긁어온다
  2. [LLM] 긁어온 조각 중 실제로 이 질문에 답이 되는 것만 고르고, 답할 수 있는지 판정한다

2번이 필요한 이유: 낱말 기반 검색은 '주차' 가 들어갔다는 이유로 엉뚱한 조각도 물어온다.
그걸 그대로 답변 생성에 넘기면 근거 없는 문장이 섞인다. 골라내는 판단이 한 단계 필요하다.

**근거가 없으면 없다고 답한다.** 이게 이 Agent 의 존재 이유다. 지어내지 않기 위해
문서를 둔 것이므로, 문서에 없으면 "모른다" 가 정답이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from sqlalchemy.orm import Session

from agents.analyst.tuning import DEFAULT_TUNING, Tuning
from agents.knowledge.retriever import Evidence, search_many

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """\
너는 안내 키오스크의 자료 조사 담당이다.
주제 하나와, 문서에서 찾아온 근거 후보들을 받는다.
그중 이 주제에 실제로 답이 되는 조각만 고른다.

파일을 찾지 마라. 필요한 것은 전부 이 지시문과 입력에 들어 있다.
설명이나 머리말을 쓰지 마라. JSON 만 낸다.

판단 기준:
- 근거 후보는 낱말이 겹친다는 이유로 딸려온 것도 섞여 있다. 주제와 무관하면 버려라.
- 질문이 묻는 것에 **직접** 답하는 조각만 고른다. 주변 정보는 버려라.
- same_document 가 true 인 조각은 낱말이 걸려서가 아니라 **같은 문서라서 함께 온 것**이다.
  버리지 말고 먼저 읽어라. "층별 안내를 보여달라" 같은 질문은 문서 전체가 답이다.
  일부만 고르면 안내문에 "나머지는 확인 후 입력 필요" 라고 쓰게 되는데,
  **자료에 답이 있는데 관리자에게 채워 넣으라고 하는 것은 틀린 답이다.**
- 고른 조각이 하나도 없으면 answerable 을 false 로 두고 chunk_ids 를 비워라.
  **문서에 없는 사실을 채워 넣지 마라.** 모르는 것은 모른다고 하는 것이 정답이다.
- missing 에는 답하기 위해 더 필요한 정보를 적어라. 관리자가 문서를 보강할 근거가 된다.

출력 형식:
{
  "answerable": true,
  "chunk_ids": [12, 15],
  "summary": "근거를 바탕으로 이 주제에 무엇을 안내할 수 있는지 두세 문장",
  "missing": []
}

답할 수 없으면:
{
  "answerable": false,
  "chunk_ids": [],
  "summary": "",
  "missing": ["수유실 위치가 문서에 없다"]
}
"""


class Completer(Protocol):
    name: str

    def complete(self, user_prompt: str) -> str: ...


@dataclass
class EvidenceReport:
    """하위 Agent 가 상위에 돌려주는 보고."""

    topic: str
    answerable: bool
    evidence: list[Evidence] = field(default_factory=list)
    summary: str = ""
    missing: list[str] = field(default_factory=list)
    candidates_seen: int = 0

    def as_prompt_block(self) -> str:
        """답변 생성 단계에 넘길 근거 묶음."""
        return "\n".join(f"- {e.cite()}" for e in self.evidence)

    def as_json(self) -> dict[str, Any]:
        return {
            "answerable": self.answerable,
            "summary": self.summary,
            "missing": self.missing,
            "sources": [
                {"title": e.document_title, "url": e.document_url, "heading": e.heading}
                for e in self.evidence
            ],
        }


def _build_prompt(topic: str, questions: list[str], candidates: list[Evidence]) -> str:
    payload = [
        {
            "chunk_id": e.chunk_id,
            "document": e.document_title,
            "heading": e.heading,
            "text": e.text,
            "same_document": e.sibling,
        }
        for e in candidates
    ]
    return (
        f"주제: {topic}\n"
        f"이 주제로 들어온 질문들:\n"
        + "\n".join(f"- {q}" for q in questions[:6])
        + "\n\n근거 후보:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n이 주제에 답이 되는 조각만 골라라."
    )


def _parse(text: str) -> dict[str, Any]:
    body = text.strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class EvidenceAgent:
    """상위 Agent 의 명령을 받아 근거를 찾아오는 하위 Agent."""

    name = "evidence-agent"

    def __init__(
        self,
        session: Session,
        customer_id: int,
        completer: Completer | None,
        tuning: Tuning = DEFAULT_TUNING,
    ) -> None:
        self.session = session
        self.customer_id = customer_id
        self.completer = completer
        self.tuning = tuning

    def collect(self, topic: str, questions: list[str]) -> EvidenceReport:
        """명령 하나를 처리한다. topic 은 상위가 지정한 조사 대상."""
        candidates = search_many(
            self.session, self.customer_id, [topic, *questions[:4]],
            limit=self.tuning.evidence_limit, min_score=self.tuning.evidence_min_score,
        )
        if not candidates:
            return EvidenceReport(
                topic=topic,
                answerable=False,
                missing=[f"'{topic}' 에 대한 근거 문서가 없다"],
                candidates_seen=0,
            )

        if self.completer is None:
            # LLM 이 없으면 검색 결과를 그대로 쓴다. 고르는 판단만 빠진다.
            # 같은 문서라서 딸려온 조각은 고를 사람이 없으니 뺀다.
            direct = [e for e in candidates if not e.sibling]
            return EvidenceReport(
                topic=topic,
                answerable=bool(direct),
                evidence=direct,
                summary="(LLM 미연결 — 검색 결과를 그대로 사용)",
                candidates_seen=len(candidates),
            )

        raw = self.completer.complete(
            SYSTEM_PROMPT + "\n\n" + _build_prompt(topic, questions, candidates)
        )
        parsed = _parse(raw)
        chosen_ids = {int(cid) for cid in parsed.get("chunk_ids", []) if str(cid).isdigit()}
        chosen = [e for e in candidates if e.chunk_id in chosen_ids]

        answerable = bool(parsed.get("answerable")) and bool(chosen)
        report = EvidenceReport(
            topic=topic,
            answerable=answerable,
            evidence=chosen,
            summary=str(parsed.get("summary") or ""),
            missing=[str(m) for m in parsed.get("missing", [])],
            candidates_seen=len(candidates),
        )
        log.info(
            "evidence_collected",
            topic=topic,
            candidates=len(candidates),
            chosen=len(chosen),
            answerable=answerable,
        )
        return report
