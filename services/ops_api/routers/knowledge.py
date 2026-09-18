"""근거 문서 조회.

관리자가 두 가지를 확인할 수 있어야 한다.

  1. AI 가 무엇을 근거로 안내문을 썼는가 — 출처를 보지 않고는 승인할 수 없다.
  2. 어떤 주제에 문서가 없는가 — 그게 곧 채워야 할 구멍이다.

읽기 전용이다. 문서를 넣는 것은 scripts/seed.py 가 한다. 웹에서 아무나
지식 저장소를 바꿀 수 있으면, 키오스크가 뭘 근거로 답했는지 추적할 수 없게 된다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.ops_api.schemas import DocumentRow, EvidenceRow

router = APIRouter(prefix="/v1", tags=["knowledge"])
DbSession = Annotated[Session, Depends(get_session)]

_DOCUMENTS_SQL = text(
    """
    SELECT d.document_id, d.title, d.url, d.note, d.fetched_at,
           count(c.chunk_id) AS chunk_count
    FROM source_document d
    LEFT JOIN document_chunk c ON c.document_id = d.document_id
    WHERE d.customer_id = :customer_id
    GROUP BY d.document_id
    ORDER BY d.title
    """
)

_EVIDENCE_SQL = text(
    """
    SELECT e.chunk_id, e.score, e.quote,
           c.heading, d.title AS document_title, d.url AS document_url
    FROM proposal_evidence e
    JOIN document_chunk c ON c.chunk_id = e.chunk_id
    JOIN source_document d ON d.document_id = c.document_id
    WHERE e.proposal_id = :proposal_id
    ORDER BY e.score DESC
    """
)


@router.get("/documents", response_model=list[DocumentRow])
def list_documents(
    session: DbSession, customer_id: int, limit: int = Query(default=50, le=200)
) -> list[DocumentRow]:
    rows = session.execute(_DOCUMENTS_SQL, {"customer_id": customer_id}).mappings()
    return [DocumentRow(**dict(row)) for row in list(rows)[:limit]]


@router.get("/proposals/{proposal_id}/evidence", response_model=list[EvidenceRow])
def list_evidence(proposal_id: int, session: DbSession) -> list[EvidenceRow]:
    """이 제안이 어느 문서 조각에서 나왔는지. 승인 전에 눌러서 확인한다."""
    rows = session.execute(_EVIDENCE_SQL, {"proposal_id": proposal_id}).mappings()
    return [EvidenceRow(**dict(row)) for row in rows]
