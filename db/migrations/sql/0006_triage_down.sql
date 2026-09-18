ALTER TABLE question_cluster
    DROP COLUMN IF EXISTS triage_keep,
    DROP COLUMN IF EXISTS triage_reason,
    DROP COLUMN IF EXISTS evidence_found,
    DROP COLUMN IF EXISTS evidence_summary,
    DROP COLUMN IF EXISTS evidence_missing;
