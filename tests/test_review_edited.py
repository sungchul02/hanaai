"""AI 초안을 그대로 썼는가, 관리자가 손봤는가 (DB 불필요).

이 둘의 비율이 초안 품질 지표다. 손보는 비율이 높아지면 프롬프트를 고쳐야 한다.

전에는 'title/body/keywords 가 요청에 왔는가' 로 판단했다. 그런데 화면은
입력칸 값을 항상 보내므로 전부 edited 가 됐다. 26건 승인에 approved 가 0건이었고,
지표가 통째로 무의미했다. 값이 왔는지가 아니라 값이 달라졌는지를 봐야 한다.
"""

from __future__ import annotations

from services.common.models import ContentProposal
from services.ops_api.routers.review import _differs


def _draft() -> ContentProposal:
    """판정 함수만 보므로 DB 에 넣지 않는다."""
    return ContentProposal(
        title="주차 요금",
        body="최초 2시간 무료이며, 이후 30분당 500원입니다.",
        keywords=["주차요금", "주차비"],
    )


def test_그대로_쓰면_수정이_아니다() -> None:
    draft = _draft()
    assert _differs(draft, draft.title, draft.body, list(draft.keywords)) is False


def test_앞뒤_공백과_줄바꿈은_수정이_아니다() -> None:
    """화면이 값을 보낼 때 섞이는 것이지 관리자가 고친 것이 아니다."""
    draft = _draft()
    assert _differs(draft, f"  {draft.title} ", draft.body + "\n", list(draft.keywords)) is False


def test_키워드_순서만_바뀐_것은_수정이_아니다() -> None:
    """매칭 결과가 같다."""
    draft = _draft()
    assert _differs(draft, draft.title, draft.body, list(reversed(draft.keywords))) is False


def test_본문을_고치면_수정이다() -> None:
    draft = _draft()
    assert _differs(draft, draft.title, draft.body + " 카드결제만 됩니다.", list(draft.keywords))


def test_제목을_고치면_수정이다() -> None:
    draft = _draft()
    assert _differs(draft, "주차 요금 안내", draft.body, list(draft.keywords))


def test_키워드를_더하면_수정이다() -> None:
    """키워드 보강이 승인 과정에서 가장 잦은 수정이라 반드시 잡아야 한다."""
    draft = _draft()
    assert _differs(draft, draft.title, draft.body, [*draft.keywords, "주차 무료"])
