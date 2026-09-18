-- 0005 지식 저장소 (RAG)
--
-- 지금까지 AI 는 질문 묶음과 건수만 봤다. 건물에 대해 아는 게 없으니 안내 문구에
-- "(확인 후 입력 필요)" 를 남길 수밖에 없었고, 그 빈칸은 사람이 채워야 했다.
--
-- 근거 문서를 넣어두면 하위 Agent 가 그 안에서 답을 찾아온다.
-- 지어내는 것과 찾아오는 것의 차이가 여기서 갈린다.

CREATE TYPE document_kind AS ENUM ('web', 'manual', 'upload');

CREATE TABLE source_document (
    document_id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customer (customer_id),
    title       TEXT NOT NULL,
    -- 출처를 반드시 남긴다. 관리자가 "이 안내가 맞는지" 확인할 수 있어야 하고,
    -- 원문이 바뀌었을 때 다시 가져올 곳도 필요하다.
    url         TEXT,
    kind        document_kind NOT NULL DEFAULT 'web',
    note        TEXT,
    fetched_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, title)
);
CREATE INDEX source_document_customer_idx ON source_document (customer_id);

CREATE TRIGGER source_document_set_updated_at
    BEFORE UPDATE ON source_document
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 검색 단위. 문서를 통째로 LLM 에 넣으면 비용도 정확도도 나빠진다.
CREATE TABLE document_chunk (
    chunk_id    BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES source_document (document_id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    heading     TEXT,
    text        TEXT NOT NULL,
    -- 검색용 낱말. 임베딩 대신 낱말 기반으로 찾는다.
    -- 정확도가 필요해지면 여기에 vector 컬럼을 더하고 retriever 만 바꾸면 된다.
    keywords    TEXT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);
CREATE INDEX document_chunk_keywords_idx ON document_chunk USING GIN (keywords);

-- 제안이 어느 근거에서 나왔는지. 관리자가 출처를 눌러 확인할 수 있어야 한다.
CREATE TABLE proposal_evidence (
    proposal_id BIGINT NOT NULL REFERENCES content_proposal (proposal_id) ON DELETE CASCADE,
    chunk_id    BIGINT NOT NULL REFERENCES document_chunk (chunk_id) ON DELETE CASCADE,
    score       NUMERIC(5, 4),
    quote       TEXT,
    PRIMARY KEY (proposal_id, chunk_id)
);
