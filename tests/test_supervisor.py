"""상위 Agent 의 분류와 명령 위임.

가짜 LLM 을 쓴다. 진짜 모델을 부르면 느리고, 답이 매번 달라져서
"판단 결과를 어디에 기록하는가" 라는 이 코드의 책임을 검증할 수 없다.
"""

from __future__ import annotations

import json

import pytest

from agents.analyst.supervisor import SupervisorAgent, _parse_verdicts


class FakeCompleter:
    """label 에 '과태료' 가 들어가면 버리는 가짜 분류기."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, user_prompt: str) -> str:
        self.calls.append(user_prompt)
        if "근거 후보" in user_prompt:  # 하위 Agent 의 호출
            return json.dumps(
                {"answerable": True, "chunk_ids": [], "summary": "", "missing": []},
                ensure_ascii=False,
            )
        payload = user_prompt[user_prompt.rindex("주제 목록:") :]
        labels = [row["label"] for row in json.loads(payload[payload.index("[") :])]
        return json.dumps(
            [
                {
                    "label": label,
                    "keep": "과태료" not in label,
                    "reason": "개별 민원" if "과태료" in label else "반복 문의",
                }
                for label in labels
            ],
            ensure_ascii=False,
        )


CLUSTERS = [
    {"label": "주차장 어디예요?", "question_count": 20, "sample_questions": ["주차장 어디예요?"]},
    {"label": "과태료 조회해줘", "question_count": 9, "sample_questions": ["과태료 조회해줘"]},
]


def test_잡다한_주제는_버리고_이유를_남긴다(db_session) -> None:
    agent = SupervisorAgent(db_session, customer_id=1, completer=FakeCompleter())
    result = agent.run(CLUSTERS)

    dropped = [v.label for v in result.dropped]
    assert dropped == ["과태료 조회해줘"]
    assert result.dropped[0].reason, "왜 버렸는지가 없으면 관리자가 확인할 수 없다"


def test_분류에_실패하면_버리지_않는다(db_session) -> None:
    """조용히 사라지는 것이 잘못 남는 것보다 나쁘다."""

    class Broken:
        name = "broken"

        def complete(self, user_prompt: str) -> str:
            return "미안하지만 JSON 을 못 만들겠다"

    agent = SupervisorAgent(db_session, customer_id=1, completer=Broken())
    verdicts = agent.triage(CLUSTERS)
    assert len(verdicts) == 2
    assert all(v.keep for v in verdicts)


def test_LLM이_없으면_전부_통과시킨다(db_session) -> None:
    agent = SupervisorAgent(db_session, customer_id=1, completer=None)
    verdicts = agent.triage(CLUSTERS)
    assert [v.keep for v in verdicts] == [True, True]


@pytest.mark.parametrize(
    "text",
    [
        '앞말 [{"label": "a", "keep": true, "reason": "r"}] 뒷말',
        '```json\n[{"label": "a", "keep": true, "reason": "r"}]\n```',
    ],
)
def test_모델이_군더더기를_붙여도_읽어낸다(text: str) -> None:
    verdicts = _parse_verdicts(text)
    assert len(verdicts) == 1 and verdicts[0].label == "a"
