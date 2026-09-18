-- 0003 되돌리기.
-- 새 분류의 이벤트 타입이 남아 있으면 제약을 되돌릴 수 없으므로 먼저 지운다.
DELETE FROM event_type
WHERE category IN ('voice', 'knowledge', 'inference', 'accessibility');

ALTER TABLE event_type DROP CONSTRAINT event_type_category_ck;

ALTER TABLE event_type ADD CONSTRAINT event_type_category_ck
    CHECK (category IN ('hardware', 'ux', 'transaction', 'system'));
