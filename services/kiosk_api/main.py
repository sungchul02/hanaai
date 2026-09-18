"""가상 키오스크 API (화면 A).

실제 키오스크 하드웨어 없이 웹 화면으로 질문을 받는다. 문서의 '화면 A' 다.
여기서 쌓인 question_log 가 분석의 입력이 된다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.common.health import build_health_router
from services.common.logging import configure_logging
from services.common.models import Kiosk
from services.kiosk_api import service
from services.kiosk_api.schemas import AskIn, AskOut, MenuBrief

STATIC_DIR = Path(__file__).resolve().parent / "static"

configure_logging()

app = FastAPI(title="HANAAI Kiosk API", version="1.0.0", description="가상 키오스크 질문 응답")
app.include_router(build_health_router())

DbSession = Annotated[Session, Depends(get_session)]


@app.get("/v1/kiosks", response_model=list[str])
def list_kiosks(session: DbSession) -> list[str]:
    """화면에서 '어느 키오스크인 척' 할지 고르게 한다."""
    return [k.serial_no for k in session.scalars(select(Kiosk).order_by(Kiosk.kiosk_id))]


@app.get("/v1/menus", response_model=list[MenuBrief])
def list_menus(kiosk_serial: str, session: DbSession) -> list[MenuBrief]:
    """지금 이 키오스크가 답할 수 있는 메뉴 목록. 화면 하단에 버튼으로 보여준다."""
    kiosk = _kiosk(session, kiosk_serial)
    menus = service.published_menus(session, service.customer_of(session, kiosk))
    return [MenuBrief.model_validate(m) for m in menus]


@app.post("/v1/ask", response_model=AskOut)
def ask(payload: AskIn, session: DbSession) -> AskOut:
    kiosk = _kiosk(session, payload.kiosk_serial)
    entry = service.ask(
        session,
        kiosk,
        payload.question,
        session_id=payload.session_id,
        input_mode=payload.input_mode,
    )
    title = None
    if entry.matched_menu_id is not None:
        menus = service.published_menus(session, service.customer_of(session, kiosk))
        title = next((m.title for m in menus if m.menu_id == entry.matched_menu_id), None)
    return AskOut(
        question_id=entry.question_id,
        answer=entry.answer_text or "",
        answer_source=entry.answer_source,
        matched_menu_title=title,
        response_ms=entry.response_ms or 0,
        asked_at=entry.asked_at,
    )


def _kiosk(session: Session, serial_no: str) -> Kiosk:
    kiosk = session.scalar(select(Kiosk).where(Kiosk.serial_no == serial_no))
    if kiosk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown kiosk: {serial_no}")
    return kiosk


app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")
