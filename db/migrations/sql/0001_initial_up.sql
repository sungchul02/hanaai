-- 0001 initial — AI 키오스크 CMS 콘텐츠 추천 시스템
--
-- 흐름:
--   사용자가 키오스크에 질문  →  question_log 에 쌓임
--   분석이 쓸모없는 질문을 거르고, 비슷한 질문을 묶고(question_cluster), 빈도를 센다
--   기존 cms_menu 로 답할 수 없는 주제면 AI 가 content_proposal 을 만든다
--   관리자가 [추가/수정/무시] 중 고르고, 추가하면 cms_menu 에 반영된다
--
-- 이 파일이 스키마의 단일 원본이다. services/common/models.py 는 이 DDL 을 따라간다.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TYPE kiosk_status AS ENUM ('provisioning', 'active', 'maintenance', 'retired');

-- 질문 1차 판정. 분석이 채우고, 관리자가 뒤집을 수 있다.
CREATE TYPE question_verdict AS ENUM ('pending', 'useful', 'irrelevant', 'abusive', 'too_short');

-- 그 질문에 무엇이 답했는가. 'none' 이 쌓이는 주제가 곧 새 메뉴 후보다.
CREATE TYPE answer_source AS ENUM ('cms_menu', 'fallback', 'none');

CREATE TYPE menu_status AS ENUM ('draft', 'published', 'archived');

-- 추천 콘텐츠의 상태. 문서의 [추가하기][수정][무시] 가 그대로 대응된다.
CREATE TYPE proposal_status AS ENUM
    ('pending_review', 'approved', 'edited', 'ignored', 'superseded');

-- ---------------------------------------------------------------- 고객사 / 지점 / 키오스크

CREATE TABLE customer (
    customer_id BIGSERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE site (
    site_id     BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customer (customer_id),
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 지점 코드는 고객사 안에서만 유일하다. 두 고객사가 같은 코드를 쓰는 일은 흔하다.
    UNIQUE (customer_id, code)
);

CREATE TABLE kiosk (
    kiosk_id     BIGSERIAL PRIMARY KEY,
    serial_no    TEXT UNIQUE NOT NULL,
    site_id      BIGINT NOT NULL REFERENCES site (site_id),
    name         TEXT NOT NULL,
    status       kiosk_status NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX kiosk_site_idx ON kiosk (site_id);

CREATE TRIGGER kiosk_set_updated_at
    BEFORE UPDATE ON kiosk
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------- CMS 콘텐츠

-- 지금 키오스크가 답할 수 있는 안내 콘텐츠. 분석의 '이미 있는 것' 기준이 된다.
CREATE TABLE cms_menu (
    menu_id     BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customer (customer_id),
    code        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    -- 질문 매칭에 쓰는 키워드. 답변 로직과 커버리지 판정이 둘 다 이걸 본다.
    keywords    TEXT[] NOT NULL DEFAULT '{}',
    status      menu_status NOT NULL DEFAULT 'published',
    -- 사람이 만든 것인지 AI 제안에서 온 것인지. 성과 측정의 근거가 된다.
    origin      TEXT NOT NULL DEFAULT 'manual',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, code),
    CONSTRAINT cms_menu_origin_ck CHECK (origin IN ('manual', 'ai_proposal'))
);
CREATE INDEX cms_menu_customer_idx ON cms_menu (customer_id, status);
CREATE INDEX cms_menu_keywords_idx ON cms_menu USING GIN (keywords);

CREATE TRIGGER cms_menu_set_updated_at
    BEFORE UPDATE ON cms_menu
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------- 질문 로그

-- 이 시스템의 원재료. 사용자가 키오스크에 던진 질문과 그때 나간 답.
CREATE TABLE question_log (
    question_id     BIGSERIAL PRIMARY KEY,
    asked_at        TIMESTAMPTZ NOT NULL,
    kiosk_id        BIGINT NOT NULL REFERENCES kiosk (kiosk_id),
    session_id      UUID,
    question_text   TEXT NOT NULL,
    -- 공백·조사·문장부호를 정리한 비교용 문자열. 묶기(clustering)가 이걸 본다.
    normalized_text TEXT NOT NULL,
    answer_text     TEXT,
    answer_source   answer_source NOT NULL DEFAULT 'none',
    matched_menu_id BIGINT REFERENCES cms_menu (menu_id),
    response_ms     INTEGER,
    input_mode      TEXT NOT NULL DEFAULT 'touch',
    verdict         question_verdict NOT NULL DEFAULT 'pending',
    verdict_reason  TEXT,
    cluster_id      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT question_log_input_ck CHECK (input_mode IN ('touch', 'voice')),
    CONSTRAINT question_log_text_ck  CHECK (length(question_text) BETWEEN 1 AND 500)
);
-- 분석은 항상 기간으로 자르므로 시간 인덱스가 첫 번째다.
CREATE INDEX question_log_time_idx     ON question_log (asked_at DESC);
CREATE INDEX question_log_kiosk_idx    ON question_log (kiosk_id, asked_at DESC);
CREATE INDEX question_log_verdict_idx  ON question_log (verdict, asked_at DESC);
-- 답하지 못한 질문만 뽑는 조회가 가장 잦다
CREATE INDEX question_log_unanswered_idx ON question_log (asked_at DESC)
    WHERE answer_source <> 'cms_menu';

-- ---------------------------------------------------------------- 분석 산출물

CREATE TABLE analysis_run (
    analysis_run_id BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    customer_id     BIGINT REFERENCES customer (customer_id),
    analyzer_version TEXT NOT NULL,
    model           TEXT NOT NULL,
    questions_seen  INTEGER,
    clusters_found  INTEGER,
    proposals_made  INTEGER,
    token_usage     JSONB,
    status          TEXT NOT NULL DEFAULT 'running',
    error           TEXT,
    CONSTRAINT analysis_run_window_ck CHECK (window_end > window_start)
);
CREATE INDEX analysis_run_started_idx ON analysis_run (started_at DESC);

-- 비슷한 질문을 묶은 주제. '주차장 어디예요' 와 '차 어디에 대요' 가 한 덩어리가 된다.
CREATE TABLE question_cluster (
    cluster_id      BIGSERIAL PRIMARY KEY,
    analysis_run_id BIGINT NOT NULL REFERENCES analysis_run (analysis_run_id),
    customer_id     BIGINT NOT NULL REFERENCES customer (customer_id),
    label           TEXT NOT NULL,
    size            INTEGER NOT NULL,
    unanswered      INTEGER NOT NULL DEFAULT 0,
    -- 이미 이 주제를 답할 수 있는 메뉴가 있으면 여기 채워진다. 있으면 제안하지 않는다.
    covered_menu_id BIGINT REFERENCES cms_menu (menu_id),
    coverage_score  NUMERIC(5, 4),
    keywords        TEXT[] NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX question_cluster_run_idx ON question_cluster (analysis_run_id, size DESC);

CREATE TABLE question_cluster_member (
    cluster_id  BIGINT NOT NULL REFERENCES question_cluster (cluster_id) ON DELETE CASCADE,
    question_id BIGINT NOT NULL REFERENCES question_log (question_id) ON DELETE CASCADE,
    similarity  NUMERIC(5, 4),
    PRIMARY KEY (cluster_id, question_id)
);

-- ---------------------------------------------------------------- 추천 콘텐츠

-- AI 가 만든 새 FAQ/메뉴 초안. 관리자 승인 전까지는 키오스크에 나가지 않는다.
CREATE TABLE content_proposal (
    proposal_id     BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    analysis_run_id BIGINT NOT NULL REFERENCES analysis_run (analysis_run_id),
    cluster_id      BIGINT REFERENCES question_cluster (cluster_id),
    customer_id     BIGINT NOT NULL REFERENCES customer (customer_id),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    reason          TEXT NOT NULL,
    keywords        TEXT[] NOT NULL DEFAULT '{}',
    sample_questions TEXT[] NOT NULL DEFAULT '{}',
    question_count  INTEGER NOT NULL,
    impact_score    NUMERIC(10, 2) NOT NULL,
    confidence      NUMERIC(3, 2) NOT NULL,
    contract        JSONB NOT NULL,
    status          proposal_status NOT NULL DEFAULT 'pending_review',
    -- 같은 주제를 매번 다시 제안하지 않게 막는다
    dedupe_key      TEXT,
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    review_note     TEXT,
    applied_menu_id BIGINT REFERENCES cms_menu (menu_id)
);
CREATE INDEX content_proposal_queue_idx ON content_proposal (status, impact_score DESC);
CREATE UNIQUE INDEX content_proposal_dedupe_uq ON content_proposal (customer_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending_review', 'approved', 'edited');

-- 질문이 어느 묶음에 들어갔는지 되짚을 수 있게 FK 를 뒤늦게 건다
ALTER TABLE question_log
    ADD CONSTRAINT question_log_cluster_fk
    FOREIGN KEY (cluster_id) REFERENCES question_cluster (cluster_id) ON DELETE SET NULL;
