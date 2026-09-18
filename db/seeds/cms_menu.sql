-- 초기 CMS 콘텐츠.
--
-- 일부러 비워둔 주제가 있다. 주차 · 와이파이 · 수유실 은 메뉴가 없다.
-- 사용자가 그걸 물으면 키오스크가 답하지 못하고, 그 기록이 쌓여
-- AI 가 "주차 안내 메뉴를 추가하세요" 라고 제안하게 된다.
-- 문서 2페이지의 예시 상황을 그대로 재현한 것이다.

INSERT INTO cms_menu (customer_id, code, title, body, keywords, status, origin)
SELECT c.customer_id, v.code, v.title, v.body, v.keywords, 'published', 'manual'
FROM customer c
CROSS JOIN (VALUES
    ('restroom', '화장실 안내',
     '화장실은 각 층 엘리베이터 옆에 있습니다. 장애인 화장실은 1층과 3층에 있습니다.',
     ARRAY['화장실', '화장실어디', '해우소', '세면']),
    ('hours', '운영시간 안내',
     '평일 09:00 - 18:00, 토요일 09:00 - 13:00 운영합니다. 일요일과 공휴일은 휴무입니다.',
     ARRAY['운영시간', '몇시', '영업시간', '언제까지', '문여는']),
    ('elevator', '엘리베이터 안내',
     '엘리베이터는 로비 중앙과 서쪽 복도 끝에 있습니다. 화물용은 지하 1층에서 이용해 주세요.',
     ARRAY['엘리베이터', '승강기', '올라가는']),
    ('lost', '분실물 안내',
     '분실물은 1층 안내데스크에서 보관합니다. 평일 09:00 - 18:00 에 방문해 주세요.',
     ARRAY['분실물', '잃어버', '찾아주']),
    ('info_desk', '안내데스크 위치',
     '안내데스크는 1층 정문으로 들어와 오른쪽에 있습니다.',
     ARRAY['안내데스크', '안내소', '직원'])
) AS v(code, title, body, keywords)
WHERE c.code = 'BLDG-A'
ON CONFLICT (customer_id, code) DO UPDATE SET
    title = EXCLUDED.title, body = EXCLUDED.body, keywords = EXCLUDED.keywords;
