"""분석 실행기.

  질문 로드
    → [규칙] 욕설·너무 짧은 것 거르기
    → [규칙] 비슷한 질문끼리 묶기
    → [규칙] 기존 메뉴로 이미 답하고 있는 주제 빼기
    → [상위 Agent·LLM] 안내 가치가 있는 주제인가 판단          supervisor.triage
    → [하위 Agent·LLM] 근거 문서에서 답의 근거 찾아오기        evidence_agent.collect
    → [LLM] 근거를 바탕으로 메뉴 초안 작성                      generator
    → [Agent] 초안을 매처에 넣어보고 못 잡은 질문이 있으면 수정  content_agent
    → content_proposal 저장 (pending_review)

규칙이 앞에 오는 이유: 질문 수천 건을 전부 LLM 에 보낼 수는 없다.
싼 판단으로 수십 개 주제까지 좁힌 다음, 비싼 판단을 거기에만 쓴다.

제안은 pending_review 로만 들어간다. 키오스크에 내보내는 것은 관리자뿐이다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, cast

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
from agents.analyst.supervisor import (
    Completer as SupervisorCompleter,
)
from agents.analyst.supervisor import (
    SupervisionResult,
    SupervisorAgent,
)
from services.common.models import (
    AnalysisRun,
    CmsMenu,
    ContentProposal,
    ProposalEvidence,
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
    if textutil.is_jamo_only(text_value):
        # 'ㅋㅋㅋㅋ' 'ㅇㅇ' 같은 것. 길이는 충분해서 위 규칙을 통과해버린다.
        # 규칙으로 확실히 걸러야 LLM 이 이런 것까지 판단하느라 돈을 쓰지 않는다.
        return "too_short", "자음/모음만 입력되어 뜻을 판단할 수 없음"
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


def _cluster_payload(cluster: Cluster, menu: MenuRef | None, cluster_id: int) -> dict[str, Any]:
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
        "_cluster_id": cluster_id,
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


def _link_evidence(session: Session, proposal_id: int, evidence: list[Any]) -> None:
    """제안이 어느 문서 조각에서 나왔는지 남긴다.

    관리자가 승인하기 전에 출처를 눌러 확인할 수 있어야 한다.
    이게 없으면 AI 가 쓴 문장을 믿을 근거가 아무것도 없다.
    """
    if not evidence:
        return
    session.execute(
        pg_insert(ProposalEvidence)
        .values(
            [
                {
                    "proposal_id": proposal_id,
                    "chunk_id": item.chunk_id,
                    "score": round(item.score, 4),
                    "quote": item.text[:500],
                }
                for item in evidence
            ]
        )
        .on_conflict_do_nothing(index_elements=["proposal_id", "chunk_id"])
    )


def _apply_supervision(
    session: Session, payloads: list[dict[str, Any]], result: SupervisionResult
) -> list[dict[str, Any]]:
    """상위·하위 Agent 의 판단을 주제에 기록하고, 답변 생성으로 넘길 것만 돌려준다.

    버려진 주제도, 근거를 못 찾은 주제도 전부 기록한다. 조용히 사라지면
    관리자는 "왜 이 질문에 제안이 없지" 를 확인할 방법이 없다.
    """
    by_label = {str(p["label"]): p for p in payloads}
    passing: list[dict[str, Any]] = []

    def mark(label: str, **values: Any) -> dict[str, Any] | None:
        payload = by_label.get(label)
        if payload is None:
            return None
        session.execute(
            update(QuestionCluster)
            .where(QuestionCluster.cluster_id == payload["_cluster_id"])
            .values(**values)
        )
        return payload

    def mark_questions(payload: dict[str, Any], verdict: str, reason: str) -> None:
        """질문 하나하나에도 LLM 의 판정을 내려 적는다.

        주제에만 적으면 '최근 질문' 화면은 "너 몇 살이야" 도 useful 로 보여준다.
        LLM 이 판단하고 있다는 사실이 화면 어디에도 드러나지 않는다.

        통과한 것에도 이유를 적는 이유: 'useful' 만으로는 LLM 이 유효하다고 판단한 것인지,
        규칙이 거를 거리를 못 찾았을 뿐인지 구분되지 않는다. 이유가 있으면 판단을 받은 것이다.
        """
        session.execute(
            update(QuestionLog)
            .where(QuestionLog.cluster_id == payload["_cluster_id"])
            .values(verdict=verdict, verdict_reason=f"AI 판단: {reason}")
        )

    for verdict in result.dropped:
        payload = mark(verdict.label, triage_keep=False, triage_reason=verdict.reason)
        if payload is not None:
            mark_questions(payload, "irrelevant", verdict.reason)

    for item in result.unanswerable:
        # 안내 가치는 있는데 근거가 없다. 지어내지 않고 숙제로 남긴다.
        payload = mark(
            item.label,
            triage_keep=True,
            triage_reason=item.keep_reason,
            evidence_found=False,
            evidence_missing=item.report.missing[:8],
        )
        if payload is not None:
            mark_questions(payload, "useful", item.keep_reason)

    for item in result.kept:
        payload = mark(
            item.label,
            triage_keep=True,
            triage_reason=item.keep_reason,
            evidence_found=True,
            evidence_summary=item.report.summary[:2000] or None,
            evidence_missing=item.report.missing[:8],
        )
        if payload is None:
            continue
        mark_questions(payload, "useful", item.keep_reason)
        # 근거를 프롬프트에 실어 보낸다. 이게 있어야 body 에 실제 사실이 들어간다.
        payload["evidence_from_documents"] = [
            {
                "document": e.document_title,
                "heading": e.heading,
                "text": e.text,
            }
            for e in item.report.evidence
        ]
        payload["source_documents"] = sorted({e.document_title for e in item.report.evidence})
        payload["_evidence"] = item.report.evidence
        passing.append(payload)

    return passing


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
        skipped_covered = 0
        skipped_small = 0

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
            if menu is not None:
                skipped_covered += 1
            elif cluster.size < min_cluster_size:
                skipped_small += 1
            else:
                payload = _cluster_payload(cluster, menu, row.cluster_id)
                payloads.append(payload)
                cluster_by_label[cluster.label] = row.cluster_id
        session.commit()

        # 4) 상위 Agent: 안내 가치가 있는 주제만 고르고, 하위 Agent 에게 근거를 찾아오게 한다.
        #    근거를 못 찾은 주제는 여기서 멈춘다. 근거 없이 쓰면 지어내기 때문이다.
        candidates = len(payloads)
        supervision = SupervisionResult()
        if payloads:
            completer = cast(
                "SupervisorCompleter | None",
                generator if hasattr(generator, "complete") else None,
            )
            supervision = SupervisorAgent(session, customer_id, completer).run(payloads)
            payloads = _apply_supervision(session, payloads, supervision)
            session.commit()

        # 5) LLM 이 근거를 바탕으로 메뉴 초안 생성 → Agent 가 검증하고 못 잡은 질문이 있으면 고친다
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

        # 제안 → 근거를 잇기 위한 표. 주제 하나가 어떤 문서 조각에서 나왔는지.
        evidence_by_cluster = {
            int(payload["_cluster_id"]): payload.get("_evidence") or [] for payload in payloads
        }

        stored = 0
        for proposal in proposals:
            dedupe_key = proposal.compute_dedupe_key()
            cluster_id = _first_cluster_id(generator, dedupe_key, cluster_by_label)
            result = session.execute(
                pg_insert(ContentProposal)
                .values(
                    analysis_run_id=run.analysis_run_id,
                    cluster_id=cluster_id,
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
                _link_evidence(session, result, evidence_by_cluster.get(cluster_id or -1, []))
        session.commit()

        run.proposals_made = stored
        run.stats = {
            "clusters": len(clusters),
            "triage_candidates": candidates,
            "sent_to_llm": len(payloads),
            "skipped_covered": skipped_covered,
            "skipped_small": skipped_small,
            **supervision.as_stats(),
            "min_cluster_size": min_cluster_size,
            "generated": len(proposals),
            # 이미 검토 대기중이거나 반영한 주제는 다시 제안하지 않는다
            "blocked_duplicate": len(proposals) - stored,
        }
        # 마지막 호출이 아니라 이번 분석에서 쓴 전부를 남긴다.
        # ContentAgent 가 생성기를 감싸고 있으므로 안쪽의 누적값을 찾아 읽는다.
        base = getattr(generator, "base", generator)
        run.token_usage = getattr(base, "total_usage", None) or getattr(
            generator, "last_usage", None
        )
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
