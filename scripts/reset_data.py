"""운영 데이터를 전부 비운다. 스키마는 건드리지 않는다.

  python scripts/reset_data.py --yes

지우는 것: 질문 로그 · 분석 결과 · 추천 콘텐츠 · CMS 메뉴 · 근거 문서 · 고객사/지점/키오스크

시연 도중 "지금 화면에 보이는 게 언제 들어온 데이터인지" 를 확신할 수 없으면
결과를 믿을 수 없다. 처음부터 다시 시작하기 위한 도구다. 되돌릴 수 없다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from services.common.db import get_sessionmaker

# 외래키 때문에 순서가 중요하다. 참조하는 쪽부터 지운다.
TABLES = [
    "proposal_evidence",
    "content_proposal",
    "question_cluster_member",
    "question_log",
    "question_cluster",
    "analysis_run",
    "document_chunk",
    "source_document",
    "cms_menu",
    "kiosk",
    "site",
    "customer",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="확인 없이 지운다")
    args = parser.parse_args()

    if not args.yes:
        print("되돌릴 수 없다. 정말 지우려면 --yes 를 붙여라.")
        raise SystemExit(1)

    with get_sessionmaker()() as session:
        for table in TABLES:
            result = session.execute(text(f"DELETE FROM {table}"))
            print(f"  {table}: {result.rowcount}행 삭제")  # type: ignore[attr-defined]
        session.commit()
    print("비웠다. 다음: python scripts/seed.py")


if __name__ == "__main__":
    main()
