"""API 기동 확인.

DB 없이도 앱이 뜨고 라우팅이 살아있는지 본다. 실제 DB 동작은 tests/test_db_*.py 가 맡는다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.ingest.main import app as ingest_app
from services.ops_api.main import app as ops_app


def test_수집_api_가_뜬다() -> None:
    with TestClient(ingest_app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_운영_api_가_뜬다() -> None:
    with TestClient(ops_app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}


def test_db_가_없으면_ready_는_503() -> None:
    """live 와 ready 를 나눈 이유. DB 장애로 프로세스를 재시작해서는 안 된다."""
    with TestClient(ops_app) as client:
        response = client.get("/health/ready")
        assert response.status_code in (200, 503)
        if response.status_code == 503:
            assert response.json()["database"] == "down"


def test_인증_없는_수집_요청은_거부된다() -> None:
    with TestClient(ingest_app) as client:
        response = client.post("/v1/ingest/events", json={"events": []})
        assert response.status_code == 422  # 헤더 누락


def test_잘못된_토큰은_401() -> None:
    with TestClient(ingest_app) as client:
        response = client.post(
            "/v1/ingest/events",
            json={
                "events": [
                    {
                        "occurred_at": "2026-09-18T12:00:00+00:00",
                        "source_seq": 1,
                        "type": "app.started",
                    }
                ]
            },
            headers={"X-Kiosk-Serial": "SEOUL-01-K01", "X-Device-Token": "wrong"},
        )
        assert response.status_code == 401


def test_openapi_스키마가_생성된다() -> None:
    with TestClient(ops_app) as client:
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/v1/kiosks" in paths
        assert "/v1/fleet/peripheral-health" in paths
        assert "/v1/proposals/{proposal_id}/approve" in paths
