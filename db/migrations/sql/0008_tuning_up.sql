-- 고객사별 조정값.
--
-- 임계값이 코드 21곳에 흩어져 있었다. 기관마다 맞는 값이 다르다 —
-- 질문이 하루 수십 건인 곳과 수천 건인 곳이 같은 기준을 쓸 이유가 없다.
-- 코드를 고치지 않고 화면에서 바꿀 수 있어야 한다.
--
-- 값은 JSONB 하나에 모은다. 항목이 스무 개 남짓이고 함께 읽히므로
-- 테이블을 따로 두면 조인만 늘어난다. 무엇이 유효한 항목인지는
-- agents/analyst/tuning.py 의 등록부가 정한다 — DB 는 그걸 강제하지 않는다.

ALTER TABLE customer
    ADD COLUMN tuning JSONB NOT NULL DEFAULT '{}';

COMMENT ON COLUMN customer.tuning IS
    '고객사별 조정값. 비어 있으면 코드 기본값을 쓴다. 등록부는 agents/analyst/tuning.py';
