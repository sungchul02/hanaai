"""텍스트 처리 단위 테스트.

여기 테스트 대부분은 실제로 겪은 오분류에서 나왔다. 묶기가 틀리면 엉뚱한 메뉴가 제안되고,
관리자는 왜 그런 추천이 떴는지 알 수 없다. 그래서 유사도 규칙을 못박아 둔다.
"""

from __future__ import annotations

from agents.analyst import textutil as tu


def test_조사와_어미를_떼어낸다() -> None:
    assert tu.normalize("주차장 어디예요?") == tu.normalize("주차장 어디에요")


def test_문장부호는_무시한다() -> None:
    assert tu.normalize("화장실 어디?!") == tu.normalize("화장실 어디")


def test_말투는_내용어에서_빠진다() -> None:
    """'위치 알려주세요' 는 주제를 가르지 못한다."""
    assert tu.content_tokens("화장실 위치 알려주세요") == {"화장실"}
    assert tu.content_tokens("주차장 위치 알려주세요") == {"주차장"}


def test_서술어로_끝나는_낱말은_주제어가_아니다() -> None:
    """'있어요' 가 낱말로 남으면 수유실과 엘리베이터가 한 묶음이 된다. 실제로 그랬다."""
    assert "있어요" not in tu.content_tokens("수유실 있어요?")
    assert tu.content_tokens("수유실 있어요?") == {"수유실"}


def test_말투가_같아도_주제가_다르면_안_붙는다() -> None:
    assert tu.similarity("화장실 위치 알려주세요", "주차장 위치 알려주세요") < 0.2


def test_같은_주제는_표현이_달라도_붙는다() -> None:
    assert tu.similarity("주차 가능한가요?", "주차비 얼마예요?") >= 0.4
    assert tu.similarity("화장실 어디예요?", "화장실 위치 알려주세요") >= 0.9


def test_전혀_다른_주제는_떨어진다() -> None:
    assert tu.similarity("수유실 어디예요?", "엘리베이터 어디 있어요?") < 0.2


def test_키워드는_자주_나온_내용어를_뽑는다() -> None:
    found = tu.keywords(["주차장 어디예요?", "주차 가능한가요?", "주차비 얼마예요?"])
    assert "주차" in found or "주차장" in found
    assert "어디" not in found


def test_욕설_판정() -> None:
    assert tu.is_abusive("바보야")
    assert not tu.is_abusive("주차장 어디예요?")
