-- 이벤트 사전 초기값.
-- id 대역: 1000 하드웨어 / 2000 UX / 3000 거래 / 4000 시스템
-- 여기 없는 code 로 들어온 이벤트는 event_quarantine 으로 격리된다.
-- 새 이벤트를 추가할 때는 키오스크 앱 배포보다 이 사전을 먼저 반영해야 한다.

INSERT INTO event_type (event_type_id, code, category, default_sev, description, is_actionable)
VALUES
    -- 하드웨어
    (1001, 'printer.paper_jam',        'hardware',    40, '프린터 용지 걸림',                TRUE),
    (1002, 'printer.paper_out',        'hardware',    30, '프린터 용지 소진',                TRUE),
    (1003, 'printer.print_failed',     'hardware',    40, '인쇄 실패',                       TRUE),
    (1010, 'card_reader.read_failed',  'hardware',    40, '카드 인식 실패',                  TRUE),
    (1011, 'card_reader.timeout',      'hardware',    30, '카드 투입 대기 시간 초과',        TRUE),
    (1020, 'scanner.read_failed',      'hardware',    40, '바코드/QR 인식 실패',             TRUE),
    (1030, 'cash_acceptor.jam',        'hardware',    40, '지폐 걸림',                       TRUE),
    (1031, 'cash_acceptor.reject',     'hardware',    30, '지폐 거부',                       TRUE),
    (1040, 'peripheral.disconnected',  'hardware',    50, '주변장치 연결 끊김',              TRUE),
    (1041, 'peripheral.reconnected',   'hardware',    20, '주변장치 재연결',                 FALSE),

    -- UX
    (2001, 'session.started',          'ux',          20, '고객 세션 시작',                  FALSE),
    (2002, 'session.completed',        'ux',          20, '고객 세션 정상 종료',             FALSE),
    (2003, 'session.abandoned',        'ux',          30, '고객이 중간에 이탈',              TRUE),
    (2004, 'session.timeout',          'ux',          30, '무입력으로 세션 종료',            TRUE),
    (2010, 'screen.entered',           'ux',          10, '화면 진입',                       FALSE),
    (2011, 'screen.back',              'ux',          20, '이전 화면으로 되돌아감',          TRUE),
    (2020, 'input.retry',              'ux',          30, '동일 입력 재시도',                TRUE),
    (2021, 'input.validation_failed',  'ux',          30, '입력값 검증 실패',                TRUE),

    -- 거래
    (3001, 'order.created',            'transaction', 20, '주문 생성',                       FALSE),
    (3002, 'order.cancelled',          'transaction', 30, '주문 취소',                       TRUE),
    (3010, 'payment.requested',        'transaction', 20, '결제 요청',                       FALSE),
    (3011, 'payment.approved',         'transaction', 20, '결제 승인',                       FALSE),
    (3012, 'payment.declined',         'transaction', 30, '결제 거절',                       TRUE),
    (3013, 'payment.failed',           'transaction', 40, '결제 실패(시스템 오류)',          TRUE),
    (3014, 'payment.timeout',          'transaction', 40, '결제 응답 시간 초과',             TRUE),

    -- 시스템
    (4001, 'app.started',              'system',      20, '앱 기동',                         FALSE),
    (4002, 'app.crashed',              'system',      50, '앱 비정상 종료',                  TRUE),
    (4003, 'app.updated',              'system',      20, '앱 버전 변경',                    FALSE),
    (4010, 'network.offline',          'system',      40, '네트워크 단절',                   TRUE),
    (4011, 'network.online',           'system',      20, '네트워크 복구',                   FALSE),
    (4020, 'sync.backlog_flushed',     'system',      20, '오프라인 버퍼 재전송 완료',       FALSE),
    (4030, 'api.call_failed',          'system',      40, '서버 API 호출 실패',              TRUE)
ON CONFLICT (event_type_id) DO UPDATE SET
    code          = EXCLUDED.code,
    category      = EXCLUDED.category,
    default_sev   = EXCLUDED.default_sev,
    description   = EXCLUDED.description,
    is_actionable = EXCLUDED.is_actionable;
