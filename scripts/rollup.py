"""이벤트 → 시간 집계 배치.

python scripts/rollup.py                 # 최근 3시간 재집계 (cron: 매시)
python scripts/rollup.py --hours 48      # 지연 도착분 보정용
python scripts/rollup.py --from 2026-09-01 --to 2026-09-15
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.common.db import get_sessionmaker
from services.common.rollup import rollup_range, rollup_recent


def _parse_date(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).replace(tzinfo=dt.UTC)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=3, help="최근 N시간 재집계")
    parser.add_argument("--from", dest="start", type=_parse_date)
    parser.add_argument("--to", dest="end", type=_parse_date)
    args = parser.parse_args()

    with get_sessionmaker()() as session:
        if args.start and args.end:
            rows = rollup_range(session, args.start, args.end)
            print(f"{args.start} ~ {args.end}: {rows}행")
        else:
            rows = rollup_recent(session, hours=args.hours)
            print(f"최근 {args.hours}시간: {rows}행")


if __name__ == "__main__":
    main()
