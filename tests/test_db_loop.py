"""전체 루프 end-to-end 테스트 (PostgreSQL 필요).

문서가 말하는 7단계를 그대로 확인한다.
  질문 → 로그 저장 → 분석(분류·묶기·빈도) → 추천 생성 → 관리자 승인 → CMS 반영
  → **키오스크가 그 질문에 답하기 시작한다**

마지막 줄이 핵심이다. 승인 후 같은 질문을 다시 던졌을 때 답이 나가야 루프가 닫힌 것이다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agents.analyst.generator import PassthroughGenerator
from agents.analyst.runner import Window, run_analysis
from services.common.db import get_engine, get_sessionmaker
from services.common.models import CmsMenu, ContentProposal, Customer, Kiosk, QuestionLog, Site
from services.kiosk_api import service

pytestmark = pytest.mark.db

PARKING_QUESTIONS = [
    "주차장 어디예요?",
    "주차 가능한가요?",
    "주차비 얼마예요?",
    "주차 무료인가요?",
    "주차장 위치 알려주세요",
]


@pytest.fixture(scope="module")
def session() -> Iterator[Session]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL 없음: {exc}")
    with get_sessionmaker()() as s:
        yield s


@pytest.fixture(scope="module")
def fixture_set(session: Session) -> Iterator[tuple[int, Kiosk]]:
    """테스트 전용 고객사. 화장실 메뉴만 있고 주차 메뉴는 없다."""
    _purge(session)
    tag = uuid.uuid4().hex[:6]
    customer = Customer(code=f"LOOP-{tag}", name="루프 테스트")
    session.add(customer)
    session.flush()
    site = Site(customer_id=customer.customer_id, code=f"S-{tag}", name="본관")
    session.add(site)
    session.flush()
    kiosk = Kiosk(serial_no=f"LOOP-{tag}-K1", site_id=site.site_id, name="로비")
    session.add(kiosk)
    session.add(
        CmsMenu(
            customer_id=customer.customer_id,
            code="restroom",
            title="화장실 안내",
            body="화장실은 각 층 엘리베이터 옆에 있습니다.",
            keywords=["화장실"],
            status="published",
        )
    )
    session.commit()
    yield customer.customer_id, kiosk
    _purge(session)


def _purge(session: Session) -> None:
    session.execute(text("DELETE FROM question_cluster_member"))
    session.execute(text("UPDATE question_log SET cluster_id = NULL"))
    session.execute(text("DELETE FROM content_proposal"))
    session.execute(text("DELETE FROM question_cluster"))
    session.execute(text("DELETE FROM analysis_run"))
    session.execute(
        text("""
        DELETE FROM question_log WHERE kiosk_id IN (
            SELECT kiosk_id FROM kiosk WHERE serial_no LIKE 'LOOP-%'
        )""")
    )
    session.execute(text("DELETE FROM kiosk WHERE serial_no LIKE 'LOOP-%'"))
    session.execute(
        text("""
        DELETE FROM cms_menu WHERE customer_id IN (
            SELECT customer_id FROM customer WHERE code LIKE 'LOOP-%'
        )""")
    )
    session.execute(
        text("""
        DELETE FROM site WHERE customer_id IN (
            SELECT customer_id FROM customer WHERE code LIKE 'LOOP-%'
        )""")
    )
    session.execute(text("DELETE FROM customer WHERE code LIKE 'LOOP-%'"))
    session.commit()


# ------------------------------------------------------------------ 1~2 단계


def test_답하지_못한_질문도_기록된다(session: Session, fixture_set: tuple[int, Kiosk]) -> None:
    """답을 못 한 기록이 오히려 제일 값지다. 이게 없으면 시스템이 성립하지 않는다."""
    _, kiosk = fixture_set
    entry = service.ask(session, kiosk, "주차장 어디예요?")
    assert entry.answer_source == "fallback"
    assert entry.matched_menu_id is None
    assert entry.question_id is not None


def test_있는_메뉴는_답한다(session: Session, fixture_set: tuple[int, Kiosk]) -> None:
    _, kiosk = fixture_set
    entry = service.ask(session, kiosk, "화장실 어디예요?")
    assert entry.answer_source == "cms_menu"
    assert entry.answer_text is not None and "엘리베이터 옆" in entry.answer_text


# ------------------------------------------------------------------ 3~4 단계


@pytest.fixture(scope="module")
def analyzed(session: Session, fixture_set: tuple[int, Kiosk]) -> int:
    """주차 질문을 충분히 쌓고 분석을 돌린다. LLM 없이 규칙 기반으로 검증한다."""
    customer_id, kiosk = fixture_set
    for index in range(30):
        service.ask(session, kiosk, PARKING_QUESTIONS[index % len(PARKING_QUESTIONS)])
    for _ in range(5):
        service.ask(session, kiosk, "화장실 어디예요?")
    service.ask(session, kiosk, "바보야")  # 욕설은 걸러져야 한다
    return run_analysis(
        session,
        Window(
            start=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1),
            end=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1),
        ),
        customer_id=customer_id,
        generator=PassthroughGenerator(),
    )


def test_욕설은_걸러진다(session: Session, analyzed: int) -> None:
    row = session.scalar(select(QuestionLog).where(QuestionLog.question_text == "바보야"))
    assert row is not None and row.verdict == "abusive"


def test_답하지_못한_주제가_추천으로_올라온다(
    session: Session, analyzed: int, fixture_set: tuple[int, Kiosk]
) -> None:
    customer_id, _ = fixture_set
    proposals = list(
        session.scalars(select(ContentProposal).where(ContentProposal.customer_id == customer_id))
    )
    assert proposals, "주차 질문이 30건인데 추천이 없다"
    assert any("주차" in p.title or "주차" in " ".join(p.keywords) for p in proposals)


def test_이미_답하는_주제는_추천하지_않는다(
    session: Session, analyzed: int, fixture_set: tuple[int, Kiosk]
) -> None:
    """화장실은 메뉴가 있으니 새로 만들면 안 된다. 중복 메뉴는 관리자 신뢰를 깎는다."""
    customer_id, _ = fixture_set
    titles = [
        p.title
        for p in session.scalars(
            select(ContentProposal).where(ContentProposal.customer_id == customer_id)
        )
    ]
    assert not any("화장실" in title for title in titles)


# ------------------------------------------------------------------ 5~7 단계


def test_승인하면_키오스크가_답하기_시작한다(
    session: Session, analyzed: int, fixture_set: tuple[int, Kiosk]
) -> None:
    """루프가 닫히는 지점. 승인 전에는 못 답하고, 승인 후에는 답해야 한다."""
    customer_id, kiosk = fixture_set

    before = service.ask(session, kiosk, "주차장 어디예요?")
    assert before.answer_source == "fallback"

    proposal = session.scalars(
        select(ContentProposal)
        .where(ContentProposal.customer_id == customer_id)
        .order_by(ContentProposal.impact_score.desc())
    ).first()
    assert proposal is not None

    # 관리자가 [수정] 후 [추가하기] 를 누른 상황
    menu = CmsMenu(
        customer_id=customer_id,
        code=f"parking-{proposal.proposal_id}",
        title="주차 안내",
        body="주차장은 건물 지하 1층에 있습니다. 최초 30분 무료입니다.",
        keywords=["주차", "주차장", "주차비"],
        status="published",
        origin="ai_proposal",
    )
    session.add(menu)
    session.flush()
    proposal.status = "edited"
    proposal.applied_menu_id = menu.menu_id
    session.commit()

    after = service.ask(session, kiosk, "주차장 어디예요?")
    assert after.answer_source == "cms_menu"
    assert after.matched_menu_id == menu.menu_id
    assert after.answer_text is not None and "지하 1층" in after.answer_text
