"""'(확인 후 입력 필요)' 문장 정리.

메뉴를 항목별로 쪼개라고 시키면 모델은 자기 메뉴만 보고 "내 본문에 없으니 모른다" 고 쓴다.
프롬프트로 두 번 막아봤고 두 번 다 안 됐다(26건 중 25건). 여기서 확인한다.
"""

from __future__ import annotations

from agents.analyst.placeholder import PLACEHOLDER, clean_body

SIBLINGS = ["주차 요금", "주차장 규모", "청사 3층 부서 안내", "청사 4층 부서 안내"]


def test_옆_메뉴가_다루는_내용은_지운다() -> None:
    body = (
        "천안시청 주차장 운영시간은 평일 08시부터 18시까지입니다. "
        "주차 요금과 무료 주차 시간은 (확인 후 입력 필요)입니다."
    )
    cleaned, dropped = clean_body(body, SIBLINGS)
    assert len(dropped) == 1
    assert PLACEHOLDER not in cleaned
    assert "08시부터 18시까지" in cleaned, "사실은 그대로 남아야 한다"


def test_다른_메뉴를_가리키는_문장은_지운다() -> None:
    """'나머지 층은' 처럼 스스로 '여기 말고 저기' 라고 밝히는 문장."""
    body = (
        "천안시청 2층에는 미래전략과, 일자리경제과가 있습니다. "
        "2층을 제외한 나머지 층의 배치도는 (확인 후 입력 필요)입니다."
    )
    cleaned, dropped = clean_body(body, SIBLINGS)
    assert len(dropped) == 1
    assert "미래전략과" in cleaned


def test_진짜_빈칸은_남긴다() -> None:
    """어느 메뉴도 다루지 않는 것은 관리자가 채워야 할 숙제다. 지우면 그 사실이 사라진다."""
    body = (
        "여권은 전국 모든 여권사무 대행기관에서 신청할 수 있습니다. "
        "천안시청 여권 창구의 층과 위치는 (확인 후 입력 필요)입니다."
    )
    cleaned, dropped = clean_body(body, SIBLINGS)
    assert dropped == []
    assert PLACEHOLDER in cleaned


def test_빈칸이_없으면_손대지_않는다() -> None:
    body = "천안시청 주차 요금은 최초 2시간 무료입니다."
    assert clean_body(body, SIBLINGS) == (body, [])


def test_전부_지워질_상황이면_되돌린다() -> None:
    """빈 안내문보다 군더더기가 낫다."""
    body = "주차 요금은 (확인 후 입력 필요)입니다."
    cleaned, dropped = clean_body(body, SIBLINGS)
    assert cleaned == body and dropped == []


def test_혼자인_메뉴는_교차_참조를_지우지_않는다() -> None:
    """가리킬 옆 메뉴가 없으면 그 문장은 진짜 빈칸이다."""
    body = (
        "천안시청 2층에는 미래전략과가 있습니다. "
        "나머지 층의 배치도는 (확인 후 입력 필요)입니다."
    )
    cleaned, dropped = clean_body(body, [], has_siblings=False)
    assert dropped == [] and PLACEHOLDER in cleaned
