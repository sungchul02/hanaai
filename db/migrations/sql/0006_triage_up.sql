-- 상위 Agent 의 판단 기록.
--
-- 주제를 버렸다면 왜 버렸는지 남아야 한다. 남지 않으면 관리자는
-- "왜 이 질문에 대한 제안이 안 나왔지" 를 확인할 방법이 없고,
-- 분류가 잘못 걸러낸 것을 알아챌 수도 없다. 사람에게 보고하기 위한 자리다.

ALTER TABLE question_cluster
    ADD COLUMN triage_keep      BOOLEAN,
    ADD COLUMN triage_reason    TEXT,
    ADD COLUMN evidence_found   BOOLEAN,
    ADD COLUMN evidence_summary TEXT,
    ADD COLUMN evidence_missing TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN question_cluster.triage_keep IS
    'LLM 분류 결과. true 면 안내 가치 있음. NULL 은 분류 대상이 아니었음(이미 답하고 있거나 표본 부족)';
COMMENT ON COLUMN question_cluster.evidence_found IS
    '하위 Agent 가 근거 문서를 찾았는지. false 면 문서 보강이 필요하다는 신호';
COMMENT ON COLUMN question_cluster.evidence_missing IS
    '답하기 위해 더 필요한 정보. 관리자가 문서를 보강할 근거';
