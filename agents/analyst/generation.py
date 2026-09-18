"""2단계: 관리자가 승인한 주제에 대해서만 근거를 찾고 답변 초안을 만든다.

1단계(runner.run_triage)와 나눠 둔 이유는 비용이다.
근거 조사와 답변 생성은 LLM 을 여러 번 부른다. 관리자가 원하지 않는 주제에
그 돈을 쓸 이유가 없다. 전에는 버튼 하나가 끝까지 갔고,
관리자는 다 끝난 뒤에야 결과를 봤다.

하위 Agent 가 근거를 못 찾으면 초안을 만들지 않는다. 지어내지 않고
'문서 보강 필요' 로 남겨 관리자에게 되돌린다 — 그게 다음 숙제다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agents.analyst.content_agent import ContentAgent
from agents.analyst.generator import ProposalGenerator, get_generator
from agents.analyst.runner import (
    MIN_CLUSTER_SIZE,
    Window,
    _fail,
    _first_cluster_id,
    _link_evidence,
    _record_usage,
    _verification_columns,
)
from agents.analyst.supervisor import Completer as SupervisorCompleter
from agents.analyst.supervisor import SupervisorAgent
from services.common.models import (
    AnalysisRun,
    CmsMenu,
    ContentProposal,
    QuestionCluster,
    QuestionLog,
)

log = structlog.get_logger(__name__)


def approved_payloads(session: Session, run_id: int) -> list[dict[str, Any]]:
    """관리자가 승인한 주제만 다시 짠다.

    1단계에서 만든 payload 를 메모리에 들고 있을 수 없다. 그 사이에 사람이
    화면을 보고 결정하기 때문이다. 그래서 DB 에서 되살린다.
    """
    rows = session.scalars(
        select(QuestionCluster).where(
            QuestionCluster.analysis_run_id == run_id,
            QuestionCluster.review_status == "approved",
        )
    )
    payloads: list[dict[str, Any]] = []
    for row in rows:
        questions = list(
            session.scalars(
                select(QuestionLog.question_text)
                .where(QuestionLog.cluster_id == row.cluster_id)
                .order_by(QuestionLog.question_id)
            )
        )
        payloads.append(
            {
                "label": row.label,
                "category": row.category,
                "question_count": row.size,
                "unanswered_count": row.unanswered,
                "sample_questions": questions[:5],
                "keywords": list(row.keywords),
                "existing_menu": None,
                "dedupe_key": "-".join(row.keywords[:3]) or row.label[:20],
                "_questions": questions,
                "_cluster_id": row.cluster_id,
            }
        )
    return payloads


def _collect_evidence(
    session: Session, supervisor: SupervisorAgent, payloads: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """승인된 주제마다 하위 Agent 에게 근거를 찾아오게 한다."""
    ready: list[dict[str, Any]] = []
    missing = 0
    for payload in payloads:
        report = supervisor.worker.collect(str(payload["label"]), payload["_questions"])
        session.execute(
            update(QuestionCluster)
            .where(QuestionCluster.cluster_id == payload["_cluster_id"])
            .values(
                evidence_found=report.answerable,
                evidence_summary=(report.summary or None),
                evidence_missing=report.missing[:8],
            )
        )
        # 근거 문서를 아직 하나도 안 채운 고객사라면 근거를 요구하지 않는다.
        if not (report.answerable or not supervisor.grounded):
            missing += 1
            continue
        payload["evidence_from_documents"] = [
            {"document": e.document_title, "heading": e.heading, "text": e.text}
            for e in report.evidence
        ]
        payload["source_documents"] = sorted({e.document_title for e in report.evidence})
        payload["_evidence"] = report.evidence
        ready.append(payload)
    session.commit()
    return ready, missing


def _store(
    session: Session,
    run_id: int,
    customer_id: int,
    generator: Any,
    proposals: list[Any],
    ready: list[dict[str, Any]],
) -> tuple[int, set[int]]:
    by_label = {str(p["label"]): int(p["_cluster_id"]) for p in ready}
    evidence_by_cluster = {int(p["_cluster_id"]): p.get("_evidence") or [] for p in ready}

    stored = 0
    answered: set[int] = set()
    for proposal in proposals:
        dedupe_key = proposal.compute_dedupe_key()
        cluster_id = _first_cluster_id(generator, dedupe_key, by_label)
        result = session.execute(
            pg_insert(ContentProposal)
            .values(
                analysis_run_id=run_id,
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
            if cluster_id is not None:
                answered.add(cluster_id)
    return stored, answered


def run_generation(
    session: Session,
    run_id: int,
    generator: ProposalGenerator | None = None,
) -> int:
    """승인된 주제의 답변 초안을 만든다. 새로 저장된 제안 수를 돌려준다."""
    generator = generator or get_generator()
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise ValueError(f"unknown analysis_run: {run_id}")
    customer_id = run.customer_id
    assert customer_id is not None  # 스키마상 NOT NULL

    payloads = approved_payloads(session, run_id)
    if not payloads:
        log.info("generation_skipped", run_id=run_id, reason="승인된 주제 없음")
        return 0

    run.status = "running"
    session.commit()

    try:
        completer = cast(
            "SupervisorCompleter | None",
            generator if hasattr(generator, "complete") else None,
        )
        supervisor = SupervisorAgent(session, customer_id, completer)
        ready, missing = _collect_evidence(session, supervisor, payloads)

        proposals: list[Any] = []
        if ready:
            active = list(
                session.scalars(
                    select(CmsMenu).where(
                        CmsMenu.customer_id == customer_id, CmsMenu.status == "published"
                    )
                )
            )
            agent = ContentAgent(generator, existing_menus=active)
            window = Window(start=run.window_start, end=run.window_end)
            proposals = list(agent.generate(ready, window.label))
            generator = agent  # 아래에서 usage / traces 를 읽는다

        stored, answered = _store(session, run_id, customer_id, generator, proposals, ready)

        # 답이 만들어진 주제는 목록에서 내린다. 근거를 못 찾은 것은 승인 상태로 남아
        # '문서 보강 필요' 로 계속 보인다 — 그게 관리자의 다음 숙제다.
        if answered:
            session.execute(
                update(QuestionCluster)
                .where(QuestionCluster.cluster_id.in_(answered))
                .values(review_status="answered")
            )

        run.proposals_made = (run.proposals_made or 0) + stored
        run.stats = {
            **(run.stats or {}),
            "approved_topics": len(payloads),
            "evidence_found": len(ready),
            "evidence_missing": missing,
            "generated": len(proposals),
            "blocked_duplicate": len(proposals) - stored,
        }
        _record_usage(run, generator)
        rejected = getattr(generator, "last_errors", [])
        if rejected:
            run.error = f"검증 실패 {len(rejected)}건: " + " | ".join(rejected)[:2000]
        run.status = "succeeded"
        run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()

        log.info(
            "generation_done",
            run_id=run_id,
            approved=len(payloads),
            evidence_found=len(ready),
            proposals=len(proposals),
            stored=stored,
        )
    except Exception as exc:
        _fail(session, run, exc)
        raise

    return stored


def run_analysis(
    session: Session,
    window: Window,
    customer_id: int,
    generator: ProposalGenerator | None = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> int:
    """분류와 생성을 한 번에. **관리자 승인을 건너뛴다.**

    화면은 이걸 쓰지 않는다. 사람이 중간에서 판단하는 것이 이 시스템의 요점이기 때문이다.
    CLI 와 테스트에서 루프 전체를 한 번에 돌려볼 때만 쓴다.
    """
    from agents.analyst.runner import run_triage

    generator = generator or get_generator()
    run_id = run_triage(session, window, customer_id, generator, min_cluster_size)
    session.execute(
        update(QuestionCluster)
        .where(
            QuestionCluster.analysis_run_id == run_id,
            QuestionCluster.review_status == "pending_review",
        )
        .values(review_status="approved", reviewed_by="auto")
    )
    session.commit()
    run_generation(session, run_id, generator)
    return run_id
