"""질문 → 메뉴 매칭 테스트 (DB 불필요).

여기 있는 케이스는 대부분 실제로 잘못 답한 것들이다. 매칭이 틀리면 두 가지가 동시에 망가진다.
사용자가 틀린 답을 받고, 그게 '응답 완료' 로 기록되어 진짜 공백이 분석에서 묻힌다.
후자가 더 나쁘다. 공백을 찾는 것이 이 시스템의 존재 이유이기 때문이다.
"""

from __future__ import annotations

import pytest

from agents.analyst import textutil
from services.common.models import CmsMenu
from services.kiosk_api.service import find_answer

PARKING = CmsMenu(
    menu_id=1,
    customer_id=1,
    code="parking",
    title="주차 안내",
    body="주차장은 건물 지하 1층에 있습니다. 정문 오른쪽 진입로로 들어오시면 되고, "
    "최초 30분은 무료입니다.",
    keywords=["주차", "주차장", "주차비"],
    status="published",
)
RESTROOM = CmsMenu(
    menu_id=2,
    customer_id=1,
    code="restroom",
    title="화장실 안내",
    body="화장실은 각 층 엘리베이터 옆에 있습니다. 장애인 화장실은 1층과 3층에 있습니다.",
    keywords=["화장실"],
    status="published",
)
ELEVATOR = CmsMenu(
    menu_id=3,
    customer_id=1,
    code="elevator",
    title="엘리베이터 안내",
    body="엘리베이터는 로비 중앙과 서쪽 복도 끝에 있습니다.",
    keywords=["엘리베이터", "승강기"],
    status="published",
)
HOURS = CmsMenu(
    menu_id=4,
    customer_id=1,
    code="hours",
    title="운영시간 안내",
    body="평일 09:00 - 18:00, 토요일 09:00 - 13:00 운영합니다. 일요일은 휴무입니다.",
    keywords=["운영시간", "몇시", "영업시간"],
    status="published",
)
LOST = CmsMenu(
    menu_id=5,
    customer_id=1,
    code="lost",
    title="분실물 안내",
    body="분실물은 1층 안내데스크에서 보관합니다. 평일 09:00 - 18:00 에 방문해 주세요.",
    keywords=["분실물", "잃어버", "찾아주"],
    status="published",
)
MENUS = [PARKING, RESTROOM, ELEVATOR, HOURS, LOST]


def _ask(question: str) -> tuple[str | None, str]:
    menu, _score, verdict = find_answer(question, MENUS)
    return (menu.title if menu else None), verdict


# ------------------------------------------------------------------ 정상 매칭


def test_주제가_맞으면_답한다() -> None:
    assert _ask("주차장 어디예요?") == ("주차 안내", "cms_menu")
    assert _ask("화장실 어디예요?") == ("화장실 안내", "cms_menu")


def test_본문에_있는_내용도_답한다() -> None:
    """관리자가 키워드를 빠뜨려도 본문에 답이 있으면 답이 된 것이다."""
    assert _ask("장애인 화장실 있나요?") == ("화장실 안내", "cms_menu")
    assert _ask("토요일에도 하나요?") == ("운영시간 안내", "cms_menu")


def test_내용어가_없는_관용_질문은_키워드로_잡는다() -> None:
    """'몇 시까지 해요' 에는 주제어가 없다. 이때는 키워드가 유일한 신호다."""
    assert _ask("몇 시까지 해요?") == ("운영시간 안내", "cms_menu")


# ------------------------------------------------------------------ 오답 방지


def test_주제만_겹치고_묻는_것이_다르면_확신하지_않는다() -> None:
    """실제로 겪은 오답. '주차장' 이 들어 있다는 이유로 위치·요금 안내가 답했다.

    잔여 대수는 이 메뉴가 답할 수 없는 내용이고, 응답 완료로 기록되면
    '실시간 주차 대수' 라는 진짜 공백이 분석에서 영원히 묻힌다.
    """
    _menu, _score, verdict = find_answer("주차장 자리 얼마나 남았어", MENUS)
    assert verdict != "cms_menu"


def test_본문에_지나가듯_언급된_메뉴보다_주제가_맞는_메뉴가_먼저다() -> None:
    """화장실 안내 본문의 '엘리베이터 옆' 때문에 엘리베이터 질문에 화장실이 답했었다."""
    assert _ask("엘리베이터 어디 있어요?") == ("엘리베이터 안내", "cms_menu")
    assert _ask("승강기 어디예요?") == ("엘리베이터 안내", "cms_menu")


def test_없는_주제는_답하지_않는다() -> None:
    """이게 쌓여야 AI 가 새 메뉴를 추천한다. 억지로 답하면 공백이 사라진다."""
    for question in ("와이파이 비밀번호 뭐예요?", "수유실 어디예요?", "너 몇 살이야?"):
        menu, _score, verdict = find_answer(question, MENUS)
        assert verdict == "fallback", f"{question} 에 {menu.title if menu else None} 가 답했다"


def test_스치듯_걸린_것은_약한_매칭으로_남는다() -> None:
    """답은 보여주되 '응답 완료' 로 치지 않는다. 그래야 분석이 공백으로 센다."""
    _menu, _score, verdict = find_answer("지갑 잃어버렸어요", MENUS)
    assert verdict == "low_confidence"


def test_활용형이_달라도_어간이_같으면_찾는다() -> None:
    """'분실했는데' 와 '분실물'. 글자 겹침만 보면 0.07 이라 통째로 놓쳤다.

    한국어는 어간이 앞에 오므로 앞부분이 같으면 같은 말일 가능성이 높다.
    """
    menu, _score, verdict = find_answer("지갑 분실했는데 도와줘", MENUS)
    assert verdict != "fallback", "분실물 안내가 있는데 아무것도 못 찾았다"
    assert menu is not None and "분실물" in menu.title


def test_요청_표현은_주제어로_치지_않는다() -> None:
    """'도와줘' 가 낱말로 잡히면 평균이 내려가 멀쩡한 매칭까지 떨어뜨린다."""
    from agents.analyst.textutil import content_tokens

    assert content_tokens("지갑 분실했는데 도와줘") == {"지갑", "분실"}


def test_메뉴가_하나도_없으면_안내_불가() -> None:
    assert find_answer("주차장 어디예요?", [])[2] == "fallback"


# ------------------------------------------------------------------ 말투 키워드

def _menu(title: str, body: str, keywords: list[str]) -> CmsMenu:
    return CmsMenu(menu_id=99, customer_id=1, code="x", title=title, body=body, keywords=keywords)


def test_말투_키워드는_아무_질문이나_잡지_않는다() -> None:
    """실제로 당한 것. 무인발급기 안내의 키워드에 '어디' 가 있어서
    "화장실 어디야" 가 그 메뉴로 답해졌다. 키오스크 사용자는 엉뚱한 안내를 받고,
    화장실 메뉴가 없다는 진짜 공백은 '응답함' 으로 묻힌다."""
    menu = _menu(
        "무인민원발급기 안내",
        "천안시청에 무인민원발급기가 있습니다. 운영시간은 평일 09:00부터 18:00까지입니다.",
        ["무인발급기", "무인민원발급기", "어디", "몇 층"],
    )
    found, _score, verdict = find_answer("화장실 어디야", [menu])
    assert verdict == "fallback", "말투만 걸린 것은 답이 아니다"
    assert found is None

    # 진짜 주제어는 여전히 잡아야 한다
    _found, _score, verdict = find_answer("무인발급기 어디 있어요?", [menu])
    assert verdict == "cms_menu"


@pytest.mark.parametrize(
    ("word", "filler"),
    [
        ("어디", True),
        ("알려주세요", True),
        ("몇 층", True),  # 질문 형식이지 주제가 아니다
        ("차", True),  # 한 글자는 '자동차' '기차' 어디에나 걸린다
        ("주차", False),
        ("3층", False),  # 짧아도 층 안내에서는 주제어다
        ("24시간", False),
    ],
)
def test_말투와_주제어를_가른다(word: str, filler: bool) -> None:
    assert textutil.is_filler(word) is filler
