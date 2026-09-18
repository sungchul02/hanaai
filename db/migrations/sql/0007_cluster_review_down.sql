DROP INDEX IF EXISTS question_cluster_review_idx;
ALTER TABLE question_cluster
    DROP COLUMN IF EXISTS category,
    DROP COLUMN IF EXISTS review_status,
    DROP COLUMN IF EXISTS reviewed_by,
    DROP COLUMN IF EXISTS reviewed_at;
DROP TYPE IF EXISTS cluster_review_status;
