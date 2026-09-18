-- 0003 되돌리기
ALTER TABLE content_proposal
    DROP COLUMN IF EXISTS verified_coverage,
    DROP COLUMN IF EXISTS verified_matched,
    DROP COLUMN IF EXISTS verified_total,
    DROP COLUMN IF EXISTS revisions,
    DROP COLUMN IF EXISTS remaining_misses;
