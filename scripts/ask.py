"""예상 질문을 키오스크에 실제로 던진다.

  python scripts/ask.py                     예상 질문 전부 1회씩
  python scripts/ask.py --repeat 3          각 질문을 3회씩 (실제 트래픽에 가깝다)
  python scripts/ask.py --limit 20          앞에서 20건만
  python scripts/ask.py "주차장 어디예요?"    한 건만

--repeat 이 필요한 이유: 실제 키오스크에서 "주차장 어디예요" 는 하루에 수십 번 나온다.
표현마다 딱 한 번씩만 넣으면 어떤 주제도 '자주 묻는다' 는 기준(4건)을 넘지 못해서,
분석이 전부 표본 부족으로 넘어가 버린다. 그러면 아무것도 확인할 수 없다.

키오스크 화면에서 손으로 치는 것과 **같은 경로**를 탄다. services.kiosk_api.service.ask()
를 그대로 부르므로 매칭·판정·저장이 전부 실제와 동일하다. 질문을 DB 에 직접
INSERT 하지 않는 이유가 이것이다. 직접 넣으면 매칭 결과를 내가 지어내게 되고,
그러면 "실제로 돌아가는지" 를 확인할 수 없다.

시각은 최근 7일에 흩어 놓는다. 분석 구간이 기본 7일이라 전부 같은 시각에 몰아넣으면
구간 경계에 걸려 통째로 빠지거나 들어오거나 한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.seeds.expected_questions import interleaved, summary
from services.common.db import get_sessionmaker
from services.common.models import Kiosk, QuestionLog, Site
from services.kiosk_api.service import ask

CUSTOMER_CODE = "CHEONAN-CITY"


def _pick_kiosk(session: Session) -> Kiosk:
    kiosk = session.scalars(
        select(Kiosk).join(Site, Site.site_id == Kiosk.site_id).order_by(Kiosk.kiosk_id).limit(1)
    ).first()
    if kiosk is None:
        raise SystemExit("키오스크가 없다. 먼저 python scripts/seed.py 를 돌려라.")
    return kiosk


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", help="직접 던질 질문. 없으면 예상 질문 목록 전체")
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N건만")
    parser.add_argument("--repeat", type=int, default=1, help="각 질문을 N회씩 던진다")
    parser.add_argument("--seed", type=int, default=20260918, help="시각 분포 난수 씨앗")
    args = parser.parse_args()

    questions = args.question or interleaved()
    if args.limit > 0:
        questions = questions[: args.limit]
    # 반복은 목록을 통째로 되돌린다. 같은 질문을 연달아 넣으면 시각이 뭉쳐서
    # "하루 종일 주차만 물어봤다" 는 이상한 분포가 된다.
    questions = questions * max(args.repeat, 1)

    rng = random.Random(args.seed)
    now = dt.datetime.now(dt.UTC)

    with get_sessionmaker()() as session:
        kiosk = _pick_kiosk(session)
        counts = {"cms_menu": 0, "low_confidence": 0, "fallback": 0}

        for text_value in questions:
            row = ask(session, kiosk, text_value)
            # 최근 7일 안, 업무시간대(09~18시)에 흩어 놓는다.
            offset = dt.timedelta(
                days=rng.uniform(0.1, 6.8), hours=rng.uniform(0, 9), minutes=rng.uniform(0, 59)
            )
            row.asked_at = now - offset
            counts[row.answer_source] = counts.get(row.answer_source, 0) + 1
        session.commit()

        total = session.query(QuestionLog).count()

    if not args.question:
        print(summary())
    print(f"던진 질문 {len(questions)}건 · 누적 {total}건")
    print(
        f"  안내 완료 {counts.get('cms_menu', 0)} · "
        f"참고 안내 {counts.get('low_confidence', 0)} · "
        f"답변 불가 {counts.get('fallback', 0)}"
    )
    print("  답변 불가가 많은 것이 정상이다. CMS 가 비어 있으므로 전부 공백으로 잡힌다.")
    print("다음: python scripts/run_analysis.py")


if __name__ == "__main__":
    main()
