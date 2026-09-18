"""관리자가 직접 메뉴를 만들고 고치는 경로 (PostgreSQL 필요).

AI 추천만 있으면 CMS 가 아니다. 이미 아는 안내는 그냥 적으면 되고,
"차 세울 데 있나요?" 처럼 콘텐츠는 있는데 말이 안 겹치는 경우는
새 메뉴가 아니라 키워드 한 줄로 해결된다. 그 경로가 실제로 도는지 본다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.common.db import get_engine, get_sessionmaker
from services.common.models import CmsMenu, Customer, Kiosk, Site
from services.kiosk_api import service
from services.ops_api.main import app

pytestmark = pytest.mark.db


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
    _purge(session)
    tag = uuid.uuid4().hex[:6]
    customer = Customer(code=f"CMS-{tag}", name="CMS 테스트")
    session.add(customer)
    session.flush()
    site = Site(customer_id=customer.customer_id, code=f"S-{tag}", name="본관")
    session.add(site)
    session.flush()
    kiosk = Kiosk(serial_no=f"CMS-{tag}-K1", site_id=site.site_id, name="로비")
    session.add(kiosk)
    session.commit()
    yield customer.customer_id, kiosk
    _purge(session)


def _purge(session: Session) -> None:
    session.execute(
        text("""
        DELETE FROM question_log WHERE kiosk_id IN (
            SELECT kiosk_id FROM kiosk WHERE serial_no LIKE 'CMS-%')""")
    )
    session.execute(text("DELETE FROM kiosk WHERE serial_no LIKE 'CMS-%'"))
    session.execute(
        text("""
        DELETE FROM cms_menu WHERE customer_id IN (
            SELECT customer_id FROM customer WHERE code LIKE 'CMS-%')""")
    )
    session.execute(
        text("""
        DELETE FROM site WHERE customer_id IN (
            SELECT customer_id FROM customer WHERE code LIKE 'CMS-%')""")
    )
    session.execute(text("DELETE FROM customer WHERE code LIKE 'CMS-%'"))
    session.commit()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def test_직접_만든_메뉴가_바로_답한다(
    client: TestClient, session: Session, fixture_set: tuple[int, Kiosk]
) -> None:
    """AI 를 기다리지 않고 관리자가 바로 쓸 수 있어야 한다."""
    customer_id, kiosk = fixture_set
    before = service.ask(session, kiosk, "옥상정원 개방하나요?")
    assert before.answer_source != "cms_menu"

    response = client.post(
        "/v1/menus",
        json={
            "customer_id": customer_id,
            "title": "옥상정원 안내",
            "body": "옥상정원은 평일 10시부터 17시까지 개방합니다.",
            "keywords": ["옥상정원", "옥상", "정원"],
        },
    )
    assert response.status_code == 201
    assert response.json()["origin"] == "manual"

    after = service.ask(session, kiosk, "옥상정원 개방하나요?")
    assert after.answer_source == "cms_menu"
    assert after.answer_text is not None and "10시부터" in after.answer_text


def test_키워드만_추가해도_못_잡던_질문이_잡힌다(
    client: TestClient, session: Session, fixture_set: tuple[int, Kiosk]
) -> None:
    """새 메뉴를 만드는 게 아니라 기존 메뉴를 고치는 것이 맞는 경우가 있다."""
    customer_id, kiosk = fixture_set
    created = client.post(
        "/v1/menus",
        json={
            "customer_id": customer_id,
            "title": "자전거 보관소",
            "body": "자전거 보관소는 건물 뒤편에 있습니다.",
            "keywords": ["자전거"],
        },
    ).json()

    missed = service.ask(session, kiosk, "따릉이 세울 데 있어요?")
    assert missed.answer_source != "cms_menu"

    patched = client.patch(
        f"/v1/menus/{created['menu_id']}",
        # 미리보기에서 "따릉이만 넣으면 아직 못 잡는다" 를 보고 '세울' 까지 더한 상황.
        # 질문의 낱말을 메뉴가 충분히 덮어야 응답으로 인정된다.
        json={"keywords": ["자전거", "따릉이", "보관소", "세울"]},
    )
    assert patched.status_code == 200

    now = service.ask(session, kiosk, "따릉이 세울 데 있어요?")
    assert now.answer_source == "cms_menu"


def test_미리보기는_저장하지_않는다(
    client: TestClient, session: Session, fixture_set: tuple[int, Kiosk]
) -> None:
    """저장 전에 효과를 보는 것이 목적이다. 눌렀다고 메뉴가 생기면 안 된다."""
    customer_id, kiosk = fixture_set
    service.ask(session, kiosk, "흡연구역 어디예요?")
    before = session.scalar(
        select(CmsMenu).where(CmsMenu.customer_id == customer_id, CmsMenu.title == "흡연구역")
    )

    response = client.post(
        "/v1/menus/preview",
        json={
            "customer_id": customer_id,
            "title": "흡연구역",
            "body": "흡연구역은 건물 외부에 있습니다.",
            "keywords": ["흡연구역", "흡연"],
        },
    )
    assert response.status_code == 200
    assert response.json()["by_draft"] >= 1

    after = session.scalar(
        select(CmsMenu).where(CmsMenu.customer_id == customer_id, CmsMenu.title == "흡연구역")
    )
    assert before is None and after is None, "미리보기가 메뉴를 만들었다"


def test_같은_제목이어도_코드가_겹치지_않는다(
    client: TestClient, fixture_set: tuple[int, Kiosk]
) -> None:
    customer_id, _ = fixture_set
    payload = {
        "customer_id": customer_id,
        "title": "중복 제목",
        "body": "같은 제목으로 두 번 만들어도 저장되어야 한다.",
        "keywords": ["중복"],
    }
    first = client.post("/v1/menus", json=payload).json()
    second = client.post("/v1/menus", json=payload).json()
    assert first["code"] != second["code"]


def test_너무_짧은_안내_문구는_거부된다(client: TestClient, fixture_set: tuple[int, Kiosk]) -> None:
    """승인 즉시 고객에게 보이는 문장이라 최소 길이를 지킨다."""
    customer_id, _ = fixture_set
    response = client.post(
        "/v1/menus",
        json={"customer_id": customer_id, "title": "짧음", "body": "짧다", "keywords": ["짧음"]},
    )
    assert response.status_code == 422
