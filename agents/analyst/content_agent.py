"""콘텐츠 작성 Agent.

단일 LLM 호출과 다른 점은 하나다. **자기가 쓴 초안을 실제 매처에 넣어보고, 못 잡은 질문을
들고 다시 고친다.** 도구를 쥐고 결과를 보고 판단을 바꾸는 반복이 있어야 Agent다.

왜 필요했나:
  주차 안내 메뉴가 있는데도 "차 세울 데 있나요?" 102건이 계속 미응답으로 남았다.
  LLM 은 초안을 던지고 끝이라 자기 메뉴가 그 질문들을 놓친다는 걸 알 방법이 없었다.

쥐는 도구는 하나뿐이다: agents/analyst/verify.verify_draft()
읽기 전용이고 부작용이 없다. DB 를 쓰지도, 파일을 건드리지도 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from agents.analyst import textutil
from agents.analyst.placeholder import clean_body, prune_keywords
from agents.analyst.verify import TARGET_COVERAGE, VerifyResult, as_menu, verify_draft
from agents.contracts.proposal import ContentProposal
from services.common.models import CmsMenu

log = structlog.get_logger(__name__)

MAX_REVISIONS = 2


class Completer(Protocol):
    """LLM 을 한 번 부르는 최소 능력. 수정 요청에 쓴다."""

    name: str

    def complete(self, user_prompt: str) -> str: ...


class BaseGenerator(Protocol):
    name: str

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]: ...


@dataclass
class AgentTrace:
    """무엇을 왜 고쳤는지. 관리자 화면과 로그에 그대로 쓴다."""

    revisions: int
    coverage: float
    matched: int
    total: int
    remaining_misses: list[str]

    def as_json(self) -> dict[str, Any]:
        return {
            "revisions": self.revisions,
            "coverage": round(self.coverage, 4),
            "matched": self.matched,
            "total": self.total,
            "remaining_misses": self.remaining_misses[:8],
        }


REVISE_PROMPT = """\
아래 안내 메뉴 초안을 실제 질문 매칭기에 넣어봤더니, 대상 질문 중 일부를 여전히 잡지 못한다.
매칭기는 질문의 낱말과 메뉴의 제목·키워드·본문을 견준다. 한 글자 키워드는 무시된다.

현재 초안:
__DRAFT__

이 초안으로도 답하지 못하는 질문들:
__MISSES__

이 질문들이 잡히도록 keywords 와 body 를 고쳐라.
- 못 잡은 질문에 실제로 나오는 낱말을 keywords 에 넣어라. 두 글자 이상이어야 한다.
- 안내 내용이 달라지면 body 도 고쳐라. 다만 **모르는 사실을 지어내지 마라.**
  질문에서 알 수 없는 것은 (확인 후 입력 필요) 로 남겨라.
- title 과 evidence 는 그대로 둬라.

고친 ContentProposal 을 JSON 배열로 하나만 출력해라.
"""


def resolve_clusters(
    labels: list[str], by_label: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """모델이 돌려준 label 로 원본 주제를 찾는다.

    dedupe_key 같은 불투명한 문자열을 모델에게 그대로 옮기라고 시켰더니 자기 식으로
    지어냈다(wifi, parking). label 은 사람이 읽을 수 있는 문장이라 훨씬 잘 옮긴다.
    그래도 어긋날 수 있으므로 정확히 안 맞으면 가장 비슷한 주제를 고른다.
    """
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label in labels:
        cluster = by_label.get(label)
        if cluster is None:
            best_label, best_score = None, 0.6
            for candidate in by_label:
                score = textutil.similarity(label, candidate)
                if score >= best_score:
                    best_label, best_score = candidate, score
            cluster = by_label.get(best_label) if best_label else None
        if cluster is not None and str(cluster.get("label")) not in seen:
            seen.add(str(cluster.get("label")))
            found.append(cluster)
    return found


class ContentAgent:
    """초안 작성기를 감싸서 검증·수정 루프를 붙인다.

    ProposalGenerator 를 그대로 흉내내므로 runner 는 이게 Agent 인지 모른다.
    백엔드를 갈아끼우듯 이것도 갈아끼울 수 있다.
    """

    def __init__(
        self,
        base: BaseGenerator,
        existing_menus: list[CmsMenu] | None = None,
        max_revisions: int = MAX_REVISIONS,
    ) -> None:
        self.base = base
        self.existing_menus = existing_menus or []
        self.max_revisions = max_revisions
        self.name = f"{base.name}+agent"
        self.last_usage: dict[str, Any] | None = None
        self.last_errors: list[str] = []
        # 제안별 검증 결과. runner 가 DB 에 남긴다.
        self.traces: dict[str, AgentTrace] = {}
        # 제안이 어느 주제에서 나왔는지. runner 가 cluster_id 를 잇는 데 쓴다.
        self.sources: dict[str, list[str]] = {}

    def _tidy_placeholders(self, proposals: list[ContentProposal]) -> list[ContentProposal]:
        """옆 메뉴가 다루는 내용을 '(확인 후 입력 필요)' 로 떠넘긴 문장을 지운다.

        메뉴를 쪼개라고 시키면 모델은 자기 메뉴만 보고 "내 본문에 없으니 모른다" 고 쓴다.
        프롬프트로 두 번 막아봤고 두 번 다 안 됐다(26건 중 25건). 구조적인 실수라
        여기서 지운다. 근거에도 옆 메뉴에도 없는 빈칸은 그대로 남는다.
        """
        titles = [p.title for p in proposals]
        fixed: list[ContentProposal] = []
        for proposal in proposals:
            others = [t for t in titles if t != proposal.title]
            body, dropped = clean_body(
                proposal.body, others,
                has_siblings=len(proposals) > 1, own_title=proposal.title,
            )
            if not dropped:
                fixed.append(proposal)
                continue
            log.info("placeholder_dropped", title=proposal.title, sentences=dropped)
            fixed.append(proposal.model_copy(update={"body": body}))
        return self._tidy_keywords(fixed)

    def _tidy_keywords(self, proposals: list[ContentProposal]) -> list[ContentProposal]:
        """옆 메뉴가 다루는 항목의 키워드를 뺀다.

        메뉴는 쪼갰는데 키워드는 안 쪼개진다. '주차장 운영시간' 에 '주차 가능 대수' 가
        들어 있어서 규모 질문까지 시간 안내가 잡았다. 쪼갠 의미가 사라진다.
        """
        titles = [p.title for p in proposals]
        fixed: list[ContentProposal] = []
        for proposal in proposals:
            others = [t for t in titles if t != proposal.title]
            keywords, dropped = prune_keywords(proposal.title, list(proposal.keywords), others)
            if not dropped:
                fixed.append(proposal)
                continue
            log.info("keywords_pruned", title=proposal.title, dropped=dropped)
            fixed.append(proposal.model_copy(update={"keywords": keywords}))
        return fixed

    def _verify(
        self,
        proposal: ContentProposal,
        questions: list[str],
        siblings: list[ContentProposal] | None = None,
    ) -> VerifyResult:
        """초안을 실제 매처에 넣어본다.

        **같은 주제에서 함께 나온 형제 초안도 나란히 놓는다.** 이게 없으면
        쪼갠 메뉴 하나하나가 주제 전체를 혼자 감당해야 하는 것으로 잰다.

        실제로 그래서 망가졌다. 부서 위치 8건을 층별 메뉴 7개로 쪼갰더니,
        '1층 부서 안내' 가 "세정과 몇 층이에요?"(6층) 를 못 잡는다고 미스로 잡혔고,
        수정 요청을 받은 모델이 지시대로 세정과를 1층 키워드에 넣었다.
        모든 층 메뉴에 모든 부서명이 들어가 전부 1.00 동점이 됐고,
        결국 세정과 질문에 1층 안내가 "안내 완료" 로 답했다.

        형제가 잡는 질문은 미스가 아니다. 운영에서도 형제와 나란히 놓이기 때문이다.
        """
        draft = as_menu(proposal.title, proposal.body, proposal.keywords)
        others = [
            as_menu(sib.title, sib.body, sib.keywords, menu_id=-2 - index)
            for index, sib in enumerate(siblings or [])
        ]
        return verify_draft(draft, questions, [*self.existing_menus, *others])

    def _revise(self, proposal: ContentProposal, result: VerifyResult) -> ContentProposal | None:
        """못 잡은 질문을 들고 다시 쓰게 한다. 고치지 못하면 None."""
        completer = self.base if hasattr(self.base, "complete") else None
        if completer is None:
            return None

        from agents.analyst.generator import parse_proposals

        draft = proposal.model_dump(mode="json")
        draft.pop("contract_version", None)
        prompt = REVISE_PROMPT.replace(
            "__DRAFT__", json.dumps(draft, ensure_ascii=False, indent=2)
        ).replace("__MISSES__", "\n".join(f"- {q}" for q in result.misses))

        try:
            text = completer.complete(prompt)
        except Exception as exc:  # 수정 실패는 치명적이지 않다. 원안을 그대로 쓴다.
            log.warning("revision_call_failed", error=str(exc)[:200])
            return None

        revised, errors = parse_proposals(text)
        if errors:
            self.last_errors.extend(errors)
        if not revised:
            return None

        # 모델이 근거나 제목을 바꿔치기하지 못하게 원본 값을 되돌린다.
        fixed = revised[0].model_copy(
            update={
                "title": proposal.title,
                "evidence": proposal.evidence,
                "impact_score": proposal.impact_score,
                "dedupe_key": proposal.dedupe_key,
            }
        )
        return fixed

    def generate(self, clusters: list[dict[str, Any]], window: str) -> list[ContentProposal]:
        proposals = self.base.generate(clusters, window)
        self.last_usage = getattr(self.base, "last_usage", None)
        self.last_errors = list(getattr(self.base, "last_errors", []))
        if not proposals:
            return proposals

        by_label = {str(cluster.get("label")): cluster for cluster in clusters}

        # 같은 주제에서 나온 것끼리 형제로 묶는다. 검증할 때 나란히 놓는다.
        by_topic: dict[str, list[ContentProposal]] = {}
        for proposal in proposals:
            by_topic.setdefault("|".join(sorted(proposal.source_labels)), []).append(proposal)

        verified: list[ContentProposal] = []
        for proposal in proposals:
            matched = resolve_clusters(proposal.source_labels, by_label)
            questions = [
                question for cluster in matched for question in (cluster.get("_questions") or [])
            ] or list(proposal.evidence.sample_questions)
            family = by_topic.get("|".join(sorted(proposal.source_labels)), [])
            siblings = [p for p in family if p is not proposal]

            result = self._verify(proposal, questions, siblings)
            revisions = 0
            while not result.ok and revisions < self.max_revisions:
                revised = self._revise(proposal, result)
                if revised is None:
                    break
                revisions += 1
                new_result = self._verify(revised, questions, siblings)
                log.info(
                    "draft_revised",
                    title=proposal.title,
                    attempt=revisions,
                    before=round(result.coverage, 3),
                    after=round(new_result.coverage, 3),
                )
                # 나빠졌으면 되돌린다. 고치다가 망치는 경우가 실제로 있다.
                if new_result.coverage <= result.coverage:
                    break
                proposal, result = revised, new_result

            # 키는 수정이 끝난 뒤에 잡는다. 키워드가 바뀌면 dedupe_key 도 바뀌므로,
            # 수정 전 키로 저장하면 runner 가 검증 결과를 찾지 못한다.
            key = proposal.compute_dedupe_key()
            self.sources[key] = [str(c.get("label")) for c in matched]
            self.traces[key] = AgentTrace(
                revisions=revisions,
                coverage=result.coverage,
                matched=result.covered,
                total=result.total,
                remaining_misses=result.misses,
            )
            log.info(
                "draft_verified",
                title=proposal.title,
                revisions=revisions,
                coverage=round(result.coverage, 3),
                summary=result.summary(),
            )
            verified.append(proposal)

        # 정리는 **수정이 끝난 뒤에** 한다.
        # 앞에서 하면 _revise 가 본문을 다시 쓰면서 '(확인 후 입력 필요)' 를 도로 넣는다.
        # 수정 프롬프트가 그렇게 시키기까지 한다. 실제로 14건 중 11건이 그랬다.
        verified = self._tidy_placeholders(verified)

        # 수정 과정에서 쓴 비용도 합산되도록 마지막 값을 다시 읽는다.
        self.last_usage = getattr(self.base, "last_usage", None)
        return verified


def coverage_target() -> float:
    return TARGET_COVERAGE
