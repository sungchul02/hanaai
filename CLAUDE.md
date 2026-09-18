# HANAAI 작업 규칙

키오스크 질문 로그를 분석해 새 CMS 콘텐츠를 추천하는 시스템.
전체 설계는 [docs/architecture.md](docs/architecture.md) 에 있다. 구조를 바꾸기 전에 읽는다.

## 스택

Python 3.11+ / FastAPI / PostgreSQL 16 / SQLAlchemy 2.x(동기) + Alembic / structlog

## 로컬 실행

```bash
pip install -e ".[dev]"
cp .env.example .env

alembic upgrade head
python scripts/seed.py
python scripts/simulate_questions.py --days 7 --per-day 55 --reset

uvicorn services.ops_api.main:app   --port 8000 --reload   # http://localhost:8000/
uvicorn services.kiosk_api.main:app --port 8001 --reload   # http://localhost:8001/

python scripts/run_analysis.py --dry-run     # 주제만 확인 (LLM 호출 없음)
python scripts/run_analysis.py               # 실제 추천 생성
```

### PostgreSQL 준비 (Docker 없이)

```bash
psql -U postgres -c "CREATE ROLE hanaai LOGIN PASSWORD 'hanaai'"
psql -U postgres -c "CREATE DATABASE hanaai OWNER hanaai ENCODING 'UTF8'"
psql -U postgres -c "CREATE DATABASE hanaai_test OWNER hanaai ENCODING 'UTF8'"

HANAAI_DATABASE_URL=postgresql+psycopg://hanaai:hanaai@localhost:5432/hanaai_test alembic upgrade head
```

### Windows 주의

- `alembic ... --sql` 로 SQL 을 출력할 때는 `PYTHONIOENCODING=utf-8` 을 준다.
- `alembic.ini` 는 ASCII 로만 유지한다. configparser 가 로케일 인코딩으로 읽어서
  한글 주석이 들어가면 alembic 자체가 뜨지 않는다.
- 셸에서 한글이 든 JSON 을 `curl` 로 보내면 깨진다. 확인은 파이썬 클라이언트로 한다.

## 지켜야 할 것

**스키마의 원본은 `db/migrations/sql/*.sql` 이다.** `services/common/models.py` 는 그 DDL 을
따라가는 ORM 매핑일 뿐이다. 스키마를 바꾸려면 새 `.sql` 과 새 리비전을 추가한다.

**SQL 문자열에 퍼센트 기호를 쓰지 않는다.** psycopg3 이 자기 파라미터 플레이스홀더로
해석해서, 주석 안에 있어도 쿼리가 서버에 닿기 전에 터진다. plpgsql 의 `format()` 대신
`quote_ident` / `quote_literal` 을 쓴다.

**키오스크가 모르는 질문에 생성형으로 답하지 않는다.** 답을 지어내기 시작하면
"어떤 주제에 콘텐츠가 없는가" 를 영원히 알 수 없게 된다. 그게 이 시스템의 존재 이유다.
답변 경로에는 LLM 이 없다. LLM 은 관리자가 [분석 실행] 을 누를 때만 호출된다.

**'키워드 하나 걸렸으니 통과' 로 두지 않는다.** 질문이 묻는 것을 메뉴가 얼마나 덮는지를
본다. 확신이 낮으면 `low_confidence` 로 남기고 분석에서는 공백으로 센다.
스치듯 맞은 답이 '응답 완료' 로 기록되면 진짜 공백이 묻힌다.

**추천을 CMS 에 자동 반영하지 않는다.** 승인 한 번이면 그대로 고객에게 보이는 문장이다.
`content_proposal` → `cms_menu` 로 가는 유일한 길은 `services/ops_api/routers/review.py` 다.
분석 쪽 코드는 그 라우터에 접근하지 않는다.

**계약을 통과하지 못한 LLM 응답은 저장하지 않되, 이유는 남긴다.**
조용히 버리면 프롬프트가 나빠진 것을 알아챌 수 없다. `analysis_run.error` 에 쌓인다.

**묶기 유사도는 `textutil.similarity()` 하나만 본다.** 임베딩으로 갈아끼울 때
그 함수만 바꾸면 되도록 유지한다.

**커버리지는 추측보다 기록을 믿는다.** 실제로 답이 나간 `matched_menu_id` 가 1순위고,
키워드 유사도는 답한 적 없을 때만 쓴다. 둘을 섞으면 응답 로직과 어긋난다.

## 분석 LLM 백엔드

`agents/analyst/generator.py` 한 파일이 교체 지점이다. runner 도 API 도 어느 백엔드인지 모른다.

| 값 | 필요한 것 |
|---|---|
| `claude-cli` (기본) | `claude login` 만 |
| `claude-api` | `pip install 'hanaai[agents]'` + `ANTHROPIC_API_KEY` |
| `passthrough` | 없음 (오프라인/테스트) |

### CLI 백엔드에서 반드시 지킬 것

전부 실제로 당한 것들이다. 빠지면 오류가 아니라 조용히 이상하게 동작한다.

1. **`.cmd` 셰임이 아니라 네이티브 실행 파일을 쓴다.** Windows npm 셰임은 cmd.exe 를
   거치면서 개행이 든 `--system-prompt` 인자를 통째로 날린다.
2. **`--exclude-dynamic-system-prompt-sections` 를 준다.** 없으면 Claude Code 의
   에이전트 프레이밍이 덧붙어 모델이 산문으로 답한다.
3. **전체 JSON Schema 를 프롬프트에 붙이지 않는다.** 붙였더니 자기 형식으로 이탈했다.
   계약 모델로 만든 유효한 예시 하나가 훨씬 강하다.

## 콘텐츠 작성 Agent

`agents/analyst/content_agent.py` · 단일 LLM 호출과 가르는 건 하나다.
**자기가 쓴 초안을 실제 매처에 넣어보고, 못 잡은 질문을 들고 다시 고친다.**

```
초안 작성 → [도구] verify_draft(초안, 대상 질문, 기존 메뉴)
         → 80퍼센트 미만이면 못 잡은 질문을 들고 수정 요청
         → 재검증 · 나빠졌으면 원안 유지 · 최대 2회
```

쥐는 도구는 `agents/analyst/verify.verify_draft()` 하나뿐이다. 읽기 전용이고
DB 도 파일도 건드리지 않는다. Agent 에게 도구를 늘려주고 싶으면 그 원칙을 먼저 확인하라.

지켜야 할 것:

- **수정본이 원안보다 나쁘면 되돌린다.** 고치다 망치는 경우가 실제로 있다.
- **수정 횟수에 상한을 둔다.** 고쳐도 안 되는 주제가 있고, 그때 비용만 늘어난다.
- **모델이 근거(evidence)와 제목을 바꿔치기하지 못하게 원본 값을 되돌린다.**
  수정 요청은 키워드와 본문을 고치라는 것이지 숫자를 다시 쓰라는 게 아니다.
- **주제 연결은 label 로 한다.** dedupe_key 같은 불투명한 문자열을 모델에게 옮기라고
  시켰더니 자기 식으로 지어냈다(wifi, parking). 사람이 읽을 수 있는 문장이어야 옮긴다.
- **검증 결과를 DB 에 남긴다.** 관리자가 "이 메뉴를 넣으면 104건 중 104건이 답이 된다" 를
  보고 승인해야지, 감으로 판단하게 두면 안 된다.

## 시뮬레이터

실제 키오스크가 붙기 전까지 `scripts/simulate_questions.py` 가 데이터를 만든다.
CMS 에 없는 주제(주차·와이파이·수유실)를 일부러 많이 물어보게 하므로,
**분석이 그걸 못 찾으면 분석이 틀린 것**이다.

## 현재 상태

| 구간 | 상태 |
|---|---|
| 질문 수집 · 로그 (화면 A) | 동작 |
| 분류 · 묶기 · 빈도 · 커버리지 | 동작 |
| AI 메뉴 초안 생성 | 동작 (Claude CLI) |
| 콘텐츠 작성 Agent (검증·수정 루프) | 동작 |
| 관리자 승인 → CMS 반영 (화면 B) | 동작 |
| 운영자 인증 | 미구현 |
| 실제 키오스크 연동 | 미구현 (시뮬레이터로 대체) |

## 코드 스타일

- 주석은 "무엇" 이 아니라 "왜" 를 적는다. 특히 남들이 다르게 짤 법한 선택에.
- 한글 주석을 쓴다. ruff 설정에 반영되어 있다.
- 새 기능에는 테스트를 함께 넣는다.
