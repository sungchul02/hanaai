"""상태 전이 → 작업 디스패치.

Agent 가 Agent 를 직접 호출하지 않기 때문에, 둘을 잇는 것은 이 프로세스 하나뿐이다.
"승인된 제안 중 아직 개발이 돌지 않은 것"을 찾아 개발 Agent 를 띄운다.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from agents.contracts.proposal import FeatureProposal as ProposalContract
from agents.developer.runner import run_developer
from services.common.db import get_sessionmaker
from services.common.models import DevRun, FeatureProposal

log = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 30


def find_dispatchable(session: Session, limit: int = 5) -> list[FeatureProposal]:
    """승인됐고, 아직 개발 실행 이력이 없는 제안."""
    has_run = exists().where(DevRun.proposal_id == FeatureProposal.proposal_id)
    stmt = (
        select(FeatureProposal)
        .where(FeatureProposal.status == "approved", ~has_run)
        .order_by(FeatureProposal.impact_score.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def dispatch_once(session: Session, repo: Path) -> int:
    """한 바퀴 돈다. 처리한 제안 수를 돌려준다."""
    proposals = find_dispatchable(session)
    for row in proposals:
        contract = ProposalContract.model_validate(row.body)
        log.info("dispatch_developer", proposal_id=row.proposal_id, title=row.title)
        dev_run_id, report = run_developer(session, row.proposal_id, contract, repo)
        log.info(
            "developer_done",
            proposal_id=row.proposal_id,
            dev_run_id=dev_run_id,
            ok=report.ok,
            violations=report.as_json(),
        )
    return len(proposals)


def serve(repo: Path, interval: int = POLL_INTERVAL_SECONDS) -> None:
    """폴링 루프.

    TODO(M6): PostgreSQL LISTEN/NOTIFY 로 바꾼다. 승인 순간 바로 뜨는 것이 맞고,
    폴링은 승인과 착수 사이에 최대 interval 만큼의 지연을 만든다.
    """
    factory = get_sessionmaker()
    log.info("orchestrator_start", repo=str(repo), interval=interval)
    while True:
        with factory() as session:
            try:
                dispatch_once(session, repo)
            except Exception:
                log.exception("dispatch_failed")
                session.rollback()
        time.sleep(interval)
