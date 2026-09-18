-- 0002 되돌리기. PostgreSQL 은 ENUM 값을 지울 수 없으므로 타입을 다시 만든다.
DROP INDEX IF EXISTS question_log_weak_idx;
ALTER TABLE question_log DROP COLUMN IF EXISTS match_score;

UPDATE question_log SET answer_source = 'fallback' WHERE answer_source = 'low_confidence';

ALTER TABLE question_log ALTER COLUMN answer_source DROP DEFAULT;
ALTER TABLE question_log
    ALTER COLUMN answer_source TYPE TEXT USING answer_source::text;
DROP TYPE answer_source;
CREATE TYPE answer_source AS ENUM ('cms_menu', 'fallback', 'none');
ALTER TABLE question_log
    ALTER COLUMN answer_source TYPE answer_source USING answer_source::answer_source;
ALTER TABLE question_log ALTER COLUMN answer_source SET DEFAULT 'none';
