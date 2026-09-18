-- 0001 initial — 되돌리기
ALTER TABLE question_log DROP CONSTRAINT IF EXISTS question_log_cluster_fk;
DROP TABLE IF EXISTS content_proposal;
DROP TABLE IF EXISTS question_cluster_member;
DROP TABLE IF EXISTS question_cluster;
DROP TABLE IF EXISTS analysis_run;
DROP TABLE IF EXISTS question_log;
DROP TABLE IF EXISTS cms_menu;
DROP TABLE IF EXISTS kiosk;
DROP TABLE IF EXISTS site;
DROP TABLE IF EXISTS customer;
DROP TYPE IF EXISTS proposal_status;
DROP TYPE IF EXISTS menu_status;
DROP TYPE IF EXISTS answer_source;
DROP TYPE IF EXISTS question_verdict;
DROP TYPE IF EXISTS kiosk_status;
DROP FUNCTION IF EXISTS set_updated_at();
