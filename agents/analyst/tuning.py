"""조정값 등록부.

임계값이 코드 스무 곳에 흩어져 있었다. 기관마다 맞는 값이 다르다 —
질문이 하루 수십 건인 곳과 수천 건인 곳이 같은 기준을 쓸 이유가 없다.
코드를 고치지 않고 화면에서 바꿀 수 있어야 한다.

여기가 유일한 원본이다. 각 항목은 **무엇을 바꾸는지와 올리면/내리면 어떻게 되는지**를
함께 들고 다닌다. 숫자만 나열하면 관리자가 무엇을 만지는지 모른 채 건드리게 된다.

값을 읽는 쪽은 `Tuning` 을 받아 쓴다. 안 넘기면 기본값이라 기존 코드가 그대로 돈다.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class Knob:
    """조정값 하나. 설명이 값보다 중요하다."""

    key: str
    label: str
    group: str
    default: float
    minimum: float
    maximum: float
    step: float
    #: 이 값이 무엇을 정하는가
    what: str
    #: 올리면 / 내리면 어떻게 되는가. 관리자가 판단할 근거다.
    higher: str
    lower: str
    integer: bool = False


KNOBS: tuple[Knob, ...] = (
    # ------------------------------------------------------------ 질문 묶기
    Knob(
        key="cluster_threshold",
        label="같은 주제로 묶는 기준",
        group="질문 묶기",
        default=0.42,
        minimum=0.2,
        maximum=0.8,
        step=0.01,
        what="두 질문이 이만큼 닮으면 한 주제로 묶는다.",
        higher="덜 묶인다. 주제가 잘게 쪼개지고 표본이 작아진다.",
        lower="더 묶인다. 다른 주제가 한 덩어리로 섞일 수 있다.",
    ),
    Knob(
        key="min_cluster_size",
        label="AI 에게 보낼 최소 질문 수",
        group="질문 묶기",
        default=1,
        minimum=1,
        maximum=20,
        step=1,
        integer=True,
        what="주제에 질문이 이만큼 모여야 AI 가 판단한다. 1 이면 전부 보낸다.",
        higher="자주 묻는 것만 본다. 비용이 줄지만 관리자가 볼 기회도 사라진다.",
        lower="전부 본다. 무엇이 가치 있는지는 사람이 정하게 된다.",
    ),
    Knob(
        key="coverage_threshold",
        label="기존 메뉴가 답한다고 볼 기준",
        group="질문 묶기",
        default=0.35,
        minimum=0.1,
        maximum=0.9,
        step=0.05,
        what="이미 있는 메뉴가 이만큼 맞으면 새 안내를 만들지 않는다.",
        higher="웬만해선 새로 만든다. 비슷한 메뉴가 늘어난다.",
        lower="기존 메뉴로 덮는다. 진짜 공백을 놓칠 수 있다.",
    ),
    # ------------------------------------------------------------ 답변 매칭
    Knob(
        key="strong_match",
        label="'안내 완료' 로 답할 기준",
        group="답변 매칭",
        default=0.6,
        minimum=0.3,
        maximum=0.95,
        step=0.05,
        what="질문이 묻는 것을 메뉴가 이만큼 덮으면 자신 있게 답한다.",
        higher="확실할 때만 답한다. '답변 불가' 가 늘지만 틀린 답은 준다.",
        lower="더 많이 답한다. 엉뚱한 답이 '안내 완료' 로 기록돼 공백이 묻힌다.",
    ),
    Knob(
        key="weak_match",
        label="'참고 안내' 로 보여줄 기준",
        group="답변 매칭",
        default=0.3,
        minimum=0.1,
        maximum=0.6,
        step=0.05,
        what="이만큼이라도 걸리면 '정확하지 않다' 고 알리며 관련 안내를 보여준다.",
        higher="어설픈 안내를 덜 보여준다. 대신 빈손으로 돌아가는 시민이 는다.",
        lower="관련될 법한 것을 더 보여준다. 엉뚱한 안내가 섞인다.",
    ),
    Knob(
        key="intent_bonus",
        label="묻는 항목이 맞을 때 주는 가산점",
        group="답변 매칭",
        default=0.2,
        minimum=0.0,
        maximum=0.5,
        step=0.05,
        what="질문이 요금을 묻는데 메뉴도 요금을 다루면 올리고, 어긋나면 내린다.",
        higher="항목이 맞는 메뉴를 강하게 우선한다.",
        lower="항목 구분이 약해진다. '주차 요금' 과 '주차장 규모' 가 섞인다.",
    ),
    # ------------------------------------------------------------ 근거 찾기
    Knob(
        key="evidence_min_score",
        label="근거로 인정할 최소 관련도",
        group="근거 찾기",
        default=0.18,
        minimum=0.05,
        maximum=0.6,
        step=0.02,
        what="문서 조각이 이만큼 관련돼야 근거 후보로 올린다.",
        higher="확실한 근거만 쓴다. '근거 문서 없음' 이 늘어난다.",
        lower="느슨하게 모은다. 상관없는 조각이 안내문에 섞일 수 있다.",
    ),
    Knob(
        key="evidence_limit",
        label="주제당 근거 후보 수",
        group="근거 찾기",
        default=5,
        minimum=1,
        maximum=20,
        step=1,
        integer=True,
        what="한 주제에 낱말로 걸린 조각을 몇 개까지 가져올지.",
        higher="놓치는 근거가 준다. 프롬프트가 길어져 비용이 는다.",
        lower="비용이 준다. 필요한 근거를 놓칠 수 있다.",
    ),
    # ------------------------------------------------------------ 초안 작성
    Knob(
        key="target_coverage",
        label="초안이 잡아야 할 질문 비율",
        group="초안 작성",
        default=0.8,
        minimum=0.3,
        maximum=1.0,
        step=0.05,
        what="초안이 대상 질문의 이만큼을 못 잡으면 AI 에게 다시 쓰게 한다.",
        higher="더 꼼꼼히 고친다. 수정 호출이 늘어 비용이 는다.",
        lower="한 번에 끝낸다. 못 잡는 질문이 남는다.",
    ),
    Knob(
        key="max_revisions",
        label="초안 수정 횟수 상한",
        group="초안 작성",
        default=2,
        minimum=0,
        maximum=5,
        step=1,
        integer=True,
        what="검증에 미달했을 때 AI 에게 다시 쓰게 할 최대 횟수.",
        higher="더 매달린다. 고쳐도 안 되는 주제에 비용만 쓴다.",
        lower="빨리 포기한다. 부실한 초안이 관리자에게 그대로 간다.",
    ),
    # ------------------------------------------------------------ 중복 판정
    Knob(
        key="duplicate_threshold",
        label="같은 메뉴로 볼 제목 유사도",
        group="중복 판정",
        default=0.7,
        minimum=0.3,
        maximum=1.0,
        step=0.05,
        what="승인할 때 제목이 이만큼 닮은 메뉴가 있으면 교체할지 물어본다.",
        higher="웬만해선 새 메뉴로 만든다. 비슷한 제목이 쌓인다.",
        lower="자주 묻는다. 다른 주제인데 교체하라고 할 수 있다.",
    ),
)

BY_KEY: dict[str, Knob] = {knob.key: knob for knob in KNOBS}


@dataclass(frozen=True)
class Tuning:
    """지금 쓸 조정값 한 벌. 기본값으로 만들면 코드에 적힌 값과 같다."""

    cluster_threshold: float = 0.42
    min_cluster_size: int = 1
    coverage_threshold: float = 0.35
    strong_match: float = 0.6
    weak_match: float = 0.3
    intent_bonus: float = 0.2
    evidence_min_score: float = 0.18
    evidence_limit: int = 5
    target_coverage: float = 0.8
    max_revisions: int = 2
    duplicate_threshold: float = 0.7

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any] | None) -> Tuning:
        """DB 에 저장된 값을 덮어씌운다. 모르는 항목과 범위 밖 값은 버린다.

        버리는 이유: 등록부가 바뀌어 없어진 항목이 DB 에 남아 있을 수 있고,
        손으로 넣은 값이 범위를 벗어날 수도 있다. 그걸로 분석이 멈추면 안 된다.
        """
        if not overrides:
            return cls()
        clean: dict[str, Any] = {}
        known = {f.name for f in fields(cls)}
        for key, value in overrides.items():
            knob = BY_KEY.get(key)
            if knob is None or key not in known:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not (knob.minimum <= number <= knob.maximum):
                continue
            clean[key] = int(number) if knob.integer else number
        return cls(**clean)

    def as_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: 아무것도 안 넘겼을 때 쓰는 값. 코드 곳곳의 기본값이 여기 하나로 모인다.
DEFAULT_TUNING = Tuning()


def groups() -> list[dict[str, Any]]:
    """화면에 뿌릴 묶음. 등록부 순서를 그대로 지킨다."""
    ordered: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for knob in KNOBS:
        bucket = index.get(knob.group)
        if bucket is None:
            bucket = {"group": knob.group, "knobs": []}
            index[knob.group] = bucket
            ordered.append(bucket)
        bucket["knobs"].append(knob)
    return ordered



def for_customer(session: Any, customer_id: int) -> Tuning:
    """고객사에 저장된 조정값. 없으면 기본값.

    분석 한 번마다 한 번만 읽는다. 매 질문마다 DB 를 때리면 답변이 느려진다.
    """
    from services.common.models import Customer

    row = session.get(Customer, customer_id)
    return Tuning.from_overrides(getattr(row, "tuning", None) if row else None)
