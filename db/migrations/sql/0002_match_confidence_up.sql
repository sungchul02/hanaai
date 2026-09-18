-- 0002 매칭 확신도
--
-- 문제: 키워드 하나만 걸려도 '응답 완료' 로 기록됐다.
--   "주차장 자리 얼마나 남았어" → '주차장' 이 들어 있다는 이유로 주차 안내가 답했다.
--   위치와 요금을 알려주는 메뉴가 잔여 대수 질문에 답한 셈이다.
--
-- 두 가지가 동시에 망가진다.
--   1) 사용자가 틀린 답을 받는다
--   2) '실시간 주차 대수' 라는 진짜 공백이 응답 완료로 묻혀서 분석에 영원히 안 잡힌다
--      2번이 더 나쁘다. 이 시스템의 존재 이유가 공백을 찾는 것이기 때문이다.
--
-- 그래서 '약하게 맞은 것' 을 따로 표시한다. 답은 보여주되 응답 완료로 치지 않는다.
--
-- ALTER TYPE ... ADD VALUE 를 쓰지 않는다. 같은 트랜잭션 안에서 새 값을 쓸 수 없어서
-- (UnsafeNewEnumValueUsage) 마이그레이션이 통째로 실패한다. 타입을 새로 만드는 쪽이 안전하다.

-- 이 컬럼을 쓰는 부분 인덱스가 있으면 타입을 바꿀 수 없다. 내렸다가 다시 만든다.
DROP INDEX question_log_unanswered_idx;

ALTER TABLE question_log ALTER COLUMN answer_source DROP DEFAULT;
ALTER TYPE answer_source RENAME TO answer_source_old;

CREATE TYPE answer_source AS ENUM ('cms_menu', 'low_confidence', 'fallback', 'none');

ALTER TABLE question_log
    ALTER COLUMN answer_source TYPE answer_source
    USING answer_source::text::answer_source;
ALTER TABLE question_log ALTER COLUMN answer_source SET DEFAULT 'none';

DROP TYPE answer_source_old;

-- 답하지 못한 질문 조회용. low_confidence 도 '답하지 못한 것' 에 포함된다.
CREATE INDEX question_log_unanswered_idx ON question_log (asked_at DESC)
    WHERE answer_source <> 'cms_menu';

-- 얼마나 확신했는지 남긴다. 임계값을 조정할 때 과거 데이터로 검증하려면 필요하다.
ALTER TABLE question_log ADD COLUMN match_score NUMERIC(5, 4);

-- 약하게 맞은 질문만 뽑는 조회가 잦아진다
CREATE INDEX question_log_weak_idx ON question_log (asked_at DESC)
    WHERE answer_source = 'low_confidence';
