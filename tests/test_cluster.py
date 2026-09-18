"""묶기와 커버리지 판정 테스트 (DB 불필요)."""

from __future__ import annotations

from agents.analyst.cluster import (
    Cluster,
    MenuRef,
    QuestionItem,
    build_clusters,
    find_covering_menu,
)


def _items(*rows: tuple[str, bool, int | None]) -> list[QuestionItem]:
    return [
        QuestionItem(
            question_id=i + 1,
            text=text,
            normalized=text,
            answered=answered,
            matched_menu_id=menu_id,
        )
        for i, (text, answered, menu_id) in enumerate(rows)
    ]


def test_같은_주제는_한_묶음이_된다() -> None:
    clusters = build_clusters(
        _items(
            ("주차장 어디예요?", False, None),
            ("주차 가능한가요?", False, None),
            ("주차비 얼마예요?", False, None),
        )
    )
    assert len(clusters) == 1
    assert clusters[0].size == 3


def test_말투만_같은_다른_주제는_갈라진다() -> None:
    clusters = build_clusters(
        _items(
            ("화장실 위치 알려주세요", True, 1),
            ("주차장 위치 알려주세요", False, None),
        )
    )
    assert len(clusters) == 2


def test_큰_묶음이_먼저_온다() -> None:
    clusters = build_clusters(
        _items(
            ("수유실 어디예요?", False, None),
            ("주차장 어디예요?", False, None),
            ("주차 가능한가요?", False, None),
            ("주차비 얼마예요?", False, None),
        )
    )
    assert clusters[0].size == 3


def test_미응답_수를_센다() -> None:
    cluster = build_clusters(
        _items(
            ("주차장 어디예요?", False, None),
            ("주차 가능한가요?", True, 7),
        )
    )[0]
    assert cluster.size == 2
    assert cluster.unanswered == 1


def test_대표_질문은_중복_표현을_뺀다() -> None:
    cluster = Cluster(
        label="주차장 어디예요?",
        items=_items(
            ("주차장 어디예요?", False, None),
            ("주차장 어디예요?", False, None),
            ("주차비 얼마예요?", False, None),
        ),
    )
    assert len(cluster.samples()) == 2


def test_실제로_답한_메뉴가_커버리지의_정답이다() -> None:
    """키워드 유사도로 '추정' 했더니 실제 응답 로직과 어긋났다. 기록이 있으면 그게 답이다."""
    cluster = build_clusters(
        _items(
            ("장애인 화장실 있나요?", True, 42),
            ("화장실 어디예요?", True, 42),
        )
    )[0]
    menus = [MenuRef(menu_id=42, title="화장실 안내", keywords=["화장실"], body="...")]
    menu, score = find_covering_menu(cluster, menus)
    assert menu is not None and menu.menu_id == 42
    assert score >= 0.5


def test_답한_적_없으면_키워드로_판단한다() -> None:
    cluster = build_clusters(_items(("화장실 어디예요?", False, None)))[0]
    menus = [MenuRef(menu_id=42, title="화장실 안내", keywords=["화장실"], body="...")]
    menu, _ = find_covering_menu(cluster, menus)
    assert menu is not None


def test_겹치는_메뉴가_없으면_None() -> None:
    cluster = build_clusters(_items(("주차장 어디예요?", False, None)))[0]
    menus = [MenuRef(menu_id=42, title="화장실 안내", keywords=["화장실"], body="...")]
    menu, _ = find_covering_menu(cluster, menus)
    assert menu is None
