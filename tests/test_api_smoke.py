"""두 앱이 뜨고 라우팅이 살아있는지 (DB 불필요)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.kiosk_api.main import app as kiosk_app
from services.ops_api.main import app as ops_app


def test_키오스크_앱이_뜬다() -> None:
    with TestClient(kiosk_app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_관리자_앱이_뜬다() -> None:
    with TestClient(ops_app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_화면_A_가_서빙된다() -> None:
    with TestClient(kiosk_app) as client:
        page = client.get("/ui/")
        assert page.status_code == 200
        assert "무엇을 도와드릴까요" in page.text


def test_화면_B_가_서빙된다() -> None:
    with TestClient(ops_app) as client:
        page = client.get("/ui/")
        assert page.status_code == 200
        assert "AI 추천 콘텐츠" in page.text


def test_루트는_화면으로_보낸다() -> None:
    for app in (kiosk_app, ops_app):
        with TestClient(app) as client:
            assert client.get("/", follow_redirects=False).headers["location"] == "/ui/"


def test_관리자_엔드포인트가_모두_등록되어_있다() -> None:
    with TestClient(ops_app) as client:
        paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/v1/dashboard/summary",
        "/v1/questions",
        "/v1/topics",
        "/v1/menus",
        "/v1/proposals",
        "/v1/proposals/{proposal_id}/approve",
        "/v1/proposals/{proposal_id}/ignore",
        "/v1/analysis/run",
    ):
        assert path in paths, path


def test_키오스크_엔드포인트가_모두_등록되어_있다() -> None:
    with TestClient(kiosk_app) as client:
        paths = client.get("/openapi.json").json()["paths"]
    for path in ("/v1/ask", "/v1/menus", "/v1/kiosks"):
        assert path in paths, path


def test_빈_질문은_거부된다() -> None:
    with TestClient(kiosk_app) as client:
        assert client.post("/v1/ask", json={"kiosk_serial": "X", "question": ""}).status_code == 422
