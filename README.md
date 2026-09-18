# HANAAI — AI 키오스크 CMS 콘텐츠 추천 시스템

키오스크 사용자 질문 데이터를 AI가 분석하여 반복적으로 발생하는 유용한 질문을 찾고,
새로운 FAQ·안내 메뉴를 자동으로 제안해 관리자가 CMS에 손쉽게 반영하도록 하는 시스템.

```
사용자 질문 ──► 질문 로그 DB ──► 분석(분류·묶기·빈도) ──► AI 메뉴 초안
                                                            │
       키오스크가 답하기 시작 ◄── CMS 반영 ◄── 관리자 [추가/수정/무시]
```

## 빠른 시작

```bash
pip install -e ".[dev]"
cp .env.example .env

alembic upgrade head
python scripts/seed.py                                    # 고객사·키오스크·기본 메뉴
python scripts/simulate_questions.py --days 7 --per-day 55 # 가상 질문 생성

uvicorn services.ops_api.main:app   --port 8000 --reload  # 관리자 콘솔
uvicorn services.kiosk_api.main:app --port 8001 --reload  # 가상 키오스크
```

- **가상 키오스크** <http://localhost:8001/> — 질문을 입력해 보세요
- **관리자 콘솔** <http://localhost:8000/> — [분석 실행] 을 누르면 AI가 추천을 만듭니다

분석 AI는 로그인된 Claude CLI를 그대로 씁니다 (`claude login` 외 설정 불필요).
기업 배포에서는 `HANAAI_ANALYST_BACKEND=claude-api` 로 교체합니다.

DB 없이도 `pytest` 는 통과합니다. DB가 필요한 테스트는 `-m db` 로 분리되어 있습니다.

## 구성

| 경로 | 내용 |
|---|---|
| `db/migrations/` | 스키마 (단일 원본) |
| `services/kiosk_api/` | 화면 A — 가상 키오스크, 질문 응답·로그 적재 |
| `services/ops_api/` | 화면 B — 관리자 CMS, 승인 게이트, 분석 실행 |
| `agents/analyst/` | 분류·묶기·커버리지(규칙) + 메뉴 초안 생성(LLM) |
| `agents/contracts/` | AI 출력 계약 |
| `scripts/` | 시드 · 질문 시뮬레이터 · 분석 실행 |

## 문서

- [설계 문서](docs/architecture.md) — 흐름, 데이터, 분석 2단계, 알려진 한계
- [작업 규칙](CLAUDE.md) — 로컬 실행, 지켜야 할 제약

스택: Python 3.11+ / FastAPI / PostgreSQL 16 / SQLAlchemy 2.x + Alembic
