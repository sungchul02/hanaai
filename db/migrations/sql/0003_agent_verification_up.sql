-- 0003 콘텐츠 작성 Agent 의 검증 결과
--
-- Agent 가 자기 초안을 실제 매처에 넣어보고 몇 건을 잡는지 확인한다.
-- 그 결과를 남겨야 관리자가 "이 메뉴를 넣으면 51건 중 49건이 답이 된다" 를 보고 판단할 수 있다.
-- 숫자 없이 "추천합니다" 만 있으면 승인 여부를 감으로 정하게 된다.

ALTER TABLE content_proposal
    ADD COLUMN verified_coverage NUMERIC(5, 4),   -- 대상 질문 중 답이 되는 비율
    ADD COLUMN verified_matched  INTEGER,          -- 그중 실제 건수
    ADD COLUMN verified_total    INTEGER,          -- 대상 질문 수
    ADD COLUMN revisions         INTEGER NOT NULL DEFAULT 0,  -- Agent 가 몇 번 고쳤나
    ADD COLUMN remaining_misses  TEXT[] NOT NULL DEFAULT '{}'; -- 고쳐도 못 잡은 질문

COMMENT ON COLUMN content_proposal.revisions IS
    '0 이면 단일 호출과 같다. 1 이상이면 Agent 가 검증 후 스스로 고친 것이다.';
