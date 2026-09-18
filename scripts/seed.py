"""초기 데이터 적재.

  python scripts/seed.py

고객사 1곳, 지점 1곳, 키오스크 3대와 기본 CMS 메뉴를 만든다.
주차/와이파이/수유실 메뉴는 일부러 넣지 않는다. AI 가 찾아낼 공백이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.common.db import get_sessionmaker
from services.common.models import CmsMenu, Customer, Kiosk, Site

SEEDS_DIR = Path(__file__).resolve().parents[1] / "db" / "seeds"

CUSTOMER = ("BLDG-A", "가나빌딩 관리사무소")
SITE = ("MAIN", "본관")
KIOSKS = [
    ("KIOSK-LOBBY-01", "1층 로비"),
    ("KIOSK-LOBBY-02", "1층 정문"),
    ("KIOSK-3F-01", "3층 엘리베이터 앞"),
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

        for path in sorted(SEEDS_DIR.glob("*.sql")):
            session.connection().exec_driver_sql(path.read_text(encoding="utf-8"))
            print(f"  적용: {path.name}")
        session.commit()

        menus = session.scalar(
            select(func.count()).select_from(CmsMenu).where(CmsMenu.customer_id == customer_id)
        )
        print(f"고객사 1 · 지점 1 · 키오스크 {len(KIOSKS)} · CMS 메뉴 {menus}")
        print("주차 / 와이파이 / 수유실 메뉴는 일부러 없음 (AI 가 찾아낼 공백)")


if __name__ == "__main__":
    main()
