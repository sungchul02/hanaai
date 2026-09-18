"""운영 API.

조회·관리와 제안 검토를 담당한다. 수집(services/ingest)과 분리되어 있으며,
인증 주체도 다르다. 여기는 사람(운영자), 저기는 기계(키오스크)다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from services.common.health import build_health_router
from services.common.logging import configure_logging
from services.ops_api.routers import customers, fleet, kiosks, proposals

STATIC_DIR = Path(__file__).resolve().parent / "static"

configure_logging()

app = FastAPI(
    title="HANAAI Ops API",
    version="0.1.0",
    description="키오스크 플릿 운영 및 제안 검토",
)

app.include_router(build_health_router())
app.include_router(kiosks.router)
app.include_router(fleet.router)
app.include_router(customers.router)
app.include_router(proposals.router)

# 운영 콘솔. 빌드 도구 없이 단일 HTML 이 위 JSON API 를 그대로 호출한다.
# 프론트 스택을 들이지 않은 이유: 지금 필요한 것은 '보이는 것' 이지 SPA 가 아니다.
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
