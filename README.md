# HANAAI — AI 키오스크 CMS 콘텐츠 추천 시스템

키오스크에 쌓인 사용자 질문을 AI가 분석해, **콘텐츠가 없어서 답하지 못한 주제**를 찾아내고
새 안내 메뉴 초안을 만들어 관리자에게 올린다. 관리자가 승인하면 키오스크가 그 질문에 답하기 시작한다.

지금은 **천안시청 안내 키오스크**를 대상으로 만들어져 있다.

```
사용자 질문 ──► 질문 로그 DB ──► 규칙(분류·묶기) ──► AI 3단계 ──► 메뉴 초안
                                                                     │
        키오스크가 답하기 시작 ◄── CMS 반영 ◄── 관리자 [추가/수정/무시]
```

---

## 빠른 시작

```bash
pip install -e ".[dev]"
cp .env.example .env

# PostgreSQL 준비 (Docker 없이)
psql -U postgres -c "CREATE ROLE hanaai LOGIN PASSWORD 'hanaai'"
psql -U postgres -c "CREATE DATABASE hanaai OWNER hanaai ENCODING 'UTF8'"
psql -U postgres -c "CREATE DATABASE hanaai_test OWNER hanaai ENCODING 'UTF8'"
HANAAI_DATABASE_URL=postgresql+psycopg://hanaai:hanaai@localhost:5432/hanaai_test alembic upgrade head

alembic upgrade head
python scripts/seed.py            # 천안시청 · 키오스크 1대 · 근거 문서 4건 (CMS 는 비어 있음)
python scripts/ask.py --repeat 3  # 예상 질문 291건 투입

uvicorn services.ops_api.main:app   --port 8000 --reload  # 관리자 콘솔
uvicorn services.kiosk_api.main:app --port 8001 --reload  # 가상 키오스크
```

- **가상 키오스크** <http://localhost:8001/> — 질문을 입력해 본다
- **관리자 콘솔** <http://localhost:8000/> — [분석 실행] 을 누르면 AI가 추천을 만든다

분석 AI는 로그인된 Claude CLI를 그대로 쓴다 (`claude login` 외 설정 불필요).
기업 배포에서는 `HANAAI_ANALYST_BACKEND=claude-api` 로 교체한다.

> **[분석 실행]은 몇 분 걸리고 LLM을 여러 번 부른다.**
> 실측: 질문 290건 기준 LLM 5~13회, 3~10분, 실행당 $0.5~1.5.
> 주제 수와 생성할 메뉴 수에 따라 달라진다.
> [분석 이력] 탭에서 지난 실행의 호출 수와 비용을 실제 값으로 볼 수 있다.

처음부터 다시 하려면 `python scripts/reset_data.py --yes` → `seed.py` → `ask.py`.

---

## 이 시스템이 지키는 원칙 네 가지

코드를 고치기 전에 이것부터 읽어야 한다. **네 가지 다 실제로 어겼다가 되돌린 것들이다.**

### 1. 키오스크는 모르는 질문에 답을 지어내지 않는다

답변 경로에 LLM이 **없다.** 답은 승인된 `cms_menu.body`에서 글자 그대로 나온다.
생성형으로 답해버리면 당장은 그럴듯하지만, **"어떤 주제에 콘텐츠가 없는가"를 영원히 알 수 없게 된다.**
그 신호를 얻는 것이 이 시스템의 존재 이유다.

LLM은 관리자가 [분석 실행]을 누를 때만 불린다.

### 2. AI는 제안만, 반영은 사람이

`content_proposal` → `cms_menu` 로 가는 유일한 길은 `services/ops_api/routers/review.py` 다.
분석 쪽 코드는 그 라우터에 접근하지 않는다. 승인 한 번이면 그대로 시민에게 보이는 문장이다.

### 3. 안내 문구의 사실은 전부 근거 문서에서 나온다

`db/seeds/cheonan_documents.py` 에 천안시청 누리집 실제 내용이 출처 URL과 함께 있다.
문서에 없는 것은 `(확인 후 입력 필요)` 로 남겨 관리자가 채우게 한다.
그럴듯한 거짓말은 틀린 것을 아무도 모르게 만들어서, 답을 안 하는 것보다 나쁘다.

### 4. 조각도 메뉴도 '사용자가 따로 묻는 단위' 로 쪼갠다

조각이 곧 답변의 단위다. `주차 안내` 한 덩어리에 면수·시간·요금·결제를 담았더니,
요금만 묻는 사람에게도 344면 이야기부터 통째로 읽혔다.

---

## AI 3단계 — 왜 Agent를 세 층으로 나눴나

질문 수천 건을 전부 LLM에 보낼 수는 없다. **싼 판단을 앞에, 비싼 일을 뒤에** 둔다.

아래 숫자는 **실제 실행 한 번(run 26)의 기록**이다. 실행마다 달라진다.

```
질문 289건
  │  [규칙] 욕설·자모만·너무 짧은 것 제거 → 비슷한 질문끼리 묶기
  ▼
주제 68개
  │  [규칙] 기존 메뉴가 답하는 주제, 4건 미만 주제 제외
  ▼
주제 15개
  │  [LLM · 상위 Agent]  agents/analyst/supervisor.py
  │  "이 주제, 안내할 가치가 있나"
  │  → '과태료 조회해줘' 9건 제외. 규칙으로는 못 거른다 —
  │     욕설도 아니고 짧지도 않고 반복해서 묻힌다.
  ▼
주제 13개
  │  [LLM · 하위 Agent]  agents/knowledge/evidence_agent.py
  │  "이 주제의 근거를 문서에서 찾아와라"
  │  → 근거 없으면 초안을 만들지 않는다. 지어내지 않고 숙제로 남긴다.
  ▼
주제 8개
  │  [LLM · 작성 Agent]  agents/analyst/content_agent.py
  │  초안 작성 → 실제 매처에 넣어보고 → 못 잡은 질문 있으면 다시 쓰게 함
  ▼
content_proposal 26건 (pending_review)
```

**Agent와 단일 LLM 호출의 차이**는 하나다. 도구를 쥐고, 결과를 보고, 판단을 바꾸는 반복이 있다.
- 작성 Agent가 쥐는 도구: `agents/analyst/verify.verify_draft()` — 읽기 전용, 부작용 없음
- 상위 Agent가 쥐는 도구: 하위 Agent 자체

각 층의 판단 근거는 전부 DB에 남아 관리자 화면에 뜬다.
판단만 보여주고 이유를 감추면 잘못 걸러진 것을 고칠 방법이 없다.

---

## 관리자 화면 — 할 일 중심 + 탭

```
┌ 26 승인 대기 ┐┌ 5 문서 보강 필요 ┐┌ 0 중복 메뉴 ┐┌ 9 미분석 질문 ┐

[할 일 ㉛] [CMS 메뉴] [질문] [근거 문서] [분석 이력]
```

맨 위는 **행동으로 이어지는 숫자만** 센다. `질문 297건`은 관리자가 할 수 있는 일이 없어서
[분석 이력] 탭으로 내렸다. 숫자를 하나 더 올리고 싶어지면,
그걸 보고 관리자가 무엇을 누를지 먼저 답해야 한다.

| 탭 | 내용 |
|---|---|
| 할 일 | 승인 대기 · 문서 보강 필요 · 중복 메뉴 |
| CMS 메뉴 | 메뉴 목록, 직접 작성/수정, 미리보기(실제 매처로 검증) |
| 질문 | 많이 나온 주제(조치 필요한 것부터) · 최근 질문 |
| 근거 문서 | AI가 사실로 쓰는 원본 (출처 링크) |
| 분석 이력 | 마지막 분석이 한 일 · 실행 이력과 **비용** · 질문 통계 |

---

## 어디를 고쳐야 하나

| 하고 싶은 것 | 고칠 곳 |
|---|---|
| 다른 기관으로 바꾸기 | `db/seeds/cheonan_documents.py` + `scripts/seed.py` 의 `CUSTOMER`/`SITE` |
| 근거 문서 추가 | `cheonan_documents.py` 에 추가 → `python scripts/seed.py` (덮어쓰기 안전) |
| LLM 백엔드 교체 | `agents/analyst/generator.py` **한 파일.** runner도 API도 어느 백엔드인지 모른다 |
| 검색을 임베딩으로 | `agents/knowledge/retriever.py` 의 `search()` **안쪽만.** 부르는 쪽은 이 함수만 본다 |
| 질문 묶기 정확도 | `agents/analyst/textutil.py` 의 `similarity()` **하나만.** 나머지는 이 함수를 본다 |
| 매칭 임계값 | `services/kiosk_api/service.py` 의 `STRONG_MATCH` / `WEAK_MATCH` |
| 스키마 변경 | `db/migrations/sql/*.sql` 에 새 파일 + `versions/` 에 리비전. `models.py`는 그걸 따라간다 |
| 화면 | `services/ops_api/static/index.html` (관리자), `services/kiosk_api/static/index.html` (키오스크) |

교체 지점을 한 곳에 모아둔 것이 이 구조의 요점이다. 흩어지면 갈아끼울 수 없다.

---

## 함정 — 모르면 몇 시간 날린다

**전부 실제로 당한 것들이다. 빠지면 오류가 아니라 조용히 이상하게 동작한다.**

| 함정 | 증상 | 대처 |
|---|---|---|
| SQL 문자열에 `%` | 주석 안에 있어도 쿼리가 서버에 닿기 전에 터짐 | `format()` 대신 `quote_ident`/`quote_literal` |
| `alembic.ini` 에 한글 | alembic 자체가 안 뜸 (configparser가 cp949로 읽음) | ASCII만 유지 |
| HTML 속 JS를 파이썬 치환으로 수정 | `\n` 이 진짜 줄바꿈으로 들어가 **스크립트 전체가 죽음.** 서버·API는 멀쩡해서 로그에 아무것도 안 남고 화면만 빈 채로 뜸 | 편집기로 직접 고치고 `pytest tests/test_static_pages.py` |
| Windows npm `.cmd` 셰임 | 개행이 든 `--system-prompt` 인자를 통째로 날림 | 네이티브 `claude.exe` 로 해석 (이미 구현됨) |
| `--exclude-dynamic-system-prompt-sections` 누락 | 모델이 JSON 대신 산문으로 답함 | 이미 붙어 있음. 지우지 말 것 |
| 전체 JSON Schema를 프롬프트에 첨부 | 모델이 자기 형식으로 이탈 | 계약 모델로 만든 유효한 예시 하나가 훨씬 강함 |
| 셸에서 한글 JSON을 `curl` 로 전송 | 깨짐 | 파이썬 클라이언트로 확인 |
| `alembic ... --sql` 출력 | 인코딩 오류 | `PYTHONIOENCODING=utf-8` |

---

## 프롬프트로 안 되는 것은 코드로

LLM에게 같은 것을 두 번 일렀는데 두 번 다 안 지킨 경우가 있었다.
**세 번째 시도 대신 검증 코드를 넣는다.** 각 파일 상단에 왜 그렇게 했는지 적혀 있다.

| 파일 | 막는 것 |
|---|---|
| `agents/analyst/placeholder.py` | 옆 메뉴가 다루는 내용을 `(확인 후 입력 필요)` 로 떠넘기는 것 (26건 중 25건이 그랬다) |
| `agents/analyst/verify.py` | 초안이 대상 질문을 실제로 못 잡는 것 |
| `agents/contracts/proposal.py` | 계약 위반 · 말투를 키워드에 넣는 것 |
| `services/ops_api/routers/review.py` | 같은 제목의 메뉴가 중복 생성되는 것 |

---

## 테스트

```bash
pytest                              # 114개
pytest -m "not db"                  # DB 없이
pytest tests/test_static_pages.py   # 화면 JS 문법 (node --check)
ruff check . && mypy .
```

테스트 대부분은 **실제로 겪은 오분류**에서 나왔다. 각 테스트의 docstring에 무엇이 잘못됐었는지 적혀 있다.
매칭·묶기를 고칠 때는 이 테스트들이 회귀 방지선이다.

---

## 알려진 한계

정직하게 적는다. 고치려면 어디를 봐야 하는지도 함께 적는다.

| 한계 | 설명 |
|---|---|
| 인증 없음 | 관리자 콘솔에 누구나 접근 가능. 운영 전 필수 |
| 낱말 기반 묶기 | "세정과 몇 층" / "축산과 몇 층" 처럼 공유 낱말이 없으면 따로 논다. `textutil.similarity()` 를 임베딩으로 바꾸면 해결 |
| 3건 이하 질문은 LLM까지 안 감 | 비용 때문에 의도한 것. 화면에서 `유효 (규칙)` 으로 구분 표시 |
| 층별 안내가 7개 메뉴로 쪼개짐 | "6층에 뭐 있어요"는 잘 잡히지만 "층별 안내 보여주세요"는 어느 것도 딱 안 맞음. 통합 메뉴가 하나 더 필요할 수 있음 |
| 실제 키오스크 미연동 | `scripts/ask.py` 가 대신한다. **DB에 직접 INSERT하지 않고** `service.ask()` 를 그대로 부른다 |

---

## 문서

| 문서 | 읽을 사람 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 구조를 바꾸기 전에. 흐름·데이터·각 결정의 이유 |
| [CLAUDE.md](CLAUDE.md) | **AI에게 코드를 맡길 때 자동으로 읽힌다.** 지켜야 할 제약이 들어 있다 |
| 각 모듈 상단 docstring | 그 파일이 왜 그렇게 생겼는지. 특히 `textutil.py`, `placeholder.py`, `supervisor.py` |

> **Claude Code나 Codex로 이 코드를 고칠 때**
> `CLAUDE.md` 가 자동으로 읽히므로 별도 설명 없이 바로 시키면 된다.
> 다른 도구를 쓴다면 `CLAUDE.md` 와 이 README를 먼저 읽히면 같은 효과가 난다.
> 주석은 "무엇"이 아니라 **"왜"** 를 적는 규칙이다. 고칠 때도 그 규칙을 따라달라.

스택: Python 3.11+ / FastAPI / PostgreSQL 16 / SQLAlchemy 2.x(동기) + Alembic / structlog
