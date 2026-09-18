"""묶기와 커버리지 판정 테스트 (DB 불필요)."""

from __future__ import annotations

from agents.analyst import textutil
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


def test_부서명이_달라도_같은_주제로_묶인다() -> None:
    """첫날 103건 중 부서 위치 질문 13건이 전부 1건짜리로 흩어졌다.
    부서명이 서로 안 겹쳐서다. 주차 다음으로 많이 물어본 주제인데
    표본 부족으로 통째로 빠졌고, 근거 문서에 층별 배치가 다 있는데도 답이 안 나왔다."""
    items = [
        QuestionItem(i, text, textutil.normalize(text), False, None)
        for i, text in enumerate([
            "세정과 몇 층이에요?", "축산과 몇 층인가요", "회계과 몇 층인가요",
            "행정지원과 몇 층이에요", "청년정책과 어디예요",
        ], 1)
    ]
    clusters = build_clusters(items)
    assert len(clusters) == 1, [c.label for c in clusters]
    assert clusters[0].size == 5


def test_같은_실_로_끝나도_다른_주제는_안_묶인다() -> None:
    """'실' 은 부서 접미사로 쓰지 않는다. 화장실 · 민원실 · 수유실이 전부 걸려서
    서로 다른 주제가 뭉친다. 그 셋은 지금 제대로 갈려 있고, 합치면 더 나빠진다."""
    items = [
        QuestionItem(i, text, textutil.normalize(text), False, None)
        for i, text in enumerate([
            "화장실 어디예요?", "화장실 어디 있나요",
            "민원실 어디예요?", "민원실 몇 시까지 해요?",
            "수유실 어디 있어요?", "수유실 몇 층이에요",
        ], 1)
    ]
    clusters = build_clusters(items)
    assert len(clusters) == 3, [c.label for c in clusters]
