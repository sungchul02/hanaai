-- 0004 이벤트에 앱 버전 차원 추가
--
-- 왜 필요한가:
-- "1.1.0 배포 후 응답이 느려졌다" 를 물으려면 이벤트가 '그때의' 앱 버전을 알아야 한다.
-- kiosk.app_version 은 현재 값이라, 배포 이후 과거 이벤트까지 새 버전으로 보이게 만든다.
-- 사실 테이블에 차원을 비정규화해서 붙이는 것이 표준적인 해법이다.
--
-- 이건 배포 후 지표 검증(설계 문서 4.2)의 전제이기도 하다.
-- 버전별로 가를 수 없으면 카나리가 좋아졌는지 나빠졌는지 판정할 수 없다.

ALTER TABLE event ADD COLUMN app_version TEXT;

-- 집계 테이블은 event 에서 언제든 다시 만들 수 있는 파생 데이터다.
-- PK 에 차원을 더해야 하므로 통째로 새로 만든다. scripts/rollup.py 로 재생성한다.
DROP TABLE event_rollup_hourly;

CREATE TABLE event_rollup_hourly (
    bucket              TIMESTAMPTZ NOT NULL,
    kiosk_id            BIGINT      NOT NULL,
    event_type_id       INT         NOT NULL,
    peripheral_model_id BIGINT      NOT NULL DEFAULT 0,
    app_version         TEXT        NOT NULL DEFAULT '',
    event_count         INTEGER     NOT NULL,
    error_count         INTEGER     NOT NULL,
    duration_p50_ms     INTEGER,
    duration_p95_ms     INTEGER,
    distinct_sessions   INTEGER,
    PRIMARY KEY (bucket, kiosk_id, event_type_id, peripheral_model_id, app_version)
);
CREATE INDEX event_rollup_bucket_idx  ON event_rollup_hourly (bucket DESC);
CREATE INDEX event_rollup_type_idx    ON event_rollup_hourly (event_type_id, bucket DESC);
CREATE INDEX event_rollup_version_idx ON event_rollup_hourly (app_version, bucket DESC);
