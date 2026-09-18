"""질문 로그 시뮬레이터.

실제 키오스크가 붙기 전까지 분석을 만들고 검증하기 위한 도구다.
실제 응답 경로(services.kiosk_api.service.ask)를 그대로 통과시키므로,
매칭 로직과 로그 적재까지 같이 돌려본다.

  python scripts/simulate_questions.py --days 7 --per-day 60
  python scripts/simulate_questions.py --days 7 --per-day 60 --reset

## 심어둔 공백

CMS 에 메뉴가 없는 주제를 일부러 많이 물어보게 한다. 정답을 아는 데이터라서,
분석이 이걸 못 찾으면 분석이 틀린 것이다.

  주차   — 메뉴 없음, 가장 많이 물음   → 1순위로 추천되어야 한다
  와이파이 — 메뉴 없음, 중간 빈도
  수유실  — 메뉴 없음, 낮은 빈도       → 표본이 적어 추천에서 빠질 수도 있다
  화장실/운영시간/엘리베이터 — 메뉴 있음 → 추천되면 안 된다 (이미 답하고 있다)
  잡담/욕설 — 규칙으로 걸러져야 한다
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from services.common.db import get_sessionmaker
from services.common.models import Kiosk, QuestionLog
from services.kiosk_api import service

# (주제, 가중치, 표현들)
TOPICS: list[tuple[str, int, list[str]]] = [
    # --- CMS 에 없는 주제. 이것들이 추천으로 올라와야 한다.
    (
        "주차",
        30,
        [
            "주차장 어디예요?",
            "차 어디에 대면 돼요?",
            "주차 가능한가요?",
            "주차장 위치 알려주세요",
            "주차비 얼마예요?",
            "주차 무료인가요?",
            "지하주차장 있어요?",
            "주차 어디에 해요",
            "차 세울 데 있나요?",
        ],
    ),
    (
        "와이파이",
        14,
        [
            "와이파이 되나요?",
            "와이파이 비밀번호 뭐예요?",
            "인터넷 연결 어떻게 해요?",
            "무료 와이파이 있어요?",
            "와이파이 이름이 뭐죠?",
        ],
    ),
    (
        "수유실",
        5,
        [
            "수유실 어디예요?",
            "아기 기저귀 갈 데 있나요?",
            "수유실 있어요?",
        ],
    ),
    # --- CMS 에 있는 주제. 답이 나가야 하고, 추천되면 안 된다.
    (
        "화장실",
        20,
        [
            "화장실 어디예요?",
            "화장실 어디 있어요?",
            "화장실 위치 알려주세요",
            "장애인 화장실 있나요?",
        ],
    ),
    (
        "운영시간",
        16,
        [
            "몇 시까지 해요?",
            "운영시간 어떻게 되나요?",
            "언제까지 열어요?",
            "토요일에도 하나요?",
            "영업시간 알려주세요",
        ],
    ),
    (
        "엘리베이터",
        8,
        [
            "엘리베이터 어디 있어요?",
            "승강기 어디예요?",
            "올라가는 길 알려주세요",
        ],
    ),
    ("분실물", 4, ["분실물 어디서 찾아요?", "지갑 잃어버렸어요"]),
    # --- 걸러져야 하는 것들
    (
        "잡담",
        6,
        [
            "너 몇 살이야?",
            "심심해",
            "너 이름이 뭐야?",
            "사랑해",
            "뭐하고 놀까",
        ],
    ),
    ("욕설", 2, ["바보야", "멍청이"]),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--per-day", type=int, default=60, help="키오스크·하루당 질문 수")
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument("--reset", action="store_true", help="기존 질문 로그를 지우고 시작")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(days=args.days)

    population: list[tuple[str, str]] = []
    for topic, weight, phrasings in TOPICS:
        for _ in range(weight):
            population.append((topic, rng.choice(phrasings)))

    with get_sessionmaker()() as session:
        if args.reset:
            session.execute(text("DELETE FROM question_cluster_member"))
            session.execute(text("UPDATE question_log SET cluster_id = NULL"))
            session.execute(text("DELETE FROM content_proposal"))
            session.execute(text("DELETE FROM question_cluster"))
            session.execute(text("DELETE FROM analysis_run"))
            session.execute(text("DELETE FROM question_log"))
            session.commit()
            print("기존 질문 로그·분석 결과 삭제")

        kiosks = list(session.scalars(select(Kiosk).order_by(Kiosk.kiosk_id)))
        if not kiosks:
            print("키오스크가 없다. 먼저 python scripts/seed.py 를 돌려라.")
            return

        counts: dict[str, int] = {}
        total = 0
        for kiosk in kiosks:
            for day in range(args.days):
                day_start = start + dt.timedelta(days=day)
                for _ in range(args.per_day):
                    topic, question = rng.choice(population)
                    # 영업시간(09~19시)에 몰리게 한다
                    when = day_start + dt.timedelta(
                        hours=rng.triangular(9, 19, 13), minutes=rng.uniform(0, 59)
                    )
                    if when >= end:
                        continue
                    entry = service.ask(
                        session, kiosk, question, session_id=uuid.uuid4(), input_mode="touch"
                    )
                    # ask() 는 '지금' 으로 기록한다. 시뮬레이션이므로 과거 시각으로 되돌린다.
                    entry.asked_at = when
                    counts[topic] = counts.get(topic, 0) + 1
                    total += 1
            session.commit()

        answered = session.scalar(
            select(text("count(*)"))
            .select_from(QuestionLog)
            .where(QuestionLog.answer_source == "cms_menu")
        )

    print(f"\n질문 {total:,}건 생성 · 구간 {start:%m-%d} ~ {end:%m-%d}")
    print(f"그중 기존 메뉴가 답한 것 {answered:,}건\n")
    for topic, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {topic:8s} {count:5,}건")
    print("\n다음: 관리자 콘솔에서 [분석 실행] 또는 python scripts/run_analysis.py")


if __name__ == "__main__":
    main()
