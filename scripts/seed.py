"""초기 데이터 적재.

  python scripts/seed.py              # 이벤트 사전만
  python scripts/seed.py --demo       # 데모 지점/키오스크/장치까지

--demo 는 로컬에서 파이프라인을 끝까지 돌려보기 위한 것이다. 운영에서는 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.common.db import get_sessionmaker
from services.common.models import (
    Customer,
    EventType,
    Kiosk,
    Peripheral,
    PeripheralModel,
    Site,
)

SEEDS_DIR = Path(__file__).resolve().parents[1] / "db" / "seeds"

# 가상의 고객사다. 실제 거래처 이름을 넣지 않는다.
DEMO_CUSTOMERS = [
    ("CUST-A", "A금융"),
    ("CUST-B", "B프랜차이즈"),
]

# 고객사가 다르면 지점 코드가 겹쳐도 된다는 것을 보여주려고 GANGNAM-01 을 양쪽에 둔다.
DEMO_SITES = [
    ("CUST-A", "GANGNAM-01", "강남지점", "서울"),
    ("CUST-A", "BUSAN-01", "부산지점", "부산"),
    ("CUST-B", "GANGNAM-01", "강남점", "서울"),
]

# 같은 종류를 두 벤더로 깔아두어야 "모델 간 비교" 탐지기가 의미를 가진다.
DEMO_MODELS = [
    ("printer", "Epson", "TM-T88VI", "1.2.0"),
    ("printer", "Bixolon", "SRP-330II", "2.0.1"),
    ("card_reader", "Ingenico", "IPP320", "3.4.0"),
    ("scanner", "Zebra", "DS457", "1.0.5"),
]


def seed_event_types() -> int:
    """db/seeds/*.sql 을 이름순으로 전부 적용한다.

    사전이 커지면 한 파일로는 읽기 어렵다. 제품군/분류별로 파일을 나누고 여기서
    순서대로 넣는다. 각 파일이 ON CONFLICT DO UPDATE 라 몇 번 돌려도 같다.
    """
    with get_sessionmaker()() as session:
        for path in sorted(SEEDS_DIR.glob("*.sql")):
            session.connection().exec_driver_sql(path.read_text(encoding="utf-8"))
            print(f"  적용: {path.name}")
        session.commit()
        return session.scalar(select(func.count()).select_from(EventType)) or 0


def seed_demo() -> None:
    with get_sessionmaker()() as session:
        for code, name in DEMO_CUSTOMERS:
            session.execute(
                pg_insert(Customer)
                .values(code=code, name=name)
                .on_conflict_do_nothing(index_elements=["code"])
            )
        session.commit()

        customer_ids = {c.code: c.customer_id for c in session.scalars(select(Customer))}
        for customer_code, code, name, region in DEMO_SITES:
            session.execute(
                pg_insert(Site)
                .values(
                    customer_id=customer_ids[customer_code],
                    code=code,
                    name=name,
                    region=region,
                )
                # 지점 코드는 고객사 안에서만 유일하다
                .on_conflict_do_nothing(index_elements=["customer_id", "code"])
            )
        session.commit()

        model_ids: dict[str, int] = {}
        for kind, vendor, model_name, driver_version in DEMO_MODELS:
            session.execute(
                pg_insert(PeripheralModel)
                .values(
                    kind=kind,
                    vendor=vendor,
                    model_name=model_name,
                    driver_version=driver_version,
                    driver_name=f"{vendor.lower()}-driver",
                    protocol="usb",
                )
                .on_conflict_do_nothing(index_elements=["vendor", "model_name", "driver_version"])
            )
        session.commit()

        for _kind, vendor, model_name, driver_version in DEMO_MODELS:
            model_ids[f"{vendor}:{model_name}"] = session.execute(
                select(PeripheralModel.peripheral_model_id).where(
                    PeripheralModel.vendor == vendor,
                    PeripheralModel.model_name == model_name,
                    PeripheralModel.driver_version == driver_version,
                )
            ).scalar_one()

        # 지점 코드가 고객사 간에 겹칠 수 있으므로 코드만으로 키를 잡지 않는다.
        code_by_customer_id = {cid: code for code, cid in customer_ids.items()}
        printers = ["Epson:TM-T88VI", "Bixolon:SRP-330II"]

        for site in session.scalars(select(Site)):
            customer_code = code_by_customer_id[site.customer_id]
            for n in range(1, 4):
                # 시리얼은 제조사가 매기므로 전역 유일하다
                serial = f"{customer_code}-{site.code}-K{n:02d}"
                kiosk = session.scalar(select(Kiosk).where(Kiosk.serial_no == serial))
                if kiosk is None:
                    kiosk = Kiosk(
                        serial_no=serial,
                        site_id=site.site_id,
                        model="HANA-K1",
                        status="active",
                        app_version="1.0.0",
                        os_version="Windows 10 IoT",
                    )
                    session.add(kiosk)
                    session.flush()

                    # 지점 안에서 프린터 벤더를 섞는다
                    printer_key = printers[n % len(printers)]
                    for slot, model_key in (
                        ("USB1", printer_key),
                        ("COM3", "Ingenico:IPP320"),
                        ("USB2", "Zebra:DS457"),
                    ):
                        session.add(
                            Peripheral(
                                kiosk_id=kiosk.kiosk_id,
                                peripheral_model_id=model_ids[model_key],
                                slot=slot,
                                serial_no=f"{serial}-{slot}",
                                firmware_version="1.0.0",
                            )
                        )
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="데모 지점/키오스크/장치 생성")
    args = parser.parse_args()

    count = seed_event_types()
    print(f"event_type: {count}건")

    if args.demo:
        seed_demo()
        print("데모 지점/키오스크/주변장치 생성 완료")


if __name__ == "__main__":
    main()
