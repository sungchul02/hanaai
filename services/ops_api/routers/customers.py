"""고객사 조회.

키오스크 제조사라 플릿이 고객사 단위로 갈린다. 대시보드의 최상위 축이기도 하다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.ops_api.schemas import CustomerRow

router = APIRouter(prefix="/v1/customers", tags=["customers"])
DbSession = Annotated[Session, Depends(get_session)]

_SQL = text(
    """
    SELECT c.customer_id,
           c.code,
           c.name,
           c.status,
           count(DISTINCT s.site_id) AS sites,
           count(k.kiosk_id)         AS kiosks
    FROM customer c
    LEFT JOIN site  s ON s.customer_id = c.customer_id
    LEFT JOIN kiosk k ON k.site_id = s.site_id
    GROUP BY c.customer_id, c.code, c.name, c.status
    ORDER BY c.code
    """
)


@router.get("", response_model=list[CustomerRow])
def list_customers(session: DbSession) -> list[CustomerRow]:
    return [CustomerRow(**dict(r)) for r in session.execute(_SQL).mappings()]
