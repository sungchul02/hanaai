"""분석 1회 실행.

  python scripts/run_analysis.py                 # 최근 7일, PassthroughGenerator
  python scripts/run_analysis.py --days 14 --dry-run   # 후보만 출력하고 저장하지 않음

--dry-run 이 중요하다. 탐지기를 손볼 때는 DB 에 제안을 쌓지 않고 후보만 보면서 조정한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.analyst.generator import get_generator
from agents.analyst.runner import collect_candidates, run_analysis
from agents.detectors import Window
from services.common.db import get_sessionmaker
from services.common.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true", help="후보만 출력")
    parser.add_argument(
        "--backend",
        choices=["claude-cli", "claude-api", "passthrough"],
        help="분석 백엔드. 기본은 HANAAI_ANALYST_BACKEND 설정값",
    )
    parser.add_argument("--model", help="백엔드별 모델 이름 (CLI: opus/sonnet)")
    args = parser.parse_args()

    configure_logging()
    window = Window.last_days(args.days)

    with get_sessionmaker()() as session:
        if args.dry_run:
            candidates = collect_candidates(session, window)
            print(f"후보 {len(candidates)}건 ({window.label})")
            for c in candidates:
                e = c.evidence
                print(
                    f"  [{c.detector_id}] {c.signal}\n"
                    f"      관측 {e.observed:.4f} / 기준 {e.baseline:.4f} "
                    f"/ 표본 {e.sample_size} / 키오스크 {e.affected_kiosks}대"
                )
            return

        generator = get_generator(args.backend, args.model)
        print(f"백엔드: {generator.name}")
        run_id = run_analysis(session, window, generator)
        print(f"analysis_run_id = {run_id}")


if __name__ == "__main__":
    main()
