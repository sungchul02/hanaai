"""근거 검색과 하위 Agent.

이 계층에서 가장 중요한 성질은 '찾은 것' 이 아니라 '없으면 없다고 하는 것' 이다.
근거 없이 답을 지어내지 않기 위해 문서를 둔 것이므로, 그 성질이 깨지면 전부 무의미하다.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from agents.knowledge.evidence_agent import EvidenceAgent
from agents.knowledge.retriever import has_documents, index_keywords, search, search_many
from services.common.models import DocumentChunk, SourceDocument

CHUNKS = [
    ("주차 안내", "청사 부설 주차장은 총 344면입니다. 최초 2시간 무료, 이후 30분당 500원입니다."),
    ("여권 야간 운영", "여권 야간 운영은 매주 월요일 18:00부터 19:30까지입니다."),
    ("6층", "6층에는 행정지원과, 세정과, 회계과가 있습니다."),
]


@pytest.fixture(scope="module")
def knowledge(db_session: Session, temp_customer: int) -> Iterator[int]:
    customer_id = temp_customer
    doc = SourceDocument(
        customer_id=customer_id,
        title="테스트 청사 안내",
        url="https://example.invalid/guide",
        kind="web",
        fetched_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(doc)
    db_session.flush()
    for ordinal, (heading, body) in enumerate(CHUNKS):
        db_session.add(
            DocumentChunk(
                document_id=doc.document_id,
                ordinal=ordinal,
                heading=heading,
                text_body=body,
                keywords=index_keywords(heading, body),
            )
        )
    db_session.commit()
    yield customer_id
    # 문서를 남겨두면 고객사를 지울 수 없다(외래키). 만든 쪽이 치운다.
    db_session.delete(doc)
    db_session.commit()


def test_관련_없는_질문에는_아무것도_주지_않는다(db_session: Session, knowledge: int) -> None:
    """억지로 붙이면 엉뚱한 안내가 나간다. 빈 손으로 돌아오는 것이 정답이다."""
    assert search(db_session, knowledge, "수유실 어디 있어요?") == []


def test_주제에_맞는_조각을_찾는다(db_session: Session, knowledge: int) -> None:
    hits = search(db_session, knowledge, "주차 요금 얼마예요?")
    assert hits and hits[0].heading == "주차 안내"
    assert "344면" in hits[0].text


def test_여러_표현으로_찾으면_더_모인다(db_session: Session, knowledge: int) -> None:
    one = search(db_session, knowledge, "주차 요금 얼마예요?")
    many = search_many(db_session, knowledge, ["주차 요금 얼마예요?", "여권 야간 되나요"])
    assert len(many) > len(one)


def test_문서가_있는지_구분한다(db_session: Session, knowledge: int) -> None:
    assert has_documents(db_session, knowledge) is True
    assert has_documents(db_session, customer_id=-1) is False


def test_근거가_없으면_답할_수_없다고_보고한다(db_session: Session, knowledge: int) -> None:
    class NeverCalled:
        name = "never"

        def complete(self, user_prompt: str) -> str:  # pragma: no cover - 불려선 안 된다
            raise AssertionError("근거 후보가 없는데 LLM 을 불렀다")

    report = EvidenceAgent(db_session, knowledge, NeverCalled()).collect(
        "수유실", ["수유실 어디예요"]
    )
    assert report.answerable is False
    assert report.missing, "무엇이 없는지 남겨야 관리자가 문서를 보강할 수 있다"


def test_LLM이_고른_조각만_근거가_된다(db_session: Session, knowledge: int) -> None:
    """검색은 낱말이 겹친다는 이유로 엉뚱한 것도 물어온다. 고르는 판단이 한 단계 필요하다."""

    class PicksNothing:
        name = "picky"

        def complete(self, user_prompt: str) -> str:
            return json.dumps(
                {"answerable": False, "chunk_ids": [], "summary": "", "missing": ["층수 없음"]},
                ensure_ascii=False,
            )

    report = EvidenceAgent(db_session, knowledge, PicksNothing()).collect("주차", ["주차 요금"])
    assert report.candidates_seen > 0, "검색은 후보를 찾았어야 한다"
    assert report.answerable is False
    assert report.evidence == []


def test_걸린_문서의_나머지_조각도_함께_준다(db_session: Session, knowledge: int) -> None:
    """실제로 당한 것. 층별 안내 문서에 1~7층이 다 있는데 "부서 층 안내" 로는
    1층 하나만 걸렸다. 질문의 낱말('부서')과 조각의 낱말('세정과')이 안 겹쳐서다.
    그 결과 안내문이 "나머지 층은 확인 후 입력 필요" 로 나갔다.
    DB 에 답이 있는데 관리자에게 채워 넣으라고 한 셈이다."""
    hits = search_many(db_session, knowledge, ["주차 요금 얼마예요?"])
    direct = [e for e in hits if not e.sibling]
    siblings = [e for e in hits if e.sibling]
    assert direct, "낱말이 걸린 조각이 있어야 한다"
    assert siblings, "같은 문서의 나머지 조각도 따라와야 한다"
    assert len(hits) == len(CHUNKS), "문서 전체가 보여야 한다"


def test_아무것도_안_걸리면_확장도_없다(db_session: Session, knowledge: int) -> None:
    """근거가 없을 때 문서를 통째로 들이밀면 관련 없는 내용으로 안내문을 쓰게 된다."""
    assert search_many(db_session, knowledge, ["수유실 어디 있어요?"]) == []
