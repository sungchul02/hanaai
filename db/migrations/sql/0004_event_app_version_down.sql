-- 0004 되돌리기. 집계는 파생 데이터라 재생성하면 된다.
DROP TABLE event_rollup_hourly;

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

ALTER TABLE event DROP COLUMN app_version;
