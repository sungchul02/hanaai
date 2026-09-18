-- MAXIVIEW (대화형 AI 아바타 + 배리어프리 키오스크) 이벤트 사전
--
-- 설계 원칙: 모든 실패 이벤트에는 분모가 되는 '시도' 이벤트가 있어야 한다.
--   실패만 수집하면 "이 모델이 더 나쁜가"를 물을 수 없다. 비율의 분모가 없기 때문이다.
--   실제로 그래서 탐지기가 한 번도 발화하지 못했다. 아래 [분모] 표시가 그 짝이다.
--
-- id 대역: 5000 음성/대화 · 6000 RAG · 7000 추론/NPU/모델 · 8000 배리어프리
--   (1000 하드웨어 / 2000 UX / 3000 거래 / 4000 시스템 은 event_types.sql 에 있다)
--
-- payload 관례:
--   duration_ms 는 컬럼을 쓴다. payload 에 중복해서 넣지 않는다.
--   error_code 는 장치/SDK 가 준 원본 코드를 그대로 넣는다.
--   개인정보는 절대 넣지 않는다. 발화 원문, 얼굴 이미지, 결제 정보 금지.
--   대신 길이·신뢰도·언어코드 같은 파생값만 넣는다.

INSERT INTO event_type (event_type_id, code, category, default_sev, description, is_actionable)
VALUES
    -- ---------------------------------------------------------- 5000 음성 / 대화
    (5001, 'voice.session_started',    'voice', 20, '음성 상호작용 시작 [분모]',              FALSE),
    (5002, 'stt.recognized',           'voice', 20, '음성 인식 성공 [분모]',                  FALSE),
    (5003, 'stt.failed',               'voice', 40, '음성 인식 실패',                         TRUE),
    (5004, 'stt.timeout',              'voice', 30, '발화 대기 시간 초과',                    TRUE),
    (5005, 'stt.low_confidence',       'voice', 30, '인식 신뢰도 임계 미만',                  TRUE),
    (5006, 'stt.noise_rejected',       'voice', 30, '주변 소음으로 입력 거부',                TRUE),

    (5010, 'intent.resolved',          'voice', 20, '의도 파악 성공 [분모]',                  FALSE),
    (5011, 'intent.unresolved',        'voice', 30, '의도 파악 실패',                         TRUE),
    (5012, 'intent.clarify_requested', 'voice', 30, '아바타가 되물음',                        TRUE),
    (5013, 'intent.out_of_scope',      'voice', 30, '지원 범위 밖 요청',                      TRUE),

    (5020, 'tts.played',               'voice', 20, '음성 안내 재생 [분모]',                  FALSE),
    (5021, 'tts.failed',               'voice', 40, '음성 합성/재생 실패',                    TRUE),

    (5030, 'avatar.response_started',  'voice', 20, '아바타 응답 생성 시작 [분모]',           FALSE),
    (5031, 'avatar.response_completed','voice', 20, '아바타 응답 완료 (duration_ms = 지연)',  FALSE),
    (5032, 'avatar.response_failed',   'voice', 40, '아바타 응답 실패',                       TRUE),
    (5033, 'avatar.interrupted',       'voice', 30, '사용자가 응답 도중 이탈/중단',           TRUE),

    -- ---------------------------------------------------------- 6000 RAG / 지식
    (6001, 'rag.query',                'knowledge', 20, '지식 검색 시도 [분모]',              FALSE),
    (6002, 'rag.no_result',            'knowledge', 30, '검색 결과 없음',                     TRUE),
    (6003, 'rag.low_confidence',       'knowledge', 30, '근거 신뢰도 임계 미만',              TRUE),
    (6004, 'rag.source_stale',         'knowledge', 30, '오래된 문서를 근거로 사용',          TRUE),
    (6005, 'rag.fallback_generic',     'knowledge', 30, '근거 없이 일반 답변으로 대체',       TRUE),
    (6006, 'rag.index_missing',        'knowledge', 50, '해당 도메인 색인 자체가 없음',       TRUE),

    -- ---------------------------------------------------------- 7000 추론 / NPU / 모델
    (7001, 'inference.completed',      'inference', 20, '온디바이스 추론 완료 [분모]',        FALSE),
    (7002, 'inference.failed',         'inference', 40, '추론 실패',                          TRUE),
    (7003, 'inference.slow',           'inference', 30, '추론 지연 임계 초과',                TRUE),
    (7004, 'inference.fallback_cloud', 'inference', 30, '온디바이스 실패로 클라우드 대체',    TRUE),

    (7010, 'npu.throttled',            'inference', 30, '발열/전력으로 NPU 성능 제한',        TRUE),
    (7011, 'npu.temp_high',            'inference', 30, '온도 임계 초과',                     TRUE),
    (7012, 'npu.oom',                  'inference', 50, '메모리 부족으로 추론 중단',          TRUE),

    (7020, 'model.deploy_started',     'inference', 20, '모델 배포 시작 [분모]',              FALSE),
    (7021, 'model.deploy_completed',   'inference', 20, '모델 배포 완료',                     FALSE),
    (7022, 'model.deploy_failed',      'inference', 50, '모델 배포 실패',                     TRUE),
    (7023, 'model.rollback',           'inference', 50, '이전 모델로 자동 롤백',              TRUE),

    -- ---------------------------------------------------------- 8000 배리어프리
    -- 2026-01-28 부터 기존 설치 키오스크에도 전면 적용되는 법적 요건이라,
    -- '되는지' 가 아니라 '증명 가능한지' 가 중요하다. 그래서 성공도 전부 남긴다.
    (8001, 'access.approach_detected', 'accessibility', 20, 'IR 센서 접근 감지 [분모]',       FALSE),
    (8002, 'access.mode_entered',      'accessibility', 20, '접근성 모드 진입 [분모]',        FALSE),
    (8003, 'access.mode_completed',    'accessibility', 20, '접근성 모드로 업무 완료',        FALSE),
    (8004, 'access.mode_abandoned',    'accessibility', 30, '접근성 모드에서 이탈',           TRUE),

    (8010, 'access.height_adjust_started',   'accessibility', 20, '높이 조절 시작 [분모]',    FALSE),
    (8011, 'access.height_adjust_completed', 'accessibility', 20, '높이 조절 완료',           FALSE),
    (8012, 'access.height_adjust_failed',    'accessibility', 40, '높이 조절 실패 (모터/센서)', TRUE),

    (8020, 'access.voice_guide_started', 'accessibility', 20, '음성 안내 시작 [분모]',        FALSE),
    (8021, 'access.braille_input',       'accessibility', 20, '점자 키패드 입력',             FALSE),
    (8022, 'access.braille_no_response', 'accessibility', 30, '점자 키패드 무응답',           TRUE),
    (8023, 'access.sign_language_played','accessibility', 20, '수어 영상 재생 [분모]',        FALSE),
    (8024, 'access.sign_language_failed','accessibility', 40, '수어 영상 재생 실패',          TRUE)
ON CONFLICT (event_type_id) DO UPDATE SET
    code          = EXCLUDED.code,
    category      = EXCLUDED.category,
    default_sev   = EXCLUDED.default_sev,
    description   = EXCLUDED.description,
    is_actionable = EXCLUDED.is_actionable;
