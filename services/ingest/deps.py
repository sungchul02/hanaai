from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.common.config import get_settings
from services.common.db import get_session
from services.common.models import Kiosk


def get_current_kiosk(
    x_kiosk_serial: Annotated[str, Header(description="키오스크 시리얼")],
    x_device_token: Annotated[str, Header(description="디바이스 토큰")],
    session: Annotated[Session, Depends(get_session)],
) -> Kiosk:
    """스켈레톤 단계의 디바이스 인증.

    운영에서는 mTLS 클라이언트 인증서로 교체한다. 공유 토큰은 한 대가 털리면 전부 털린다.
    교체 지점을 한 곳에 모아두기 위해 의존성으로 분리해 둔 것이다.
    """
    settings = get_settings()
    if not secrets.compare_digest(x_device_token, settings.device_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid device token")

    kiosk = session.scalar(select(Kiosk).where(Kiosk.serial_no == x_kiosk_serial))
    if kiosk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown kiosk: {x_kiosk_serial}")
    if kiosk.status == "retired":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "kiosk is retired")
    return kiosk


CurrentKiosk = Annotated[Kiosk, Depends(get_current_kiosk)]
DbSession = Annotated[Session, Depends(get_session)]
