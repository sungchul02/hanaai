"""고객사별 조정값 (DB 불필요).

임계값이 코드 스무 곳에 흩어져 있었다. 기관마다 맞는 값이 다르다 —
질문이 하루 수십 건인 곳과 수천 건인 곳이 같은 기준을 쓸 이유가 없다.
"""

from __future__ import annotations

import pytest

from agents.analyst.tuning import BY_KEY, DEFAULT_TUNING, KNOBS, Tuning, groups
from services.common.models import CmsMenu
from services.kiosk_api.service import find_answer


def test_등록부와_값이_어긋나지_않는다() -> None:
    """Knob 은 있는데 Tuning 에 자리가 없으면 화면에서 바꿔도 아무 일이 없다.
    반대면 코드가 쓰는 값을 관리자가 볼 수 없다. 양쪽이 같아야 한다."""
    assert {k.key for k in KNOBS} == set(DEFAULT_TUNING.as_dict())


def test_기본값이_등록부와_같다() -> None:
    """등록부에 적힌 기본값과 실제 기본값이 다르면 화면이 거짓말을 한다."""
    current = DEFAULT_TUNING.as_dict()
    for knob in KNOBS:
        assert current[knob.key] == knob.default, knob.key


def test_기본값이_허용_범위_안에_있다() -> None:
    for knob in KNOBS:
        assert knob.minimum <= knob.default <= knob.maximum, knob.key


def test_모든_항목에_설명이_있다() -> None:
    """숫자만 보여주면 관리자가 무엇을 만지는지 모른 채 건드린다."""
    for knob in KNOBS:
        assert knob.what and knob.higher and knob.lower, knob.key
        assert knob.higher != knob.lower, knob.key


def test_묶음이_모든_항목을_담는다() -> None:
    listed = {k.key for bucket in groups() for k in bucket["knobs"]}
    assert listed == set(BY_KEY)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"strong_match": 0.8}, 0.8),
        ({"strong_match": 99}, DEFAULT_TUNING.strong_match),      # 범위 밖은 버린다
        ({"strong_match": "글자"}, DEFAULT_TUNING.strong_match),   # 숫자가 아니면 버린다
        ({"없는항목": 1}, DEFAULT_TUNING.strong_match),             # 모르는 항목은 버린다
        (None, DEFAULT_TUNING.strong_match),
    ],
)
def test_이상한_값으로_분석이_멈추지_않는다(
    overrides: dict[str, object] | None, expected: float
) -> None:
    """등록부가 바뀌어 없어진 항목이 DB 에 남아 있을 수 있고,
    손으로 넣은 값이 범위를 벗어날 수도 있다. 그걸로 분석이 멈추면 안 된다."""
    assert Tuning.from_overrides(overrides).strong_match == expected


def test_정수_항목은_정수로_들어온다() -> None:
    assert Tuning.from_overrides({"max_revisions": 3.0}).max_revisions == 3
    assert isinstance(Tuning.from_overrides({"max_revisions": 3.0}).max_revisions, int)


def test_값을_바꾸면_판정이_달라진다() -> None:
    """설정이 실제로 동작을 바꾸는지. 저장만 되고 안 쓰이면 의미가 없다."""
    menu = CmsMenu(
        menu_id=1, customer_id=1, code="c", status="published",
        title="주차 요금", body="최초 2시간 무료이며 30분당 500원입니다.",
        keywords=["주차요금", "주차비"],
    )
    question = "주차장 요금이랑 자리 있나요"

    _m, _s, strict = find_answer(question, [menu], Tuning(strong_match=0.9))
    _m, _s, loose = find_answer(question, [menu], Tuning(strong_match=0.35))
    assert strict == "low_confidence"
    assert loose == "cms_menu"
