# HANAAI

키오스크 운영 데이터를 수집해 → 필요한 기능을 도출하고 → 코드를 생성하고 → 배포까지 잇는
자율 개선 루프.

```
키오스크 ──이벤트──► 데이터 레이어 ──집계──► 탐지기 ──후보──► 분석 Agent
                          ▲                                      │
                          │                                   제안 (사람 승인)
                     배포 결과 검증                                 │
                          │                                      ▼
                    CI/CD ◄── PR ◄── 개발 Agent ◄── orchestrator ◄┘
```

| 과제 | 내용 | 상태 |
|---|---|---|
| 1 | 키오스크 주변장치·이벤트 로그 DB 및 운영 시스템 | 뼈대 동작 |
| 2 | 수집 데이터 기반 필요 기능 분석 Agent | **탐지기 5개 + Claude CLI 연결 완료**, 백엔드 교체 가능 |
| 3 | 분석 내용 기반 개발 Agent | 가드레일·워크스페이스 동작, 구현 루프는 M5 |
| 4 | Agent 연결용 CI/CD | CI 동작, 카나리 배포는 M6 |

## 빠른 시작

```bash
docker compose up -d
cp .env.example .env
pip install -e ".[dev]"

alembic upgrade head
python scripts/seed.py --demo

uvicorn services.ops_api.main:app --port 8000 --reload   # 콘솔 http://localhost:8000/
uvicorn services.ingest.main:app --port 8001 --reload
```

DB 가 없어도 `pytest` 는 통과한다.

분석 Agent 는 로그인된 Claude CLI 를 그대로 쓴다 (`claude login` 외 설정 불필요).
기업 배포에서는 `HANAAI_ANALYST_BACKEND=claude-api` 로 교체한다.

## 문서

- [설계 문서](docs/architecture.md) — 아키텍처, 스키마, 로드맵, 열린 질문
- [작업 규칙](CLAUDE.md) — 로컬 실행, 지켜야 할 제약, 현재 구현 상태

스택: Python 3.11+ / FastAPI / PostgreSQL 16 / SQLAlchemy 2.x + Alembic
