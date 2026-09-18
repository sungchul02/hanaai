"""개발 Agent 실행기.

LLM 이 붙지 않은 지금도 workspace 준비 / 변경 수집 / 가드레일 판정 / dev_run 기록은
동작한다. 비어 있는 것은 implement() 하나뿐이며, 그것이 M5 의 작업 범위다.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from agents.contracts.proposal import FeatureProposal, Scope
from agents.developer.guardrails import GuardrailConfig, GuardrailReport, check_changes
from services.common.models import DevRun

log = structlog.get_logger(__name__)

DEVELOPER_MODEL = "claude-opus-5"


@dataclass(frozen=True)
class Workspace:
    repo: Path
    branch: str
    path: Path


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def prepare_workspace(repo: Path, proposal_id: int, base: str = "main") -> Workspace:
    """제안 하나당 격리된 worktree 를 만든다.

    Agent 가 메인 작업 트리를 건드리지 못하게 하는 것이 목적이다.
    """
    branch = f"agent/proposal-{proposal_id}"
    path = repo.parent / ".agent-workspaces" / branch.replace("/", "-")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _git(repo, "worktree", "add", "-b", branch, str(path), base)
    return Workspace(repo=repo, branch=branch, path=path)


def collect_changed_files(workspace: Workspace, base: str = "main") -> dict[str, int]:
    """git diff --numstat → {경로: 변경 줄 수}."""
    output = _git(workspace.path, "diff", "--numstat", f"{base}...HEAD")
    changed: dict[str, int] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        # 바이너리 파일은 '-' 로 나온다
        count = (0 if added == "-" else int(added)) + (0 if removed == "-" else int(removed))
        changed[path] = count
    return changed


def implement(workspace: Workspace, proposal: FeatureProposal) -> None:
    """M5 에서 구현한다.

    할 일:
      1. Claude Agent SDK 세션을 workspace.path 에 띄운다.
      2. 도구를 제한한다 — 파일 편집과 테스트 실행만. 네트워크 쓰기는 막는다.
      3. 먼저 '변경할 파일 목록 + 테스트 전략'을 산출시키고, check_changes 로 사전 검증한다.
         (실제로 고치기 전에 계획 단계에서 거르는 것이 훨씬 싸다)
      4. 편집 → 테스트 → 실패 시 수정 루프. 반복 상한과 토큰 예산을 건다.
      5. acceptance_criteria 각 항목에 대응하는 테스트를 만들고 매핑표를 남긴다.
    """
    raise NotImplementedError("M5: Claude Agent SDK 연결")


def run_developer(
    session: Session,
    proposal_id: int,
    proposal: FeatureProposal,
    repo: Path,
    base: str = "main",
    config: GuardrailConfig | None = None,
) -> tuple[int, GuardrailReport]:
    """개발 실행 1회. (dev_run_id, 가드레일 판정) 을 돌려준다."""
    dev_run = DevRun(proposal_id=proposal_id, status="running")
    session.add(dev_run)
    session.commit()

    workspace = prepare_workspace(repo, proposal_id, base=base)
    dev_run.branch = workspace.branch

    try:
        implement(workspace, proposal)
        changed = collect_changed_files(workspace, base=base)
        report = check_changes(_as_scope(proposal.scope), changed, config)

        dev_run.files_changed = len(changed)
        dev_run.lines_changed = sum(changed.values())
        dev_run.guardrail_hits = report.as_json()
        dev_run.status = "succeeded" if report.ok else "failed"
        if not report.ok:
            dev_run.failure_reason = "guardrail violation"
    except NotImplementedError:
        raise
    except Exception as exc:
        dev_run.status = "failed"
        dev_run.failure_reason = f"{type(exc).__name__}: {exc}"
        report = GuardrailReport()
    finally:
        dev_run.finished_at = dt.datetime.now(dt.UTC)
        session.commit()

    return dev_run.dev_run_id, report


def _as_scope(value: str) -> Scope:
    if value not in ("kiosk_app", "ops_backend", "device_driver", "config_only"):
        raise ValueError(f"unknown scope: {value}")
    return value  # type: ignore[return-value]
