"""분석 1회 실행.

  python scripts/run_analysis.py                        # 최근 7일, 설정된 백엔드
  python scripts/run_analysis.py --dry-run              # 주제만 묶어서 출력 (LLM 호출 없음)
  python scripts/run_analysis.py --backend passthrough  # LLM 없이

--dry-run 이 중요하다. 묶기 임계값을 손볼 때는 DB 에 제안을 쌓지 않고 주제만 보면서 조정한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from agents.analyst.cluster import QuestionItem, build_clusters, find_covering_menu
from agents.analyst.generator import get_generator
from agents.analyst.runner import (
    COVERAGE_THRESHOLD,
    MIN_CLUSTER_SIZE,
    Window,
    _load_menus,
    _load_questions,
    classify,
    run_analysis,
)
from services.common.db import get_sessionmaker
from services.common.logging import configure_logging
from services.common.models import Customer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--customer", default="BLDG-A", help="고객사 코드")
    parser.add_argument("--dry-run", action="store_true", help="주제만 출력")
    parser.add_argument(
        "--backend",
        choices=["claude-cli", "claude-api", "passthrough"],
        help="기본은 HANAAI_ANALYST_BACKEND 설정값",
    )
    parser.add_argument("--model")
    args = parser.parse_args()

    configure_logging()
    window = Window.last_days(args.days)

    with get_sessionmaker()() as session:
        customer_id = session.execute(
            select(Customer.customer_id).where(Customer.code == args.customer)
        ).scalar_one_or_none()
        if customer_id is None:
            print(f"고객사 {args.customer} 가 없다. python scripts/seed.py 를 먼저 돌려라.")
            return

        if args.dry_run:
            questions = _load_questions(session, window, customer_id)
            usable = [
                QuestionItem(
                    question_id=q.question_id,
                    text=q.question_text,
                    normalized=q.normalized_text,
                    answered=q.answer_source == "cms_menu",
                    matched_menu_id=q.matched_menu_id,
                )
                for q in questions
                if classify(q.question_text, q.normalized_text)[0] == "useful"
            ]
            clusters = build_clusters(usable)
            menus = _load_menus(session, customer_id)
            print(
                f"질문 {len(questions)}건 → 유효 {len(usable)}건 → 주제 {len(clusters)}개 "
                f"({window.label})\n"
            )
            print(f"{'질문수':>5} {'미응답':>5}  {'상태':10s} 대표 질문")
            print("-" * 78)
            for cluster in clusters:
                menu, _ = find_covering_menu(cluster, menus, COVERAGE_THRESHOLD)
                if menu is not None:
                    state = "응답중"
                elif cluster.size < MIN_CLUSTER_SIZE:
                    state = "표본부족"
                else:
                    state = "추천대상"
                print(f"{cluster.size:5d} {cluster.unanswered:5d}  {state:10s} {cluster.label}")
            return

        generator = get_generator(args.backend, args.model)
        print(f"백엔드: {generator.name}")
        run_id = run_analysis(session, window, customer_id, generator)
        print(f"analysis_run_id = {run_id}")


if __name__ == "__main__":
    main()
