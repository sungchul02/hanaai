"""분석 실행기.

  질문 로드 → 쓰레기 거르기 → 비슷한 것 묶기 → 기존 메뉴로 답할 수 있는지 확인
  → LLM 이 메뉴 초안 생성 → content_proposal 저장

제안은 pending_review 로만 들어간다. 키오스크에 내보내는 것은 관리자뿐이다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agents.analyst import textutil
from agents.analyst.cluster import (
    Cluster,
    MenuRef,
    QuestionItem,
    build_clusters,
    find_covering_menu,
)
from agents.analyst.content_agent import ContentAgent
from agents.analyst.generator import ProposalGenerator, get_generator
from services.common.models import (
    AnalysisRun,
    CmsMenu,
    ContentProposal,
    QuestionCluster,
    QuestionClusterMember,
    QuestionLog,
)

log = structlog.get_logger(__name__)

ANALYZER_VERSION = "1.0.0"

# 이보다 적게 물어본 주제는 제안하지 않는다. 우연일 수 있다.
MIN_CLUSTER_SIZE = 4
# 기존 메뉴가 이미 답하고 있으면 새로 만들지 않는다.
COVERAGE_THRESHOLD = 0.35


@dataclass(frozen=True)
class Window:
    start: dt.datetime
    end: dt.datetime

    @property
    def label(self) -> str:
        return f"{self.start.date().isoformat()}/{self.end.date().isoformat()}"

    @classmethod
    def last_days(cls, days: int, now: dt.datetime | None = None) -> Window:
        end = now or dt.datetime.now(dt.UTC)
        return cls(start=end - dt.timedelta(days=days), end=end)


def classify(text_value: str, normalized: str) -> tuple[str, str | None]:
    """규칙으로 판정할 수 있는 것만 판정한다.

    '안내와 무관한 주제인가' 는 여기서 판단하지 않는다. 질문 하나만 보고는 알기 어렵고,
    묶어놓고 보면 훨씬 분명해진다. 그 판단은 클러스터 단위로 LLM 이 한다.
    """
    if textutil.is_abusive(text_value):
        return "abusive", "욕설/비방 표현 포함"
    if len(normalized) < 2:
        return "too_short", "내용을 판단할 수 없을 만큼 짧음"
    return "useful", None


_KIOSKS_OF_CUSTOMER = text(
    "SELECT k.kiosk_id FROM kiosk k JOIN site s ON s.site_id = k.site_id "
    "WHERE s.customer_id = :customer_id"
)


def _load_questions(session: Session, window: Window, customer_id: int) -> list[QuestionLog]:
    kiosk_ids = [
        row[0] for row in session.execute(_KIOSKS_OF_CUSTOMER, {"customer_id": customer_id})
    ]
    if not kiosk_ids:
        return []
    stmt = (
        select(QuestionLog)
        .where(
            QuestionLog.asked_at >= window.start,
            QuestionLog.asked_at < window.end,
            QuestionLog.kiosk_id.in_(kiosk_ids),
        )
        # 순서를 고정한다. 탐욕적 클러스터링은 입력 순서에 결과가 의존한다.
        .order_by(QuestionLog.asked_at, QuestionLog.question_id)
    )
    return list(session.scalars(stmt))


def _load_menus(session: Session, customer_id: int) -> list[MenuRef]:
    rows = session.scalars(
        select(CmsMenu).where(CmsMenu.customer_id == customer_id, CmsMenu.status == "published")
    )
    return [
        MenuRef(menu_id=m.menu_id, title=m.title, keywords=list(m.keywords), body=m.body)
        for m in rows
    ]


def _cluster_payload(cluster: Cluster, menu: MenuRef | None) -> dict[str, Any]:
    """분석이 만드는 주제 하나.

    '_' 로 시작하는 키는 LLM 에 보내지 않는다(generator.build_user_prompt 참조).
    _questions 는 콘텐츠 Agent 가 초안을 검증할 때 쓰는 원재료다. 수백 건을 프롬프트에
    실으면 비용만 늘고 판단에는 도움이 안 되지만, 검증에는 전부 필요하다.
    """
    return {
        "label": cluster.label,
        "question_count": cluster.size,
        "unanswered_count": cluster.unanswered,
        "sample_questions": cluster.samples(),
        "keywords": cluster.keywords,
        "existing_menu": menu.title if menu else None,
        "dedupe_key": "-".join(cluster.keywords[:3]) or cluster.label[:20],
        "_questions": [item.text for item in cluster.items],
    }


def _first_cluster_id(
    generator: ProposalGenerator, key: str, by_label: dict[str, int]
) -> int | None:
    """추천이 나온 첫 주제의 id. Agent 가 남긴 source label 을 따라간다."""
    sources = getattr(generator, "sources", None)
    labels = sources.get(key, []) if sources else []
    for label in labels:
        if label in by_label:
            return by_label[label]
    return None


def _verification_columns(generator: ProposalGenerator, key: str | None) -> dict[str, Any]:
    """콘텐츠 Agent 가 남긴 검증 결과. 일반 생성기면 빈 값이다."""
    traces = getattr(generator, "traces", None)
    trace = traces.get(str(key or "")) if traces else None
    if trace is None:
        return {}
    return {
        "verified_coverage": round(trace.coverage, 4),
        "verified_matched": trace.matched,
        "verified_total": trace.total,
        "revisions": trace.revisions,
        "remaining_misses": trace.remaining_misses[:8],
    }


def run_analysis(
    session: Session,
    window: Window,
    customer_id: int,
    generator: ProposalGenerator | None = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> int:
    """분석 1회 실행. analysis_run_id 를 돌려준다."""
    generator = generator or get_generator()

    run = AnalysisRun(
        window_start=window.start,
        window_end=window.end,
        customer_id=customer_id,
        analyzer_version=ANALYZER_VERSION,
        model=generator.name,
        status="running",
    )
    session.add(run)
    session.commit()

    try:
        questions = _load_questions(session, window, customer_id)
        run.questions_seen = len(questions)

        # 1) 규칙으로 쓰레기 거르기
        usable: list[QuestionItem] = []
        for question in questions:
            verdict, reason = classify(question.question_text, question.normalized_text)
            question.verdict = verdict
            question.verdict_reason = reason
            if verdict == "useful":
                usable.append(
                    QuestionItem(
                        question_id=question.question_id,
                        text=question.question_text,
                        normalized=question.normalized_text,
                        answered=question.answer_source == "cms_menu",
                        matched_menu_id=question.matched_menu_id,
                    )
                )
        session.flush()

        # 2) 비슷한 질문끼리 묶기
        clusters = build_clusters(usable)
        run.clusters_found = len(clusters)
        log.info("clustered", questions=len(questions), usable=len(usable), clusters=len(clusters))

        # 3) 기존 메뉴로 답할 수 있는지 확인하고 저장
        menus = _load_menus(session, customer_id)
        payloads: list[dict[str, Any]] = []
        cluster_by_label: dict[str, int] = {}

        for cluster in clusters:
            menu, score = find_covering_menu(cluster, menus, COVERAGE_THRESHOLD)
            row = QuestionCluster(
                analysis_run_id=run.analysis_run_id,
                customer_id=customer_id,
                label=cluster.label,
                size=cluster.size,
                unanswered=cluster.unanswered,
                covered_menu_id=menu.menu_id if menu else None,
                coverage_score=round(score, 4),
                keywords=cluster.keywords,
            )
            session.add(row)
            session.flush()

            session.execute(
                pg_insert(QuestionClusterMember).values(
                    [
                        {
                            "cluster_id": row.cluster_id,
                            "question_id": item.question_id,
                            "similarity": cluster.similarities.get(item.question_id),
                        }
                        for item in cluster.items
                    ]
                )
            )
            session.execute(
                update(QuestionLog)
                .where(QuestionLog.question_id.in_([i.question_id for i in cluster.items]))
                .values(cluster_id=row.cluster_id)
            )

            # 너무 작거나 이미 답하고 있는 주제는 LLM 에게 보내지 않는다. 비용과 소음을 줄인다.
            if cluster.size >= min_cluster_size and menu is None:
                payload = _cluster_payload(cluster, menu)
                payloads.append(payload)
                cluster_by_label[cluster.label] = row.cluster_id
        session.commit()

        # 4) LLM 이 메뉴 초안 생성 → Agent 가 검증하고 못 잡은 질문이 있으면 고친다
        if payloads:
            active = list(
                session.scalars(
                    select(CmsMenu).where(
                        CmsMenu.customer_id == customer_id, CmsMenu.status == "published"
                    )
                )
            )
            agent = ContentAgent(generator, existing_menus=active)
            proposals = agent.generate(payloads, window.label)
            generator = agent  # 아래에서 usage / traces 를 읽는다
        else:
            proposals = []

        stored = 0
        for proposal in proposals:
            dedupe_key = proposal.compute_dedupe_key()
            result = session.execute(
                pg_insert(ContentProposal)
                .values(
                    analysis_run_id=run.analysis_run_id,
                    cluster_id=_first_cluster_id(generator, dedupe_key, cluster_by_label),
                    customer_id=customer_id,
                    title=proposal.title,
                    body=proposal.body,
                    reason=proposal.reason,
                    keywords=proposal.keywords,
                    sample_questions=proposal.evidence.sample_questions,
                    question_count=proposal.evidence.question_count,
                    impact_score=proposal.impact_score,
                    confidence=proposal.confidence,
                    contract=proposal.model_dump(mode="json"),
                    status="pending_review",
                    dedupe_key=dedupe_key,
                    **_verification_columns(generator, proposal.dedupe_key),
                )
                .on_conflict_do_nothing(
                    index_elements=["customer_id", "dedupe_key"],
                    index_where=text(
                        "dedupe_key IS NOT NULL "
                        "AND status IN ('pending_review', 'approved', 'edited')"
                    ),
                )
                .returning(ContentProposal.proposal_id)
            ).scalar_one_or_none()
            if result is not None:
                stored += 1

        run.proposals_made = stored
        run.token_usage = getattr(generator, "last_usage", None)
        rejected = getattr(generator, "last_errors", [])
        if rejected:
            run.error = f"검증 실패 {len(rejected)}건: " + " | ".join(rejected)[:2000]
        run.status = "succeeded"
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()

        log.info(
            "analysis_done",
            run_id=run.analysis_run_id,
            backend=generator.name,
            questions=len(questions),
            clusters=len(clusters),
            sent_to_llm=len(payloads),
            proposals=len(proposals),
            stored=stored,
        )
    except Exception as exc:
        session.rollback()
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()
        raise

    return run.analysis_run_id
