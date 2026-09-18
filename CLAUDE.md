# HANAAI 작업 규칙

키오스크 운영 데이터 → 기능 도출 → 코드 생성 → 배포로 이어지는 자율 개선 루프.
전체 설계는 [docs/architecture.md](docs/architecture.md) 에 있다. 구조를 바꾸기 전에 읽는다.

## 스택

Python 3.11+ / FastAPI / PostgreSQL 16 / SQLAlchemy 2.x(동기) + Alembic / structlog

## 로컬 실행

```bash
docker compose up -d                 # PostgreSQL 16
cp .env.example .env
pip install -e ".[dev]"

alembic upgrade head                 # 스키마
python scripts/seed.py --demo        # 이벤트 사전 + 데모 플릿

uvicorn services.ingest.main:app --port 8001 --reload    # 수집 API
uvicorn services.ops_api.main:app --port 8000 --reload   # 운영 API  → http://localhost:8000/docs

python scripts/rollup.py             # 이벤트 → 시간 집계 (cron: 매시)
python scripts/run_analysis.py --dry-run   # 탐지기 후보만 확인
```

DB 없이도 `pytest` 는 통과한다. DB 가 필요한 테스트는 `-m db` 로 분리되어 있다.

### Docker 없이 (Windows 로컬)

PostgreSQL 16 을 직접 설치했다면 docker compose 대신 아래로 준비한다.

```bash
# 슈퍼유저로 역할과 DB 생성 (한 번만)
psql -U postgres -c "CREATE ROLE hanaai LOGIN PASSWORD 'hanaai'"
psql -U postgres -c "CREATE DATABASE hanaai OWNER hanaai ENCODING 'UTF8'"
psql -U postgres -c "CREATE DATABASE hanaai_test OWNER hanaai ENCODING 'UTF8'"

alembic upgrade head
python scripts/seed.py --demo

# 테스트 DB 도 따로 올려둔다 (tests/conftest.py 가 hanaai_test 를 본다)
HANAAI_DATABASE_URL=postgresql+psycopg://hanaai:hanaai@localhost:5432/hanaai_test alembic upgrade head
HANAAI_DATABASE_URL=postgresql+psycopg://hanaai:hanaai@localhost:5432/hanaai_test python scripts/seed.py
```

### Windows 주의

- `alembic ... --sql` 로 SQL 을 출력할 때는 `PYTHONIOENCODING=utf-8` 을 준다.
  마이그레이션 SQL 에 한글 주석이 있어서 콘솔 기본 인코딩(cp949)으로는 깨진다.
- `alembic.ini` 는 ASCII 로만 유지한다. configparser 가 로케일 인코딩으로 읽어서
  한글 주석이 들어가면 alembic 자체가 뜨지 않는다.

## 시뮬레이터

실제 키오스크가 붙기 전까지 `scripts/simulate_events.py` 가 데이터를 만든다.
정답을 아는 신호 4개를 의도적으로 심어두므로, **탐지기가 못 찾으면 탐지기가 틀린 것**이다.

```bash
python scripts/simulate_events.py --days 7 --sessions 35 --reset
python scripts/rollup.py --hours 170
python scripts/run_analysis.py --days 7 --dry-run
```

새 탐지기를 만들 때는 시뮬레이터에 그 탐지기가 찾을 신호를 먼저 심고 시작한다.

## 분석 LLM 백엔드

`agents/analyst/generator.py` 한 파일이 교체 지점이다. runner 도 orchestrator 도
어느 백엔드인지 모른다.

| 값 | 동작 | 필요한 것 |
|---|---|---|
| `claude-cli` (기본) | 로그인된 Claude CLI 를 헤드리스 호출 | `claude login` 만 |
| `claude-api` | Anthropic SDK | `pip install 'hanaai[agents]'` + `ANTHROPIC_API_KEY` |
| `passthrough` | LLM 없이 규칙 기반 | 없음 (오프라인/테스트) |

```bash
python scripts/run_analysis.py --days 7                      # 설정값 사용
python scripts/run_analysis.py --backend passthrough         # LLM 없이
HANAAI_ANALYST_BACKEND=claude-api python scripts/run_analysis.py   # 기업 배포
```

### CLI 백엔드에서 반드시 지켜야 하는 것

세 가지가 빠지면 조용히 이상하게 동작한다. 전부 실제로 당한 것들이다.

1. **`.cmd` 셰임이 아니라 네이티브 실행 파일을 쓴다.** Windows npm 셰임은 cmd.exe 를
   거치는데, 그 과정에서 개행이 든 `--system-prompt` 인자가 통째로 사라진다.
   오류가 아니라 '기본 프롬프트로 정상 동작' 이라 원인 찾기가 어렵다.
2. **`--exclude-dynamic-system-prompt-sections` 를 준다.** 없으면 Claude Code 의
   에이전트 프레이밍이 덧붙어, 모델이 "스키마 파일을 찾아보겠다" 며 산문으로 답한다.
3. **전체 JSON Schema 를 프롬프트에 붙이지 않는다.** 붙였더니 오히려 자기 형식으로
   이탈했다. 계약 모델로 만든 유효한 예시 하나가 훨씬 강하다.

응답은 항상 `FeatureProposal` 로 검증하고, 실패 건은 버리되 `analysis_run.error` 에
이유를 남긴다. 조용히 버리면 프롬프트가 나빠진 것을 알아챌 수 없다.

## 운영 콘솔

`services/ops_api/static/index.html` 하나다. 빌드 도구도 프레임워크도 없이 JSON API 를
그대로 호출한다. 지금 필요한 것은 '보이는 것' 이지 SPA 가 아니다.
서버를 띄우고 <http://localhost:8000/> 을 열면 된다.

## 지켜야 할 것

**스키마의 원본은 `db/migrations/sql/*.sql` 이다.** `services/common/models.py` 는 그 DDL 을
따라가는 ORM 매핑일 뿐이다. 스키마를 바꾸려면 새 `.sql` 과 새 리비전을 추가한다.
**이미 적용된 마이그레이션 파일은 수정하지 않는다.** (가드레일이 막는다)

**`event` 는 append-only 다.** UPDATE / DELETE 하지 않는다. 잘못 들어온 데이터는
`event_quarantine` 으로 격리하거나 집계에서 배제한다.

**`occurred_at` 조건 없는 `event` 쿼리를 쓰지 않는다.** 파티션 프루닝이 안 걸려 전체를 훑는다.

**SQL 문자열에 퍼센트 기호를 쓰지 않는다.** psycopg3 이 자기 파라미터 플레이스홀더로
해석해서, 주석 안에 있어도 쿼리가 서버에 닿기 전에 터진다. plpgsql 의 `format()` 대신
`quote_ident` / `quote_literal` 을 쓴다.

**장치 모델 비교는 '활동량 대비' 로 한다.** 같은 이벤트 타입 안에서 errors/events 를
계산하면, 심각도가 error 인 타입은 모두 비율이 1.0 이 되어 아무것도 잡히지 않는다.
(`tests/test_db_pipeline.py` 가 이걸 고정한다)

**새 이벤트 타입에는 분모를 같이 만든다.** 실패만 정의하면 "이 모델이 더 나쁜가"를
물을 수 없다. 비율의 분모가 없기 때문이다. `stt.failed` 를 넣으면 `stt.recognized` 도
넣어야 한다. 사전 파일의 `[분모]` 표시가 그 짝이며, 분모 이벤트는 `is_actionable=FALSE` 다.

**이벤트 payload 에 개인정보를 넣지 않는다.** 발화 원문, 얼굴 이미지, 결제 정보 금지.
길이·신뢰도·언어코드 같은 파생값만 넣는다. 대상이 대화형 키오스크라 특히 위험하다.

**분석은 `event_rollup_hourly` 를 본다.** 탐지기에서 `event` 원본을 직접 집계하지 않는다.

**Agent 는 Agent 를 직접 호출하지 않는다.** 모든 연결은 DB 상태 전이를 통한다
(`feature_proposal.status` → `dev_run` → `deployment`). orchestrator 만 그 전이를 감시한다.

**`agents/contracts/` 는 함부로 바꾸지 않는다.** 분석 Agent 와 개발 Agent 의 유일한 계약이고,
`feature_proposal.body` 에 이미 저장된 JSON 도 계속 읽혀야 한다.

**제안을 `approved` 로 바꾸는 것은 사람뿐이다.** Agent 에게 그 권한을 주지 않는다.

## 현재 상태 (M1 뼈대)

| 구간 | 상태 |
|---|---|
| 스키마 · 마이그레이션 | 적용 완료, up/down 왕복 검증됨 |
| 수집 API (이벤트 / 인벤토리) | 동작 |
| 운영 API (조회 · 검토 게이트) | 동작 |
| 집계 (rollup) | 동작, 실제 데이터로 검증됨 |
| 탐지기 | **5개 전부 구현·검증됨** (주변장치 오류율 / 급증 / 지연회귀 / 재시도폭주 / 퍼널이탈) |
| 분석 Agent | **claude-cli 백엔드로 실제 동작.** 백엔드 교체 가능 |
| 개발 Agent | 가드레일 · 워크스페이스만. `implement()` 는 M5 |
| CI/CD | CI 있음(미실행). 카나리 배포는 M6 |
| 운영 콘솔 | `/ui` 단일 HTML 대시보드. 동작 |

`NotImplementedError` 가 있는 자리는 전부 의도된 빈칸이며, 어느 마일스톤에서 채우는지
docstring 에 적혀 있다.

## 코드 스타일

- 주석은 "무엇"이 아니라 "왜"를 적는다. 특히 남들이 다르게 짤 법한 선택에.
- 한글 주석을 쓴다. ruff 설정에 이미 반영되어 있다.
- 새 기능에는 테스트를 함께 넣는다. CI 가 없으면 막는다.
