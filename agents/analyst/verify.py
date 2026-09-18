"""초안 검증 — 이 메뉴를 넣으면 그 질문들이 실제로 답이 되는가.

LLM 은 초안을 던지고 끝이라 자기가 쓴 메뉴가 쓸모 있는지 모른다. 실제로 그래서
"차 세울 데 있나요?" 102건이 주차 안내가 있는데도 계속 미응답으로 남았다.

여기서 하는 일은 단순하다. 초안을 진짜 매처에 넣고 그 질문들을 돌려본다.
추측이 아니라 실행이라 결과를 믿을 수 있고, LLM 없이 도는 순수 함수라 테스트도 쉽다.
이 함수가 콘텐츠 작성 Agent 가 쥐는 유일한 도구다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.analyst.tuning import DEFAULT_TUNING
from services.common.models import CmsMenu
from services.kiosk_api.service import find_answer

# 대상 질문의 이만큼은 잡아야 쓸 만한 메뉴로 본다.
TARGET_COVERAGE = DEFAULT_TUNING.target_coverage


@dataclass
class VerifyResult:
    """초안을 넣었을 때 각 질문이 어떻게 되는지."""

    total: int
    by_draft: int  # 이 초안이 답하게 되는 질문
    by_existing: int  # 기존 메뉴가 이미 답하던 질문
    misses: list[str] = field(default_factory=list)  # 초안을 넣어도 여전히 못 잡는 질문
    target: float = TARGET_COVERAGE  # 이만큼 잡으면 더 안 고친다

    @property
    def covered(self) -> int:
        return self.by_draft + self.by_existing

    @property
    def coverage(self) -> float:
        return self.covered / self.total if self.total else 0.0

    @property
    def ok(self) -> bool:
        return self.coverage >= self.target

    def summary(self) -> str:
        return (
            f"{self.total}건 중 {self.covered}건 응답 "
            f"(초안 {self.by_draft} · 기존 {self.by_existing}) "
            f"= {self.coverage * 100:.0f}퍼센트"
        )


def as_menu(
    title: str, body: str, keywords: list[str], customer_id: int = 0, menu_id: int = -1
) -> CmsMenu:
    """저장하지 않는 임시 메뉴. 매처에 넣어보기 위한 것이다."""
    return CmsMenu(
        menu_id=menu_id,
        customer_id=customer_id,
        code="__draft__",
        title=title,
        body=body,
        keywords=list(keywords),
        status="published",
    )


def verify_draft(
    draft: CmsMenu,
    questions: list[str],
    existing: list[CmsMenu],
    miss_limit: int = 12,
    target: float = TARGET_COVERAGE,
) -> VerifyResult:
    """초안을 기존 메뉴들과 함께 놓고 질문을 돌려본다.

    기존 메뉴와 나란히 두는 것이 중요하다. 초안만 놓고 재면 실제 상황과 다르다.
    운영에서는 다른 메뉴와 경쟁하며, 더 잘 맞는 메뉴에 질문을 빼앗길 수 있다.
    """
    menus = [*existing, draft]
    by_draft = 0
    by_existing = 0
    misses: list[str] = []

    for question in questions:
        menu, _score, verdict = find_answer(question, menus)
        if verdict != "cms_menu" or menu is None:
            if len(misses) < miss_limit:
                misses.append(question)
            continue
        if menu.menu_id == draft.menu_id:
            by_draft += 1
        else:
            by_existing += 1

    return VerifyResult(
        total=len(questions), by_draft=by_draft, by_existing=by_existing,
        misses=misses, target=target,
    )
