-- 0001 initial — 되돌리기
DROP TABLE IF EXISTS event_rollup_hourly;
DROP TABLE IF EXISTS event_quarantine;
DROP TABLE IF EXISTS event;              -- 파티션도 함께 삭제된다
DROP FUNCTION IF EXISTS ensure_event_partition(DATE);
DROP TABLE IF EXISTS event_type;
DROP TABLE IF EXISTS peripheral;
DROP TABLE IF EXISTS peripheral_model;
DROP TABLE IF EXISTS kiosk;
DROP TABLE IF EXISTS site;
DROP TABLE IF EXISTS customer;
DROP TYPE IF EXISTS peripheral_kind;
DROP TYPE IF EXISTS kiosk_status;
DROP FUNCTION IF EXISTS set_updated_at();
