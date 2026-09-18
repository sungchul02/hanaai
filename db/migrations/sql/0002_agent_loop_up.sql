-- 0002 agent loop — 분석/개발 Agent, 배포 (과제 2·3·4)
-- 이 테이블들이 곧 파이프라인의 상태다. Agent 는 서로를 직접 호출하지 않고
-- 여기의 status 전이를 통해서만 연결된다. (설계 문서 0. 원칙 1)

CREATE TYPE proposal_status AS ENUM
    ('draft', 'pending_review', 'approved', 'rejected', 'superseded', 'implemented');

CREATE TYPE run_status AS ENUM
    ('running', 'succeeded', 'failed', 'abandoned');

-- ---------------------------------------------------------------- 분석

CREATE TABLE analysis_run (
    analysis_run_id  BIGSERIAL PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    window_start     TIMESTAMPTZ NOT NULL,
    window_end       TIMESTAMPTZ NOT NULL,
    detector_version TEXT NOT NULL,
    model            TEXT NOT NULL,
    candidate_count  INTEGER,
    token_usage      JSONB,
    status           run_status NOT NULL DEFAULT 'running',
    error            TEXT,
    CONSTRAINT analysis_run_window_ck CHECK (window_end > window_start)
);
CREATE INDEX analysis_run_started_idx ON analysis_run (started_at DESC);

CREATE TABLE feature_proposal (
    proposal_id     BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    analysis_run_id BIGINT NOT NULL REFERENCES analysis_run (analysis_run_id),
    title           TEXT NOT NULL,
    body            JSONB NOT NULL,              -- FeatureProposal 전문
    scope           TEXT NOT NULL,
    impact_score    NUMERIC(10, 2) NOT NULL,
    status          proposal_status NOT NULL DEFAULT 'draft',
    dedupe_key      TEXT,
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    review_note     TEXT,
    CONSTRAINT feature_proposal_scope_ck
        CHECK (scope IN ('kiosk_app', 'ops_backend', 'device_driver', 'config_only'))
);
CREATE INDEX feature_proposal_queue_idx ON feature_proposal (status, impact_score DESC);
-- 같은 문제를 매일 다시 제안하는 것을 막는다
CREATE UNIQUE INDEX feature_proposal_dedupe_uq ON feature_proposal (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending_review', 'approved');

-- ---------------------------------------------------------------- 개발

CREATE TABLE dev_run (
    dev_run_id     BIGSERIAL PRIMARY KEY,
    proposal_id    BIGINT NOT NULL REFERENCES feature_proposal (proposal_id),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    branch         TEXT,
    pr_url         TEXT,
    status         run_status NOT NULL DEFAULT 'running',
    files_changed  INTEGER,
    lines_changed  INTEGER,
    test_summary   JSONB,
    token_usage    JSONB,
    guardrail_hits JSONB NOT NULL DEFAULT '[]',  -- 어떤 가드레일에 걸렸는지
    failure_reason TEXT
);
CREATE INDEX dev_run_proposal_idx ON dev_run (proposal_id, started_at DESC);

-- ---------------------------------------------------------------- 배포

CREATE TABLE deployment (
    deployment_id  BIGSERIAL PRIMARY KEY,
    proposal_id    BIGINT REFERENCES feature_proposal (proposal_id),
    dev_run_id     BIGINT REFERENCES dev_run (dev_run_id),
    version        TEXT NOT NULL,
    stage          TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    kiosk_count    INTEGER,
    baseline       JSONB,       -- 배포 전 대상 지표값
    observed       JSONB,       -- 배포 후 관측값
    verdict        TEXT,        -- improved / neutral / regressed
    rolled_back_at TIMESTAMPTZ,
    CONSTRAINT deployment_stage_ck
        CHECK (stage IN ('staging', 'canary_1', 'canary_10', 'canary_50', 'full')),
    CONSTRAINT deployment_verdict_ck
        CHECK (verdict IS NULL OR verdict IN ('improved', 'neutral', 'regressed'))
);
CREATE INDEX deployment_proposal_idx ON deployment (proposal_id, started_at DESC);

-- 어떤 키오스크가 어떤 배포 단계에 속했는지 (카나리 그룹 / 대조군)
CREATE TABLE deployment_target (
    deployment_id BIGINT NOT NULL REFERENCES deployment (deployment_id) ON DELETE CASCADE,
    kiosk_id      BIGINT NOT NULL REFERENCES kiosk (kiosk_id),
    is_control    BOOLEAN NOT NULL DEFAULT FALSE,
    applied_at    TIMESTAMPTZ,
    PRIMARY KEY (deployment_id, kiosk_id)
);
