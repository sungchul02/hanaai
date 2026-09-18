-- 0001 initial — 데이터 레이어 (과제 1)
-- 이 파일이 스키마의 단일 원본(single source of truth)이다.
-- services/common/models.py 의 ORM 은 이 DDL 을 "따라가는" 쪽이며, 반대가 아니다.

-- ---------------------------------------------------------------- 공통

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TYPE kiosk_status    AS ENUM ('provisioning', 'active', 'maintenance', 'retired');
CREATE TYPE peripheral_kind AS ENUM ('printer', 'scanner', 'card_reader', 'cash_acceptor',
                                     'nfc', 'pinpad', 'camera', 'display', 'speaker', 'other');

-- ---------------------------------------------------------------- 고객사 / 설치 지점 / 키오스크

-- 우리는 키오스크를 만들어 파는 쪽이므로 플릿이 고객사 단위로 갈린다.
-- 이 계층이 없으면 "A은행 30대"와 "B프랜차이즈 200대"를 구분할 수 없고,
-- 나중에 붙이려면 이미 쌓인 데이터와 전체 조회 쿼리를 모두 고쳐야 한다.
CREATE TABLE customer (
    customer_id BIGSERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    contact     JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT customer_status_ck
        CHECK (status IN ('prospect', 'active', 'suspended', 'churned'))
);

CREATE TABLE site (
    site_id     BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customer (customer_id),
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    region      TEXT,
    timezone    TEXT NOT NULL DEFAULT 'Asia/Seoul',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 지점 코드는 고객사 안에서만 유일하다. 두 고객사가 똑같이 'GANGNAM-01' 을
    -- 쓰는 일은 흔하다. 전역 UNIQUE 로 두면 고객사가 늘어날 때 충돌한다.
    UNIQUE (customer_id, code)
);
CREATE INDEX site_customer_idx ON site (customer_id);

CREATE TABLE kiosk (
    kiosk_id     BIGSERIAL PRIMARY KEY,
    serial_no    TEXT UNIQUE NOT NULL,
    site_id      BIGINT NOT NULL REFERENCES site (site_id),
    model        TEXT NOT NULL,
    os_version   TEXT,
    app_version  TEXT,
    status       kiosk_status NOT NULL DEFAULT 'provisioning',
    installed_at DATE,
    last_seen_at TIMESTAMPTZ,
    meta         JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX kiosk_site_status_idx ON kiosk (site_id, status);
CREATE INDEX kiosk_last_seen_idx   ON kiosk (last_seen_at DESC NULLS LAST);

CREATE TRIGGER kiosk_set_updated_at
    BEFORE UPDATE ON kiosk
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------- 주변장치

-- 모델 단위 카탈로그. "이 모델이 전 지점에서 더 고장나는가"를 묻기 위해 개체와 분리한다.
CREATE TABLE peripheral_model (
    peripheral_model_id BIGSERIAL PRIMARY KEY,
    kind                peripheral_kind NOT NULL,
    vendor              TEXT NOT NULL,
    model_name          TEXT NOT NULL,
    driver_name         TEXT,
    driver_version      TEXT NOT NULL DEFAULT '',
    protocol            TEXT,
    capabilities        JSONB NOT NULL DEFAULT '{}',
    eol_date            DATE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vendor, model_name, driver_version)
);

-- 실제 장착된 개체. detached_at 으로 장착 이력이 남는다.
CREATE TABLE peripheral (
    peripheral_id       BIGSERIAL PRIMARY KEY,
    kiosk_id            BIGINT NOT NULL REFERENCES kiosk (kiosk_id),
    peripheral_model_id BIGINT NOT NULL REFERENCES peripheral_model (peripheral_model_id),
    slot                TEXT NOT NULL,
    serial_no           TEXT,
    firmware_version    TEXT,
    attached_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    detached_at         TIMESTAMPTZ,
    meta                JSONB NOT NULL DEFAULT '{}'
);
-- 한 슬롯에 현재 장착된 장치는 하나뿐
CREATE UNIQUE INDEX peripheral_active_slot_uq
    ON peripheral (kiosk_id, slot) WHERE detached_at IS NULL;
CREATE INDEX peripheral_model_idx ON peripheral (peripheral_model_id);
CREATE INDEX peripheral_kiosk_idx ON peripheral (kiosk_id);

-- ---------------------------------------------------------------- 이벤트

-- 통제된 어휘. 분석 Agent 가 자유 텍스트를 추측하지 않게 하는 장치.
CREATE TABLE event_type (
    event_type_id INT PRIMARY KEY,
    code          TEXT UNIQUE NOT NULL,
    category      TEXT NOT NULL,
    default_sev   SMALLINT NOT NULL,
    description   TEXT NOT NULL,
    is_actionable BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT event_type_category_ck
        CHECK (category IN ('hardware', 'ux', 'transaction', 'system')),
    CONSTRAINT event_type_sev_ck
        CHECK (default_sev IN (10, 20, 30, 40, 50))
);

-- append-only, occurred_at 월 단위 RANGE 파티션.
-- PK 에 (kiosk_id, source_seq) 를 포함시킨 것이 재전송 멱등성의 근거다.
CREATE TABLE event (
    occurred_at   TIMESTAMPTZ NOT NULL,
    kiosk_id      BIGINT      NOT NULL,
    source_seq    BIGINT      NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    peripheral_id BIGINT,
    event_type_id INT         NOT NULL,
    severity      SMALLINT    NOT NULL,
    session_id    UUID,
    duration_ms   INTEGER,
    error_code    TEXT,
    payload       JSONB       NOT NULL DEFAULT '{}',
    PRIMARY KEY (occurred_at, kiosk_id, source_seq)
) PARTITION BY RANGE (occurred_at);

-- 파티션 테이블에는 FK 를 걸 수 있으나 대량 적재 비용이 있어 event_type 만 건다.
-- kiosk_id / peripheral_id 정합성은 수집 계층에서 보장한다. (설계 문서 1.3)
ALTER TABLE event
    ADD CONSTRAINT event_event_type_fk
    FOREIGN KEY (event_type_id) REFERENCES event_type (event_type_id);

CREATE INDEX event_type_time_idx  ON event (event_type_id, occurred_at DESC);
CREATE INDEX event_kiosk_time_idx ON event (kiosk_id, occurred_at DESC);
CREATE INDEX event_periph_idx     ON event (peripheral_id, occurred_at DESC);
CREATE INDEX event_session_idx    ON event (session_id) WHERE session_id IS NOT NULL;
CREATE INDEX event_payload_idx    ON event USING GIN (payload jsonb_path_ops);

-- 월 파티션 생성 (멱등). scripts/ensure_partitions.py 가 주기적으로 호출한다.
CREATE OR REPLACE FUNCTION ensure_event_partition(p_month DATE)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_start DATE := date_trunc('month', p_month)::date;
    v_end   DATE := (date_trunc('month', p_month) + INTERVAL '1 month')::date;
    v_name  TEXT := 'event_' || to_char(v_start, 'YYYYMM');
BEGIN
    -- 여기서 format() 을 쓰지 않는 이유:
    -- psycopg3 은 보내는 SQL 문자열에서 퍼센트 기호를 자기 파라미터 플레이스홀더로
    -- 해석한다. 주석 안에 있어도 마찬가지다. format() 의 식별자/리터럴 지정자가
    -- 퍼센트로 시작하므로, 쓰는 순간 마이그레이션이 서버에 닿기도 전에 터진다.
    -- 그래서 quote_ident / quote_literal 로 직접 이어붙인다.
    IF to_regclass(v_name) IS NULL THEN
        EXECUTE 'CREATE TABLE ' || quote_ident(v_name)
             || ' PARTITION OF event FOR VALUES FROM ('
             || quote_literal(v_start) || ') TO (' || quote_literal(v_end) || ')';
    END IF;
    RETURN v_name;
END;
$$;

SELECT ensure_event_partition((CURRENT_DATE - INTERVAL '1 month')::date);
SELECT ensure_event_partition(CURRENT_DATE);
SELECT ensure_event_partition((CURRENT_DATE + INTERVAL '1 month')::date);
SELECT ensure_event_partition((CURRENT_DATE + INTERVAL '2 month')::date);

-- 스키마를 통과하지 못한 원본은 버리지 않고 격리한다. 미등록 event_type 발견 경로.
CREATE TABLE event_quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    kiosk_serial  TEXT,
    reason        TEXT NOT NULL,
    raw           JSONB NOT NULL
);
CREATE INDEX event_quarantine_time_idx ON event_quarantine (received_at DESC);

-- ---------------------------------------------------------------- 집계

-- 분석 Agent 의 실제 입력. peripheral_model_id = 0 은 "장치 무관" 을 뜻한다.
CREATE TABLE event_rollup_hourly (
    bucket              TIMESTAMPTZ NOT NULL,
    kiosk_id            BIGINT      NOT NULL,
    event_type_id       INT         NOT NULL,
    peripheral_model_id BIGINT      NOT NULL DEFAULT 0,
    event_count         INTEGER     NOT NULL,
    error_count         INTEGER     NOT NULL,
    duration_p50_ms     INTEGER,
    duration_p95_ms     INTEGER,
    distinct_sessions   INTEGER,
    PRIMARY KEY (bucket, kiosk_id, event_type_id, peripheral_model_id)
);
CREATE INDEX event_rollup_bucket_idx ON event_rollup_hourly (bucket DESC);
