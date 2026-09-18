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
      -- 아직 한 번도 분류에 들어가지 않은 질문. **이것만 '할 일' 이다.**
      --
      -- 전에는 '판단이 끝났는가' 로 셌다. 그러면 표본 부족(4건 미만)으로 대기하는
      -- 주제의 질문이 영원히 '미분석' 으로 떠 있는다. 분류를 아무리 눌러도 안 줄어든다.
      -- 그건 이미 분류했고 "더 쌓여야 한다" 는 판단까지 받은 질문이다.
      --
      -- 분석이 다시 볼 대상과는 기준이 다르다(runner._load_questions).
      -- 그쪽은 주제를 키우려고 계속 담아두고, 여기는 관리자가 누를 일이 있는지만 센다.
      (SELECT count(*) FROM question_log q
        JOIN kiosk k ON k.kiosk_id = q.kiosk_id
        JOIN site s ON s.site_id = k.site_id
        WHERE s.customer_id = :customer_id
          AND q.asked_at >= now() - interval '7 days'
          AND q.cluster_id IS NULL
          -- 규칙이 거른 것(욕설 · 자모만 입력)은 클러스터가 없지만 이미 판단받았다.
          AND q.verdict = 'pending')                                        AS unanalyzed,
      -- 마지막 분석에서 규칙이 걸러낸 주제. 지금은 '기존 메뉴가 이미 답함' 뿐이다.
      -- 질문 수로 미리 자르던 규칙은 폐기했다 — 무엇이 가치 있는지는 사람이 정한다.
      (SELECT count(*) FROM question_cluster c
        WHERE c.customer_id = :customer_id
          AND c.analysis_run_id = (
              SELECT max(analysis_run_id) FROM analysis_run
              WHERE customer_id = :customer_id AND status IN ('succeeded', 'awaiting_review'))
          AND c.review_status IS NULL
          AND c.covered_menu_id IS NULL)                                    AS growing_topics,
      -- 초안 품질 지표. 손보지 않고 그대로 쓴 비율이 높을수록 프롬프트가 잘 맞는 것이다.
      (SELECT count(*) FROM content_proposal
        WHERE customer_id = :customer_id AND status = 'approved')           AS approved_as_is,
      (SELECT count(*) FROM content_proposal
        WHERE customer_id = :customer_id AND status = 'edited')             AS approved_edited
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
