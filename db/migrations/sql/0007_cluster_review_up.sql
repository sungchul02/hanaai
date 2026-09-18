-- 분류 결과에 대한 관리자 승인 관문.
--
-- 전에는 [분석 실행] 버튼 하나가 분류 · 근거조사 · 답변생성을 한 번에 했다.
-- 관리자는 다 끝난 뒤에야 결과를 봤고, 원하지 않는 주제에도 생성 비용이 나갔다.
--
-- 이제 두 단계로 나눈다. 1단계는 싸다(분류만). 2단계는 비싸다(근거조사 + 생성).
-- 비싼 일을 하기 전에 사람이 무엇을 할지 정한다.

CREATE TYPE cluster_review_status AS ENUM ('pending_review', 'approved', 'rejected', 'answered');

ALTER TABLE question_cluster
    ADD COLUMN category      TEXT,
    ADD COLUMN review_status cluster_review_status,
    ADD COLUMN reviewed_by   TEXT,
    ADD COLUMN reviewed_at   TIMESTAMPTZ;

CREATE INDEX question_cluster_review_idx
    ON question_cluster (customer_id, review_status)
    WHERE review_status IS NOT NULL;

COMMENT ON COLUMN question_cluster.category IS
    'LLM 이 붙인 분류. 관리자가 한 번에 훑어보고 판단할 수 있게 묶는 단위';
COMMENT ON COLUMN question_cluster.review_status IS
    '관리자 승인 상태. NULL 은 분류 대상이 아니었음(이미 답하고 있거나 표본 부족)';
