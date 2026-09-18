"""관리자 CMS API + 대시보드 (화면 B)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from services.common.health import build_health_router
from services.common.logging import configure_logging
from services.ops_api.routers import analysis, cms, insights, knowledge, review, todo

STATIC_DIR = Path(__file__).resolve().parent / "static"

configure_logging()

app = FastAPI(
    title="HANAAI CMS API",
    version="1.0.0",
    description="질문 로그 분석과 추천 콘텐츠 검토",
)

app.include_router(build_health_router())
app.include_router(insights.router)
app.include_router(cms.router)
app.include_router(review.router)
app.include_router(analysis.router)
app.include_router(knowledge.router)
app.include_router(todo.router)

# 관리자 콘솔. 빌드 도구 없이 단일 HTML 이 위 JSON API 를 그대로 호출한다.
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
