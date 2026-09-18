"""분석 Agent 와 CMS 사이의 계약.

AI 가 내놓는 '추천 콘텐츠' 의 형태를 여기서 못박는다. 이 스키마를 통과하지 못한 응답은
저장하지 않는다. 승인 한 번이면 그대로 키오스크에 나가는 내용이라, 형태가 헐거우면
관리자가 매번 손봐야 하고 그러면 시스템의 존재 이유가 없어진다.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0"


class ClusterEvidence(BaseModel):
    """제안의 근거. 관리자가 "왜 이걸 추천했지" 를 바로 납득할 수 있어야 한다."""

    model_config = ConfigDict(extra="forbid")

    question_count: int = Field(ge=1, description="이 주제로 묶인 질문 수")
    unanswered_count: int = Field(ge=0, description="그중 기존 메뉴가 답하지 못한 수")
    window: str = Field(description="예: 2026-09-11/2026-09-18")
    sample_questions: list[str] = Field(min_length=1, max_length=8)
    existing_menu: str | None = Field(
        default=None, description="비슷한 기존 메뉴가 있으면 제목. 없으면 null"
    )


class ContentProposal(BaseModel):
    """AI 가 만든 새 FAQ/메뉴 초안.

    title/body 는 승인 즉시 cms_menu 로 들어간다. 그래서 '초안' 이 아니라
    '그대로 써도 되는 문장' 이어야 한다.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    title: str = Field(min_length=2, max_length=40, description="키오스크에 표시될 메뉴명")
    body: str = Field(
        min_length=10,
        max_length=500,
        description="안내 문구. 사용자가 그대로 읽고 이해할 수 있는 완성된 문장",
    )
    reason: str = Field(min_length=5, description="왜 이 메뉴가 필요한지, 관리자에게 하는 설명")
    keywords: list[str] = Field(
        min_length=1, max_length=10, description="이 메뉴를 찾게 할 검색 키워드"
    )
    source_labels: list[str] = Field(
        min_length=1,
        max_length=6,
        description=(
            "이 추천이 나온 주제의 label. 입력의 label 을 글자 그대로 옮긴다. "
            "여러 주제를 하나로 합쳤으면 모두 적는다."
        ),
    )
    evidence: ClusterEvidence
    impact_score: float = Field(ge=0, description="질문 수와 미응답 비율로 매긴 우선순위")

    @field_validator("keywords")
    @classmethod
    def _drop_filler_keywords(cls, values: list[str]) -> list[str]:
        """말투를 키워드에서 뺀다. '어디', '알려주세요' 같은 것들.

        모델이 "사용자가 쓸 법한 말" 을 넣으라는 지시를 말투까지로 넓게 받는다.
        실제로 무인민원발급기 안내에 '어디' 가 들어갔고, 그 메뉴가
        "화장실 어디야" 에 답해버렸다. 아무 질문이나 걸리는 그물이 된다.

        전부 말투였다면 원래 값을 남긴다. 여기서 비우면 계약(최소 1개)이 깨져서
        제안 자체가 버려지는데, 매칭 쪽에서 어차피 다시 거르므로 그럴 이유가 없다.
        """
        from agents.analyst import textutil

        kept = [value for value in values if not textutil.is_filler(value)]
        return kept or values
    confidence: float = Field(ge=0, le=1)
    dedupe_key: str | None = None

    def compute_dedupe_key(self) -> str:
        """같은 주제를 매일 다시 제안하지 않게 막는 키."""
        if self.dedupe_key:
            return self.dedupe_key
        seed = "|".join(sorted(self.keywords))
        return hashlib.sha256(seed.encode()).hexdigest()[:32]


class RejectedCluster(BaseModel):
    """제안하지 않기로 한 주제와 그 이유.

    버리는 판단도 기록한다. '왜 주차 질문이 87건인데 추천이 안 떴지' 를
    관리자가 되짚을 수 있어야 한다.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    reason: str = Field(description="안내와 무관 / 이미 메뉴 있음 / 표본 부족 등")
