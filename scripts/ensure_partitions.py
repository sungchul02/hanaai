"""event 월 파티션 미리 생성.

  python scripts/ensure_partitions.py --ahead 3

수집 API 도 스스로 파티션을 만들지만(services/ingest/service.py), 월초 첫 요청이
파티션 생성을 기다리게 되는 것보다 미리 만들어 두는 편이 낫다. cron 으로 매일 돌린다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from services.common.db import get_sessionmaker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ahead", type=int, default=3, help="몇 달 앞까지 만들지")
    args = parser.parse_args()

    today = dt.date.today().replace(day=1)
    with get_sessionmaker()() as session:
        for offset in range(-1, args.ahead + 1):
            month = today
            for _ in range(abs(offset)):
                month = (
                    (month - dt.timedelta(days=1)).replace(day=1)
                    if offset < 0
                    else (month + dt.timedelta(days=32)).replace(day=1)
                )
            name = session.execute(select(func.ensure_event_partition(month))).scalar_one()
            print(f"{month:%Y-%m} -> {name}")
        session.commit()


if __name__ == "__main__":
    main()
