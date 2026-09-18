"""개발 Agent 가드레일.

LLM 호출과 무관한 순수 함수로 두는 것이 핵심이다. Agent 가 무엇을 하든 이 함수가
마지막에 판정하며, 여기는 테스트로 고정할 수 있다. CI 에서도 같은 함수를 돌린다.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from agents.contracts.proposal import Scope

# scope 별로 손댈 수 있는 경로. 여기 없는 경로를 건드리면 실패다.
SCOPE_PATHS: dict[Scope, tuple[str, ...]] = {
    "kiosk_app": ("kiosk_app/", "tests/kiosk_app/"),
    "ops_backend": ("services/", "agents/", "orchestrator/", "db/", "tests/"),
    "device_driver": ("drivers/", "tests/drivers/"),
    "config_only": ("config/",),
}

# scope 와 무관하게 금지. 사고가 나면 되돌리기 가장 어려운 것들.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    ".env*",
    "**/secrets*",
    "**/*.pem",
    "**/*.key",
    "db/migrations/sql/0001_*",  # 적용된 마이그레이션은 수정이 아니라 추가로 바꾼다
    "db/migrations/sql/0002_*",
    ".github/workflows/*",  # CI 가 스스로를 느슨하게 만들지 못하게
    "agents/developer/guardrails.py",  # 가드레일이 스스로를 풀지 못하게
)

TEST_PATTERNS: tuple[str, ...] = ("tests/**", "**/test_*.py", "**/*_test.py")


@dataclass(frozen=True)
class GuardrailConfig:
    max_files: int = 20
    max_lines: int = 800
    require_tests: bool = True


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str


@dataclass
class GuardrailReport:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_json(self) -> list[dict[str, str]]:
        return [{"rule": v.rule, "detail": v.detail} for v in self.violations]


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def check_changes(
    scope: Scope,
    changed_files: dict[str, int],
    config: GuardrailConfig | None = None,
) -> GuardrailReport:
    """변경 목록을 판정한다.

    changed_files: 경로 -> 변경 줄 수(추가+삭제). git diff --numstat 에서 얻는다.
    """
    config = config or GuardrailConfig()
    report = GuardrailReport()
    allowed = SCOPE_PATHS[scope]

    for path in sorted(changed_files):
        normalized = path.replace("\\", "/")
        if _matches_any(normalized, FORBIDDEN_PATTERNS):
            report.violations.append(
                Violation("forbidden_path", f"{normalized} 은(는) 수정 금지 대상이다")
            )
            continue
        if not normalized.startswith(allowed):
            report.violations.append(
                Violation(
                    "path_whitelist",
                    f"{normalized} 은(는) scope '{scope}' 의 허용 경로 {allowed} 밖이다",
                )
            )

    if len(changed_files) > config.max_files:
        report.violations.append(
            Violation(
                "max_files",
                f"변경 파일 {len(changed_files)}개 > 상한 {config.max_files}개. 제안을 쪼개라",
            )
        )

    total_lines = sum(changed_files.values())
    if total_lines > config.max_lines:
        report.violations.append(
            Violation(
                "max_lines",
                f"변경 {total_lines}줄 > 상한 {config.max_lines}줄. 제안을 쪼개라",
            )
        )

    if config.require_tests and changed_files:
        has_test = any(_matches_any(p.replace("\\", "/"), TEST_PATTERNS) for p in changed_files)
        if not has_test:
            report.violations.append(Violation("require_tests", "테스트 파일 변경이 하나도 없다"))

    return report
