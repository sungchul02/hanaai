"""관리자가 지금 해야 할 일.

화면 맨 위에 놓인다. 숫자를 늘어놓는 대시보드와 다른 점은 **행동으로 이어지는 것만** 센다는 것이다.
'질문 297건' 은 관리자가 할 수 있는 일이 없다. '승인 대기 2건' 은 있다.

세 가지뿐이다. 늘리고 싶어지면, 그걸 보고 관리자가 무엇을 누를지 먼저 답해야 한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.common.db import get_session
from services.ops_api.schemas import TodoSummary

router = APIRouter(prefix="/v1", tags=["todo"])
DbSession = Annotated[Session, Depends(get_session)]

_TODO_SQL = text(
    """
    WITH last_run AS (
        SELECT analysis_run_id FROM analysis_run
        WHERE customer_id = :customer_id AND status = 'succeeded'
        ORDER BY analysis_run_id DESC LIMIT 1
    )
    SELECT
      (SELECT count(*) FROM content_proposal
        WHERE customer_id = :customer_id AND status = 'pending_review')     AS pending_review,
      -- 안내할 가치는 있는데 근거 문서가 없는 주제. 문서를 보강해야 답할 수 있다.
      (SELECT count(*) FROM question_cluster
        WHERE customer_id = :customer_id
          AND analysis_run_id = (SELECT analysis_run_id FROM last_run)
          AND evidence_found IS FALSE)                                      AS needs_document,
      -- 제목이 같은 메뉴가 둘 이상인 경우. 키오스크는 하나만 답하므로 나머지는 잡음이다.
      (SELECT coalesce(sum(cnt - 1), 0) FROM (
          SELECT count(*) AS cnt FROM cms_menu
          WHERE customer_id = :customer_id AND status = 'published'
          GROUP BY title HAVING count(*) > 1
      ) dup)                                                                AS duplicate_menus,
      -- 답한 적 없는 질문. 분석을 한 번도 안 돌렸으면 여기가 크게 잡힌다.
      (SELECT count(*) FROM question_log q
        JOIN kiosk k ON k.kiosk_id = q.kiosk_id
        JOIN site s ON s.site_id = k.site_id
        WHERE s.customer_id = :customer_id
          AND q.asked_at >= now() - interval '7 days'
          AND q.cluster_id IS NULL)                                         AS unanalyzed
    """
)


@router.get("/todo", response_model=TodoSummary)
def todo(session: DbSession, customer_id: int) -> TodoSummary:
    row = session.execute(_TODO_SQL, {"customer_id": customer_id}).mappings().one()
    return TodoSummary(**dict(row))


_DUPLICATES_SQL = text(
    """
    SELECT m.menu_id, m.title, m.body, m.keywords, m.origin, m.status,
           m.created_at, m.updated_at
    FROM cms_menu m
    JOIN (
        SELECT title FROM cms_menu
        WHERE customer_id = :customer_id AND status = 'published'
        GROUP BY title HAVING count(*) > 1
    ) d ON d.title = m.title
    WHERE m.customer_id = :customer_id AND m.status = 'published'
    ORDER BY m.title, m.menu_id
    """
)


@router.get("/menus/duplicates")
def duplicates(session: DbSession, customer_id: int) -> list[dict[str, object]]:
    """제목이 겹치는 메뉴들. 관리자가 어느 것을 남길지 고른다.

    자동으로 지우지 않는다. 어느 쪽 본문이 최신인지는 사람만 안다.
    """
    rows = session.execute(_DUPLICATES_SQL, {"customer_id": customer_id}).mappings()
    return [dict(row) for row in rows]
