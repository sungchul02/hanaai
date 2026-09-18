"""콘텐츠 작성 Agent 테스트 (실제 LLM 호출 없음).

Agent 를 단일 호출과 가르는 건 '자기 초안을 검증하고 고치는 반복' 하나뿐이다.
그 루프가 실제로 도는지, 그리고 고치다 망치면 되돌리는지를 여기서 고정한다.

실운영에서는 초안이 처음부터 괜찮아서 수정이 안 일어나는 경우가 많다.
그래서 일부러 나쁜 초안을 주는 가짜 생성기로 루프를 돌려본다.
"""

from __future__ import annotations

import json
from typing import Any

from agents.analyst.content_agent import ContentAgent, resolve_clusters
from agents.analyst.verify import as_menu, verify_draft
from agents.contracts.proposal import ClusterEvidence, ContentProposal
from services.common.models import CmsMenu

WINDOW = "2026-09-11/2026-09-18"

PARKING_QUESTIONS = [
    "차 세울 데 있나요?",
    "차 어디에 대면 돼요?",
    "주차장 어디예요?",
    "주차비 얼마예요?",
]
RESTROOM = CmsMenu(
    menu_id=1,
    customer_id=1,
    code="restroom",
    title="화장실 안내",
    body="화장실은 각 층에 있습니다.",
    keywords=["화장실"],
    status="published",
)


def _cluster(label: str = "주차장 어디예요?", questions: list[str] | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "question_count": len(questions or PARKING_QUESTIONS),
        "unanswered_count": len(questions or PARKING_QUESTIONS),
        "sample_questions": (questions or PARKING_QUESTIONS)[:3],
        "keywords": ["주차"],
        "existing_menu": None,
        "dedupe_key": "주차",
        "_questions": questions or PARKING_QUESTIONS,
    }


def _proposal(title: str, body: str, keywords: list[str]) -> ContentProposal:
    return ContentProposal(
        title=title,
        body=body,
        keywords=keywords,
        source_labels=["주차장 어디예요?"],
        reason="주차 질문이 많은데 메뉴가 없다",
        evidence=ClusterEvidence(
            question_count=4,
            unanswered_count=4,
            window=WINDOW,
            sample_questions=PARKING_QUESTIONS[:2],
            existing_menu=None,
        ),
        impact_score=4.0,
        confidence=0.7,
    )


# 키워드가 부실해서 절반도 못 잡는 초안
WEAK = _proposal("주차 안내", "주차장은 지하 1층입니다.", ["주차장"])
# 못 잡던 표현까지 키워드에 넣은 초안
STRONG = _proposal(
    "주차 안내",
    "주차장은 지하 1층입니다. 차를 세우실 곳은 정문 오른쪽 진입로로 들어오시면 됩니다.",
    ["주차", "주차장", "주차비", "세울", "대면"],
)


class FakeBase:
    """첫 응답은 나쁜 초안, 수정 요청에는 좋은 초안을 돌려주는 가짜 LLM."""

    name = "fake"

    def __init__(self, revised: ContentProposal | None = STRONG) -> None:
        self.revised = revised
        self.complete_calls = 0
        self.last_usage: dict[str, Any] | None = {"cost_usd": 0.01}
        self.last_errors: list[str] = []

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]:
        return [WEAK.model_copy()]

    def complete(self, user_prompt: str) -> str:
        self.complete_calls += 1
        if self.revised is None:
            return "[]"
        dumped = self.revised.model_dump(mode="json")
        dumped.pop("contract_version", None)
        return json.dumps([dumped], ensure_ascii=False)


class NoCompleteBase(FakeBase):
    """수정 능력이 없는 생성기. passthrough 가 이 경우다."""

    name = "no-complete"
    complete = None  # type: ignore[assignment]


# ------------------------------------------------------------------ 검증 도구


def test_초안이_잡는_질문을_실제로_센다() -> None:
    draft = as_menu(STRONG.title, STRONG.body, STRONG.keywords)
    result = verify_draft(draft, PARKING_QUESTIONS, [RESTROOM])
    assert result.total == 4
    assert result.by_draft == 4
    assert result.ok


def test_부실한_초안은_놓친_질문을_알려준다() -> None:
    draft = as_menu(WEAK.title, WEAK.body, WEAK.keywords)
    result = verify_draft(draft, PARKING_QUESTIONS, [RESTROOM])
    assert not result.ok
    assert "차 세울 데 있나요?" in result.misses


def test_기존_메뉴가_이미_답하면_그것도_센다() -> None:
    """초안만 놓고 재면 실제 상황과 다르다. 다른 메뉴와 경쟁하며 돌아간다."""
    draft = as_menu("주차 안내", "지하 1층입니다.", ["주차"])
    result = verify_draft(draft, ["화장실 어디예요?", "주차 어디예요?"], [RESTROOM])
    assert result.by_existing == 1
    assert result.by_draft == 1


# ------------------------------------------------------------------ 주제 연결


def test_label_로_원본_주제를_찾는다() -> None:
    by_label = {"주차장 어디예요?": _cluster()}
    assert resolve_clusters(["주차장 어디예요?"], by_label) == [by_label["주차장 어디예요?"]]


def test_label_이_조금_달라도_찾는다() -> None:
    """모델이 글자를 정확히 옮기지 못하는 경우가 있다."""
    by_label = {"주차장 어디예요?": _cluster()}
    assert resolve_clusters(["주차장 어디에요"], by_label)


def test_전혀_다른_label_은_버린다() -> None:
    by_label = {"주차장 어디예요?": _cluster()}
    assert resolve_clusters(["화장실 어디예요?"], by_label) == []


# ------------------------------------------------------------------ Agent 루프


def test_부실한_초안을_스스로_고친다() -> None:
    """Agent 를 단일 호출과 가르는 지점. 검증 결과를 보고 다시 쓴다."""
    base = FakeBase()
    agent = ContentAgent(base, existing_menus=[RESTROOM])
    result = agent.generate([_cluster()], WINDOW)

    assert base.complete_calls >= 1, "수정 요청이 한 번도 안 갔다"
    trace = agent.traces[result[0].compute_dedupe_key()]
    assert trace.revisions >= 1
    assert trace.coverage == 1.0
    assert "세울" in result[0].keywords


def test_고치다_나빠지면_원안을_지킨다() -> None:
    """수정이 항상 개선은 아니다. 나빠졌으면 되돌려야 한다."""
    worse = _proposal("주차 안내", "주차 관련 안내입니다.", ["없는키워드"])
    agent = ContentAgent(FakeBase(revised=worse), existing_menus=[RESTROOM])
    result = agent.generate([_cluster()], WINDOW)

    assert result[0].keywords == WEAK.keywords, "나빠진 수정본이 채택됐다"


def test_수정_능력이_없으면_검증만_한다() -> None:
    """passthrough 처럼 LLM 이 없는 생성기도 그대로 동작해야 한다."""
    agent = ContentAgent(NoCompleteBase(), existing_menus=[RESTROOM])
    result = agent.generate([_cluster()], WINDOW)

    trace = agent.traces[result[0].compute_dedupe_key()]
    assert trace.revisions == 0
    assert trace.coverage < 1.0  # 부실한 초안 그대로
    assert trace.remaining_misses


def test_수정_횟수에_상한이_있다() -> None:
    """고쳐도 안 되면 무한히 부르지 않는다. 비용이 그대로 늘어난다."""
    never_good = _proposal("주차 안내", "주차 관련 안내입니다.", ["주차장"])
    base = FakeBase(revised=never_good)
    agent = ContentAgent(base, existing_menus=[RESTROOM], max_revisions=2)
    agent.generate([_cluster()], WINDOW)
    assert base.complete_calls <= 2


def test_검증_결과를_남긴다() -> None:
    """관리자가 '이 메뉴를 넣으면 몇 건이 답이 되는가' 를 보고 판단해야 한다."""
    agent = ContentAgent(FakeBase(), existing_menus=[RESTROOM])
    result = agent.generate([_cluster()], WINDOW)
    trace = agent.traces[result[0].compute_dedupe_key()]
    assert trace.total == 4
    assert trace.matched == 4
    assert agent.sources[result[0].compute_dedupe_key()] == ["주차장 어디예요?"]


def test_주제가_없으면_LLM_을_부르지_않는다() -> None:
    class Empty(FakeBase):
        def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]:
            return []

    base = Empty()
    assert ContentAgent(base).generate([], WINDOW) == []
    assert base.complete_calls == 0
