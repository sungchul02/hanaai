-- 0003 MAXIVIEW 이벤트 분류 확장
--
-- 최초 사전은 주문·결제 중심이었다. 일반 식음료 키오스크를 가정한 것인데,
-- 실제 대상인 MAXIVIEW 는 '대화형 AI 아바타 + 배리어프리' 가 본체다.
-- 프린터 고장보다 "아바타가 못 알아들었다", "NPU 가 느려 응답이 늦었다" 가
-- 훨씬 중요한 신호이므로 분류를 넓힌다.
--
-- 기존 4개는 그대로 두고 4개를 더한다. 이미 쌓인 데이터를 건드리지 않는다.

ALTER TABLE event_type DROP CONSTRAINT event_type_category_ck;

ALTER TABLE event_type ADD CONSTRAINT event_type_category_ck
    CHECK (category IN (
        'hardware',       -- 주변장치 (프린터, 카드리더, 센서)
        'ux',             -- 화면·세션 흐름
        'transaction',    -- 주문·결제
        'system',         -- 앱·네트워크·엣지 연결
        'voice',          -- 음성 인식, TTS, 아바타 대화
        'knowledge',      -- RAG 검색·근거
        'inference',      -- 온디바이스 추론, NPU, 모델 배포
        'accessibility'   -- 배리어프리 (높이조절, 점자, 수어, 음성안내)
    ));
