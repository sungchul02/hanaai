from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from services.common.config import get_settings
from services.common.db import get_engine


def build_health_router() -> APIRouter:
    """live / ready 를 분리한다.

    live 는 프로세스가 살아있는지만 본다 (DB 가 죽어도 재시작하면 안 되므로 200).
    ready 는 트래픽을 받을 수 있는지를 본다 (DB 없으면 503).
    """
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready")
    def ready(response: Response) -> dict[str, str]:
        settings = get_settings()
        database = "up"
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            database = "down"
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if database == "up" else "degraded",
            "env": settings.env,
            "database": database,
            "checked_at": dt.datetime.now(dt.UTC).isoformat(),
        }

    return router
