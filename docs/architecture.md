# HANAAI 아키텍처 설계

> 키오스크 운영 데이터를 수집해 → 필요한 기능을 스스로 도출하고 → 코드를 생성하고 → 배포까지 잇는 자율 개선 루프.

- 작성일: 2026-09-18
- 상태: 초안 (Phase 1 착수 전 합의용)
- 스택: Python 3.12 / FastAPI / PostgreSQL 16 / SQLAlchemy 2.x + Alembic / Claude Agent SDK

---

## 0. 전체 그림

4개 과제는 독립된 시스템이 아니라 **하나의 닫힌 루프**의 4개 구간이다.

```
 [키오스크 플릿]
      │  (1) 주변장치 상태 · 이벤트 로그
      ▼
 ┌──────────────────┐
 │  데이터 레이어    │  과제 1: DB + 수집/운영 시스템
 │  PostgreSQL      │
 └────────┬─────────┘
          │  집계 · 이상패턴 (결정론적 전처리)
          ▼
 ┌──────────────────┐
 │  분석 Agent      │  과제 2: "무슨 기능이 필요한가" 도출
 └────────┬─────────┘
          │  FeatureProposal (구조화 JSON) + 사람 승인 게이트
          ▼
 ┌──────────────────┐
 │  개발 Agent      │  과제 3: 브랜치 · 코드 · 테스트 · PR 생성
 └────────┬─────────┘
          │  PR
          ▼
 ┌──────────────────┐
 │  CI/CD           │  과제 4: 검증 → 카나리 배포 → 지표 감시
 └────────┬─────────┘
          │  배포된 변경이 다시 이벤트를 만든다
          └────────────────────► (루프 닫힘)
```

**핵심 설계 원칙 3가지**

1. **모든 구간은 DB를 통해 통신한다.** Agent가 Agent를 직접 호출하지 않는다. 제안·실행·배포 이력이 전부 테이블에 남아야 재현·감사·롤백이 된다.
2. **LLM은 판단만, 집계는 SQL이 한다.** 이벤트 수백만 건을 모델에 넣지 않는다. 결정론적 룰과 통계가 후보를 좁히고, LLM은 좁혀진 후보를 해석·우선순위화한다.
3. **자동화는 PR까지, 머지는 사람이.** 루프 전체를 무인으로 돌리는 건 맨 마지막 단계다. M5까지는 승인 게이트를 반드시 둔다.

---

## 1. 과제 1 — 데이터 레이어

### 1.1 도메인 모델

| 개념 | 설명 | 변경 빈도 |
|---|---|---|
| `customer` | 키오스크를 납품받는 고객사 | 거의 없음 |
| `site` | 고객사의 설치 지점(영업점/매장) | 거의 없음 |
| `kiosk` | 키오스크 본체 1대 | 낮음 |
| `peripheral_model` | 주변장치 **모델** 카탈로그 (벤더/모델/드라이버) | 낮음 |
| `peripheral` | 특정 키오스크에 실제 장착된 **개체** + 장착 이력 | 중간 |
| `event_type` | 이벤트 사전 (통제된 어휘) | 낮음 |
| `event` | 이벤트 로그 본체 (append-only, 파티션) | 매우 높음 |
| `event_rollup_hourly` | 시간 단위 집계 — 분석 Agent의 실제 입력 | 높음 |

`peripheral_model`과 `peripheral`을 분리하는 이유: "이 프린터 개체가 고장났다"와 "이 **모델**의 프린터가 전 지점에서 3배 더 고장난다"는 전혀 다른 질문이고, 분석 Agent가 답해야 하는 것은 후자다. 모델을 분리하지 않으면 그 집계 자체가 불가능해진다.

### 1.2 스키마 (초안 DDL)

```sql
CREATE TYPE kiosk_status    AS ENUM ('provisioning','active','maintenance','retired');
CREATE TYPE peripheral_kind AS ENUM ('printer','scanner','card_reader','cash_acceptor',
                                     'nfc','pinpad','camera','display','speaker','other');

-- 우리는 키오스크를 만들어 파는 쪽이므로 플릿이 고객사 단위로 갈린다
CREATE TABLE customer (
    customer_id  BIGSERIAL PRIMARY KEY,
    code         TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    contact      JSONB NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE site (
    site_id      BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT NOT NULL REFERENCES customer(customer_id),
    code         TEXT NOT NULL,
    name         TEXT NOT NULL,
    region       TEXT,
    timezone     TEXT NOT NULL DEFAULT 'Asia/Seoul',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 지점 코드는 고객사 안에서만 유일하다
    UNIQUE (customer_id, code)
);

CREATE TABLE kiosk (
    kiosk_id      BIGSERIAL PRIMARY KEY,
    serial_no     TEXT UNIQUE NOT NULL,
    site_id       BIGINT NOT NULL REFERENCES site(site_id),
    model         TEXT NOT NULL,
    os_version    TEXT,
    app_version   TEXT,
    status        kiosk_status NOT NULL DEFAULT 'provisioning',
    installed_at  DATE,
    last_seen_at  TIMESTAMPTZ,
    meta          JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON kiosk (site_id, status);
CREATE INDEX ON kiosk (last_seen_at DESC);

-- 주변장치 카탈로그 (모델 단위)
CREATE TABLE peripheral_model (
    peripheral_model_id BIGSERIAL PRIMARY KEY,
    kind            peripheral_kind NOT NULL,
    vendor          TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    driver_name     TEXT,
    driver_version  TEXT NOT NULL DEFAULT '',
    protocol        TEXT,                        -- usb / serial / tcp / bluetooth
    capabilities    JSONB NOT NULL DEFAULT '{}', -- 예: {"dpi": 203, "auto_cut": true}
    eol_date        DATE,
    UNIQUE (vendor, model_name, driver_version)
);

-- 실제 장착된 개체 + 장착/탈착 이력
CREATE TABLE peripheral (
    peripheral_id       BIGSERIAL PRIMARY KEY,
    kiosk_id            BIGINT NOT NULL REFERENCES kiosk(kiosk_id),
    peripheral_model_id BIGINT NOT NULL REFERENCES peripheral_model(peripheral_model_id),
    slot                TEXT NOT NULL,           -- COM3 / USB1 등 물리·논리 슬롯
    serial_no           TEXT,
    firmware_version    TEXT,
    attached_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    detached_at         TIMESTAMPTZ,
    meta                JSONB NOT NULL DEFAULT '{}'
);
-- 한 슬롯에 현재 장착된 장치는 하나뿐
CREATE UNIQUE INDEX peripheral_active_slot_uq
    ON peripheral (kiosk_id, slot) WHERE detached_at IS NULL;
CREATE INDEX ON peripheral (peripheral_model_id);

-- 이벤트 사전: 통제된 어휘. 분석 Agent가 자유 텍스트를 추측하지 않게 하는 장치.
CREATE TABLE event_type (
    event_type_id  INT PRIMARY KEY,
    code           TEXT UNIQUE NOT NULL,   -- printer.paper_jam, session.timeout ...
    category       TEXT NOT NULL,          -- hardware / ux / transaction / system
    default_sev    SMALLINT NOT NULL,      -- 10 debug 20 info 30 warn 40 error 50 fatal
    description    TEXT NOT NULL,
    is_actionable  BOOLEAN NOT NULL DEFAULT TRUE   -- 분석 대상 포함 여부
);

-- 이벤트 본체: append-only, 월 단위 range 파티션
CREATE TABLE event (
    occurred_at    TIMESTAMPTZ NOT NULL,
    kiosk_id       BIGINT      NOT NULL,
    source_seq     BIGINT      NOT NULL,   -- 키오스크 로컬 단조증가 시퀀스
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    peripheral_id  BIGINT,
    event_type_id  INT         NOT NULL REFERENCES event_type(event_type_id),
    severity       SMALLINT    NOT NULL,
    session_id     UUID,
    duration_ms    INTEGER,
    error_code     TEXT,
    payload        JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (occurred_at, kiosk_id, source_seq)
) PARTITION BY RANGE (occurred_at);

CREATE INDEX ON event (event_type_id, occurred_at DESC);
CREATE INDEX ON event (kiosk_id, occurred_at DESC);
CREATE INDEX ON event (peripheral_id, occurred_at DESC) WHERE peripheral_id IS NOT NULL;
CREATE INDEX ON event USING GIN (payload jsonb_path_ops);

-- 분석 Agent의 실제 입력이 되는 집계 테이블
CREATE TABLE event_rollup_hourly (
    bucket              TIMESTAMPTZ NOT NULL,
    kiosk_id            BIGINT      NOT NULL,
    peripheral_model_id BIGINT      NOT NULL DEFAULT 0,   -- 0 = 장치 무관
    event_type_id       INT         NOT NULL,
    event_count         INTEGER     NOT NULL,
    error_count         INTEGER     NOT NULL,
    duration_p50_ms     INTEGER,
    duration_p95_ms     INTEGER,
    distinct_sessions   INTEGER,
    PRIMARY KEY (bucket, kiosk_id, event_type_id, peripheral_model_id)
);
```

**`(occurred_at, kiosk_id, source_seq)`를 PK로 잡은 이유** — 키오스크는 네트워크가 끊기면 로컬에 쌓았다가 재전송한다. 재전송 시 중복은 반드시 발생하므로 수집 API는 멱등이어야 하고, 그 멱등성의 근거가 이 복합키다. `INSERT ... ON CONFLICT DO NOTHING` 한 줄로 중복이 무비용 처리된다.

**파티셔닝** — `occurred_at` 월 단위 RANGE. `pg_partman`으로 자동 생성·삭제(보존 13개월), 3개월 지난 파티션은 압축하거나 콜드 스토리지로 분리. 규모가 커지면 TimescaleDB hypertable + continuous aggregate로 전환하되, 위 스키마는 그 전환에 변경이 필요 없도록 설계되어 있다.

### 1.3 수집 경로

```
키오스크 로컬 에이전트
  ├─ 이벤트를 SQLite 버퍼에 append        (오프라인 내성)
  ├─ N초 / M건 단위 배치 POST  ──────────► POST /v1/ingest/events
  └─ 2xx 수신 시에만 로컬 커밋 포인터 전진

Ingest API (FastAPI)
  ├─ mTLS 또는 디바이스 토큰으로 키오스크 인증
  ├─ Pydantic 스키마 검증 → 미등록 event_type은 quarantine 테이블로 격리
  ├─ COPY / execute_values 벌크 INSERT ... ON CONFLICT DO NOTHING
  └─ 부하가 커지면 앞단에 Redis Stream 또는 Kafka 삽입 (API 계약은 그대로)
```

주변장치 인벤토리는 별도 경로를 쓴다. 키오스크가 부팅 시 `PUT /v1/kiosks/{serial}/peripherals` 로 **현재 장착 상태 전체**를 보고하면, 서버가 기존 활성 레코드와 diff 하여 사라진 장치는 `detached_at`을 채우고 새 장치는 `peripheral` row를 생성한다. 즉 장착 이력이 별도 조작 없이 자동으로 쌓인다.

### 1.4 운영 시스템 (Ops)

| 영역 | 내용 |
|---|---|
| 조회 API | 플릿 현황, 키오스크 상세, 장치 모델별 고장률, 이벤트 검색 |
| 관리 API | 키오스크 등록/폐기, 주변장치 카탈로그 CRUD, 이벤트 사전 관리 |
| 알림 | 룰 기반 (예: 동일 장치에서 30분 내 error 5회) → Slack / 메일 |
| 대시보드 | 지점별 가동률, 장치 모델별 MTBF, 세션 이탈 지점 |
| 인증 | 운영자 OIDC + RBAC (viewer / operator / admin) |

---

## 2. 과제 2 — 분석 Agent (무슨 기능이 필요한가)

### 2.1 왜 2단계인가

이벤트 원본을 LLM에 그대로 던지는 설계는 비용과 정확도 양쪽에서 실패한다. 파이프라인을 둘로 나눈다.

```
[A] 후보 탐지 — 결정론적 (SQL + 통계)
    · 급증 탐지 : 이벤트 타입별 7일 이동평균 대비 z-score
    · 코호트 비교: 특정 peripheral_model / app_version 의 오류율이 전체 대비 유의하게 높은가
    · 퍼널 이탈 : session_id 기준 단계별 이탈률, 이탈 직전 이벤트
    · 반복 실패 : 동일 세션 내 재시도 N회 이상 패턴
    · 지연     : duration_p95 상위 화면·동작
         ↓  후보 20~50건 (각각 근거 수치 포함)
[B] 해석 — LLM (Claude)
    · 후보들을 묶어 "원인 가설 → 기능 제안"으로 번역
    · 중복 제거, 영향 범위·빈도 기준 우선순위 부여
    · 기존 제안과의 중복 / 후속 여부 판정
         ↓
    FeatureProposal (구조화 출력)
```

[A]가 없으면 [B]는 환각한다. [B]가 없으면 [A]는 "오류율이 올랐다"까지만 말하고 "그래서 무엇을 만들어야 하는가"를 말하지 못한다.

### 2.2 산출물 스키마

```python
class Evidence(BaseModel):
    metric: str  # "printer.paper_jam rate"
    query_id: str  # 재현 가능한 저장 쿼리 ID
    observed: float
    baseline: float
    sample_size: int
    window: str  # "2026-09-01/2026-09-15"
    affected_kiosks: int


class FeatureProposal(BaseModel):
    title: str
    problem: str  # 관측된 문제
    hypothesis: str  # 원인 가설
    proposal: str  # 제안하는 기능/변경
    scope: Literal["kiosk_app", "ops_backend", "device_driver", "config_only"]
    acceptance_criteria: list[str]  # 개발 Agent가 그대로 테스트로 옮길 수 있는 형태
    evidence: list[Evidence]
    impact_score: float  # 영향 키오스크 수 × 빈도 × 심각도
    effort_estimate: Literal["S", "M", "L"]
    confidence: float
    risks: list[str]
```

`acceptance_criteria`가 이 스키마의 핵심이다. 여기가 부실하면 과제 3의 개발 Agent가 만들 것이 없다. **분석 Agent의 성공 기준은 "좋은 분석"이 아니라 "개발 Agent가 즉시 착수 가능한 명세"** 라는 점을 평가 지표에 그대로 반영한다.

### 2.3 저장과 승인

```sql
CREATE TYPE proposal_status AS ENUM
    ('draft','pending_review','approved','rejected','superseded','implemented');

CREATE TABLE analysis_run (
    analysis_run_id  BIGSERIAL PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    window_start     TIMESTAMPTZ NOT NULL,
    window_end       TIMESTAMPTZ NOT NULL,
    detector_version TEXT NOT NULL,
    model            TEXT NOT NULL,
    token_usage      JSONB,
    status           TEXT NOT NULL
);

CREATE TABLE feature_proposal (
    proposal_id     BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    analysis_run_id BIGINT NOT NULL REFERENCES analysis_run(analysis_run_id),
    title           TEXT NOT NULL,
    body            JSONB NOT NULL,        -- FeatureProposal 전문
    scope           TEXT NOT NULL,
    impact_score    NUMERIC(10,2) NOT NULL,
    status          proposal_status NOT NULL DEFAULT 'draft',
    dedupe_key      TEXT,                  -- 동일 문제 재제안 억제
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    review_note     TEXT
);
CREATE INDEX ON feature_proposal (status, impact_score DESC);
CREATE UNIQUE INDEX ON feature_proposal (dedupe_key)
    WHERE status IN ('pending_review','approved');
```

실행 주기는 일 1회 야간 배치 + 임계 이벤트 발생 시 온디맨드. `analysis_run`에 분석 윈도우와 detector 버전을 남겨 **같은 결론을 언제든 재현**할 수 있게 한다.

---

## 3. 과제 3 — 개발 Agent

### 3.1 입력과 출력

- 입력: `status = 'approved'` 인 `feature_proposal` 1건
- 출력: 대상 리포지토리의 브랜치 + 테스트를 포함한 커밋 + PR + `dev_run` 레코드

### 3.2 실행 구조

```
dev-agent (Claude Agent SDK)
  1. 격리 워크스페이스   git worktree add /work/proposal-{id}
  2. 컨텍스트 로딩       proposal.body + 대상 모듈 소스 + 코딩 컨벤션(CLAUDE.md)
  3. 계획 수립          변경할 파일 목록과 테스트 전략을 먼저 산출 → 가드레일 사전 검증
  4. 구현 루프          편집 → 테스트 실행 → 실패 시 수정 (최대 N회)
  5. 자기 검증          acceptance_criteria 각 항목 ↔ 테스트 매핑표 작성
  6. PR 생성            제안 근거·증거 수치·테스트 결과를 본문에 포함
```

### 3.3 가드레일 — 이 구간 설계의 본체

| 가드레일 | 내용 |
|---|---|
| 경로 화이트리스트 | `proposal.scope` 별로 수정 허용 디렉터리를 고정. 그 밖의 파일을 건드리면 즉시 실패 |
| 변경 규모 상한 | 파일 20개 / 800줄 초과 시 자동 중단 → 사람에게 분할 요청 |
| 테스트 필수 | 신규 테스트 없는 PR은 CI에서 차단 |
| 금지 영역 | 파괴적 마이그레이션(DROP, ALTER TYPE), 결제·인증 모듈, 시크릿 파일 |
| 도구 제한 | 외부 네트워크 쓰기 차단, 패키지 추가는 허용 목록 내에서만 |
| 예산 | run 당 토큰·시간 상한. 초과 시 중단하고 부분 결과를 보고 |
| 사람 승인 | 머지 권한은 Agent에게 주지 않는다 |

```sql
CREATE TABLE dev_run (
    dev_run_id     BIGSERIAL PRIMARY KEY,
    proposal_id    BIGINT NOT NULL REFERENCES feature_proposal(proposal_id),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    branch         TEXT,
    pr_url         TEXT,
    status         TEXT NOT NULL,      -- running / pr_opened / failed / abandoned
    files_changed  INT,
    lines_changed  INT,
    test_summary   JSONB,
    token_usage    JSONB,
    failure_reason TEXT
);
```

---

## 4. 과제 4 — CI/CD (Agent 연결)

### 4.1 트리거 체인

```
feature_proposal.status → 'approved'
        │  (LISTEN/NOTIFY 또는 orchestrator 폴링)
        ▼
  dev-agent 작업 큐 enqueue
        │
        ▼
  PR 생성 ──► CI 파이프라인
                ├─ lint / type check (ruff, mypy)
                ├─ 단위·통합 테스트 (testcontainers PostgreSQL)
                ├─ 마이그레이션 검사 (up/down 왕복, 파괴적 변경 탐지)
                ├─ acceptance_criteria ↔ 테스트 매핑 검증
                └─ 의존성·시크릿 보안 스캔
        │
        ▼ (사람 리뷰 + 머지)
  스테이징 배포 → 스모크 테스트
        │
        ▼
  카나리 배포: 플릿의 1% → 10% → 50% → 100%
        │       각 단계마다 배포 후 지표 감시 (4.2)
        ▼
  deployment 레코드 기록 → 루프 닫힘
```

### 4.2 루프를 닫는 부분 — 배포 후 자동 검증

이 프로젝트가 단순 자동화와 갈리는 지점이다. 배포한 변경이 **원래 해결하려던 지표를 실제로 개선했는지**를 같은 데이터 레이어로 측정한다.

```sql
CREATE TABLE deployment (
    deployment_id  BIGSERIAL PRIMARY KEY,
    proposal_id    BIGINT REFERENCES feature_proposal(proposal_id),
    dev_run_id     BIGINT REFERENCES dev_run(dev_run_id),
    version        TEXT NOT NULL,
    stage          TEXT NOT NULL,      -- canary_1 / canary_10 / canary_50 / full
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    kiosk_count    INT,
    baseline       JSONB,              -- 배포 전 대상 지표값
    observed       JSONB,              -- 배포 후 관측값
    verdict        TEXT,               -- improved / neutral / regressed
    rolled_back_at TIMESTAMPTZ
);
```

- 카나리 그룹과 대조군의 대상 지표를 N시간 비교한다.
- `regressed` 판정 시 자동 롤백하고, 해당 제안을 `superseded`로 되돌린 뒤 회귀 근거를 분석 Agent에 재투입한다.
- `improved` 가 확인된 뒤에만 다음 카나리 단계로 넘어간다.

### 4.3 키오스크 배포 방식 — 선행 전제조건

키오스크 앱은 **원자적 교체 + 부팅 실패 시 자동 롤백** 구조여야 한다 (A/B 슬롯 또는 컨테이너 이미지 태그 전환 + 워치독). 키오스크는 현장 접근 비용이 매우 높아서, 되돌아갈 수단이 없는 상태로 자동 배포를 붙이면 장애 한 번에 전 지점이 멈춘다. **이 요구사항은 과제 1~3보다 먼저 확인되어야 한다.**

---

## 5. 리포지토리 구조

```
HANAAI/
├── docs/
│   ├── architecture.md          ← 이 문서
│   └── adr/                     아키텍처 결정 기록
├── db/
│   ├── migrations/              Alembic
│   └── seeds/                   event_type 사전, 장치 카탈로그 초기값
├── services/
│   ├── ingest/                  수집 API
│   ├── ops_api/                 운영·조회 API
│   └── common/                  ORM 모델, 설정, DB 세션
├── agents/
│   ├── contracts/               FeatureProposal 등 공유 스키마
│   ├── detectors/               [A] 결정론적 후보 탐지 (SQL + 통계)
│   ├── analyst/                 [B] 분석 Agent
│   └── developer/               개발 Agent
├── orchestrator/                상태 전이 → 작업 디스패치
├── .github/workflows/           CI/CD
└── tests/
```

`agents/contracts/`를 독립 패키지로 두는 것이 중요하다. 분석 Agent와 개발 Agent가 공유하는 유일한 계약이고, 여기가 흔들리면 두 Agent가 조용히 어긋난 채로 돌아간다.

---

## 6. 단계별 로드맵

| 단계 | 산출물 | 완료 기준 |
|---|---|---|
| **M1** 데이터 기반 | 스키마 + 마이그레이션 + 수집 API | 키오스크 10대 데이터가 24시간 무손실 적재, 재전송 시 중복 0 |
| **M2** 운영 시스템 | 조회·관리 API + 대시보드 + 알림 | 운영자가 DB 콘솔 없이 장치 모델별 고장률 확인 가능 |
| **M3** 탐지기 | `detectors/` 결정론적 룰 | 과거 데이터 재생 시 이미 알려진 장애를 탐지해냄 |
| **M4** 분석 Agent | 제안 생성 + 승인 UI | 사람이 "타당한 지적"이라 판단한 제안 비율 ≥ 60% |
| **M5** 개발 Agent | PR 자동 생성 | 승인 제안 중 CI 통과 PR 생성률 ≥ 50%, 가드레일 위반 0건 |
| **M6** CI/CD | 카나리 + 자동 롤백 | 롤백 훈련 성공, 배포 후 지표 검증이 자동 기록됨 |

M1~M3는 M4 없이도 단독으로 가치가 있다. **M4 이후를 먼저 만들고 싶은 유혹을 막기 위해 순서를 고정한다.**

---

## 7. 리스크 / 열린 질문

| 항목 | 내용 | 필요한 결정 |
|---|---|---|
| 데이터 규모 | 키오스크 대수 × 일 이벤트 수에 따라 PostgreSQL 단독 / TimescaleDB / 별도 OLAP로 갈린다 | **대수와 예상 일 이벤트 건수** |
| 개인정보 | 이벤트 payload에 고객 정보가 섞이면 보존기간·마스킹 규정이 붙는다 | 수집 항목 화이트리스트 확정 |
| 고객사 격리 | `customer` 계층은 넣었으나, A고객사 운영자가 B고객사 플릿을 못 보게 하는 인증·인가는 미구현 | 운영자 인증 방식(OIDC 연동 대상) |
| 대상 리포 | 개발 Agent가 수정할 키오스크 앱 코드베이스의 위치·언어·테스트 상태 | 기존 코드베이스 존재 여부 |
| 배포 인프라 | 키오스크 원격 업데이트 채널 유무 (4.3의 전제조건) | 현행 업데이트 방식 |
| 이벤트 사전 | 키오스크 앱이 현재 남기는 로그의 포맷과 항목 | 현행 로그 샘플 |
