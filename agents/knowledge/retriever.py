"""근거 검색.

임베딩을 쓰지 않는다. 문서가 수십 개 규모라 낱말 기반으로 충분하고, 설치와 모델 파일이
학생 프로젝트 환경에 무겁다. 정확도가 필요해지면 document_chunk 에 vector 컬럼을 더하고
search() 안쪽만 바꾸면 된다. 부르는 쪽은 이 함수 하나만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from agents.analyst import textutil
from agents.analyst.tuning import DEFAULT_TUNING
from services.common.models import DocumentChunk, SourceDocument

# 이보다 관련이 낮으면 근거로 쓰지 않는다. 억지로 붙이면 엉뚱한 안내가 나간다.
MIN_SCORE = DEFAULT_TUNING.evidence_min_score


@dataclass
class Evidence:
    """근거 한 조각. 출처를 함께 들고 다닌다."""

    chunk_id: int
    document_title: str
    document_url: str | None
    heading: str | None
    text: str
    score: float
    # 낱말이 직접 걸린 것이 아니라, 같은 문서라서 함께 딸려온 조각인가.
    sibling: bool = False

    def cite(self) -> str:
        where = f"{self.document_title}"
        if self.heading:
            where += f" / {self.heading}"
        return f"[{where}] {self.text}"


def index_keywords(heading: str | None, text: str) -> list[str]:
    """저장할 때 뽑아두는 검색용 낱말. 조회할 때마다 다시 계산하지 않는다."""
    tokens = textutil.content_tokens(f"{heading or ''} {text}")
    return sorted(tokens)


def _score(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    """질문의 낱말이 이 조각에 얼마나 담겼는가.

    조각 쪽 길이로 나누지 않는다. 긴 문단이라고 불리해질 이유가 없고,
    우리가 묻는 것은 '질문을 이 조각이 설명하는가' 이기 때문이다.
    """
    if not query_tokens or not chunk_tokens:
        return 0.0
    covered = sum(max(textutil.token_score(q, c) for c in chunk_tokens) for q in query_tokens)
    return covered / len(query_tokens)


def search(
    session: Session,
    customer_id: int,
    query: str,
    limit: int = 4,
    min_score: float = MIN_SCORE,
) -> list[Evidence]:
    """질문이나 주제로 근거 조각을 찾는다. 점수 높은 순."""
    query_tokens = textutil.content_tokens(query)
    if not query_tokens:
        return []

    rows = session.scalars(
        select(DocumentChunk)
        .join(SourceDocument, SourceDocument.document_id == DocumentChunk.document_id)
        .where(SourceDocument.customer_id == customer_id)
        .options(joinedload(DocumentChunk.document))
    )

    found: list[Evidence] = []
    for chunk in rows:
        score = _score(query_tokens, set(chunk.keywords))
        if score < min_score:
            continue
        found.append(
            Evidence(
                chunk_id=chunk.chunk_id,
                document_title=chunk.document.title,
                document_url=chunk.document.url,
                heading=chunk.heading,
                text=chunk.text_body,
                score=round(score, 4),
            )
        )

    found.sort(key=lambda e: e.score, reverse=True)
    return found[:limit]


# 한 문서에서 이만큼까지는 같이 딸려온다. 문서 하나가 후보를 다 잡아먹지 않게 막는다.
SIBLING_LIMIT = 10


def _siblings(session: Session, document_ids: set[int], seen: set[int]) -> list[Evidence]:
    """이미 걸린 문서의 나머지 조각. 낱말은 안 걸렸지만 같은 이야기의 일부다."""
    if not document_ids:
        return []
    rows = session.scalars(
        select(DocumentChunk)
        .join(SourceDocument, SourceDocument.document_id == DocumentChunk.document_id)
        .where(DocumentChunk.document_id.in_(document_ids))
        .options(joinedload(DocumentChunk.document))
        .order_by(DocumentChunk.document_id, DocumentChunk.ordinal)
    )
    found: list[Evidence] = []
    per_document: dict[int, int] = {}
    for chunk in rows:
        if chunk.chunk_id in seen:
            continue
        count = per_document.get(chunk.document_id, 0)
        if count >= SIBLING_LIMIT:
            continue
        per_document[chunk.document_id] = count + 1
        found.append(
            Evidence(
                chunk_id=chunk.chunk_id,
                document_title=chunk.document.title,
                document_url=chunk.document.url,
                heading=chunk.heading,
                text=chunk.text_body,
                score=0.0,
                sibling=True,
            )
        )
    return found


def search_many(
    session: Session,
    customer_id: int,
    queries: list[str],
    limit: int = 5,
    expand_documents: bool = True,
    min_score: float = MIN_SCORE,
) -> list[Evidence]:
    """여러 표현으로 찾아서 합친다.

    한 주제 안에서도 "주차장 어디예요" 와 "주차비 얼마예요" 는 서로 다른 조각을 가리킨다.
    대표 질문 하나만으로 찾으면 절반을 놓친다.

    expand_documents: 걸린 문서의 나머지 조각도 함께 준다.

      이게 없어서 실제로 이런 일이 있었다. 청사 층별 안내 문서에 1층부터 7층까지
      전부 들어 있는데, "부서별 층 안내 있어요?" 로는 1층 조각 하나만 걸렸다.
      질문의 낱말은 '부서' 인데 층 조각의 낱말은 '세정과' '행정지원과' 라 안 겹친 것이다.
      그 결과 안내문이 "그 밖의 층별 배치는 (확인 후 입력 필요)" 로 나갔다.
      **DB 에 답이 있는데 관리자에게 채워 넣으라고 한 셈이다.**

      문서는 하나의 이야기다. 일부가 걸렸으면 나머지도 보여주고, 무엇을 쓸지는
      하위 Agent 가 고르게 한다. 고르는 판단은 이미 그쪽에 있다.
    """
    best: dict[int, Evidence] = {}
    for query in queries:
        for evidence in search(session, customer_id, query, limit=limit, min_score=min_score):
            kept = best.get(evidence.chunk_id)
            if kept is None or evidence.score > kept.score:
                best[evidence.chunk_id] = evidence

    hits = sorted(best.values(), key=lambda e: e.score, reverse=True)[:limit]
    if not expand_documents or not hits:
        return hits

    document_ids = set(
        session.scalars(
            select(DocumentChunk.document_id).where(
                DocumentChunk.chunk_id.in_([e.chunk_id for e in hits])
            )
        )
    )
    return hits + _siblings(session, document_ids, {e.chunk_id for e in hits})


def has_documents(session: Session, customer_id: int) -> bool:
    """이 고객사에 근거 문서가 하나라도 있는가.

    "문서가 아예 없다" 와 "문서는 있는데 이 주제를 다루지 않는다" 는 전혀 다른 상황이다.
    앞은 아직 지식 저장소를 안 채운 것이고, 뒤는 채워야 할 구멍을 찾아낸 것이다.
    이걸 구분하지 못하면 문서를 넣기 전에는 제안이 하나도 안 나온다.
    """
    return (
        session.scalar(
            select(DocumentChunk.chunk_id)
            .join(SourceDocument, SourceDocument.document_id == DocumentChunk.document_id)
            .where(SourceDocument.customer_id == customer_id)
            .limit(1)
        )
        is not None
    )
