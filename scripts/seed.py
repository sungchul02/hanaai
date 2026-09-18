"""초기 데이터 적재.

  python scripts/seed.py

만드는 것: 고객사(천안시청) · 지점 · 키오스크 1대 · 근거 문서.

**CMS 메뉴는 하나도 만들지 않는다.** 이게 이 프로젝트의 출발점이다.
콘텐츠가 비어 있는 채로 질문을 받고, 무엇이 필요한지를 질문이 알려주게 한다.
미리 메뉴를 넣어두면 '무엇이 없는지' 라는 신호가 사라진다.

질문도 넣지 않는다. 예상 질문 목록은 db/seeds/expected_questions.py 에 있고,
넣는 것은 scripts/ask.py 가 따로 한다. 데이터가 언제 어떻게 들어왔는지
구분되지 않으면 결과를 믿을 수 없다.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agents.knowledge.retriever import index_keywords
from db.seeds.cheonan_documents import DOCUMENTS
from services.common.db import get_sessionmaker
from services.common.models import (
    CmsMenu,
    Customer,
    DocumentChunk,
    Kiosk,
    Site,
    SourceDocument,
)

CUSTOMER = ("CHEONAN-CITY", "천안시청")
SITE = ("MAIN", "천안시청 본관")
KIOSKS = [
    ("KIOSK-CHEONAN-1F-01", "1층 종합민원실 입구"),
]


def main() -> None:
    with get_sessionmaker()() as session:
        session.execute(
            pg_insert(Customer)
            .values(code=CUSTOMER[0], name=CUSTOMER[1])
            .on_conflict_do_nothing(index_elements=["code"])
        )
        session.commit()
        customer_id = session.execute(
            select(Customer.customer_id).where(Customer.code == CUSTOMER[0])
        ).scalar_one()

        session.execute(
            pg_insert(Site)
            .values(customer_id=customer_id, code=SITE[0], name=SITE[1])
            .on_conflict_do_nothing(index_elements=["customer_id", "code"])
        )
        session.commit()
        site_id = session.execute(
            select(Site.site_id).where(Site.customer_id == customer_id, Site.code == SITE[0])
        ).scalar_one()

        for serial, name in KIOSKS:
            session.execute(
                pg_insert(Kiosk)
                .values(serial_no=serial, site_id=site_id, name=name)
                .on_conflict_do_nothing(index_elements=["serial_no"])
            )
        session.commit()

        chunks_total = load_documents(session, customer_id)

        menus = session.scalar(
            select(func.count()).select_from(CmsMenu).where(CmsMenu.customer_id == customer_id)
        )
        print(f"고객사 {CUSTOMER[1]} · 지점 1 · 키오스크 {len(KIOSKS)}")
        print(f"근거 문서 {len(DOCUMENTS)}건 · 조각 {chunks_total}개")
        print(f"CMS 메뉴 {menus}개 — 비어 있는 것이 정상이다. 질문이 쌓여야 채워진다.")


def load_documents(session: Session, customer_id: int) -> int:
    """근거 문서를 넣는다. 다시 돌려도 안전하도록 문서 단위로 조각을 갈아끼운다.

    출처가 바뀌면 문서를 다시 가져와야 하므로, 덮어쓰기가 되어야 한다.
    """
    now = dt.datetime.now(dt.UTC)
    total = 0
    for doc in DOCUMENTS:
        session.execute(
            pg_insert(SourceDocument)
            .values(
                customer_id=customer_id,
                title=doc["title"],
                url=doc["url"],
                kind="web",
                note=doc["note"],
                fetched_at=now,
            )
            .on_conflict_do_update(
                index_elements=["customer_id", "title"],
                set_={"url": doc["url"], "note": doc["note"], "fetched_at": now},
            )
        )
        session.commit()
        document_id = session.execute(
            select(SourceDocument.document_id).where(
                SourceDocument.customer_id == customer_id,
                SourceDocument.title == doc["title"],
            )
        ).scalar_one()

        session.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
        for ordinal, chunk in enumerate(doc["chunks"]):
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    ordinal=ordinal,
                    heading=chunk["heading"],
                    text_body=chunk["text"],
                    keywords=index_keywords(chunk["heading"], chunk["text"]),
                )
            )
            total += 1
        session.commit()
        print(f"  문서: {doc['title']} ({len(doc['chunks'])} 조각)")
    return total


if __name__ == "__main__":
    main()
