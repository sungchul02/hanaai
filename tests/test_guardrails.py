"""가드레일 테스트.

개발 Agent 에서 LLM 과 무관하게 고정할 수 있는 유일한 부분이고, 사고를 막는 마지막 층이다.
여기가 느슨해지면 나머지 설계는 의미가 없다.
"""

from __future__ import annotations

from agents.developer.guardrails import GuardrailConfig, check_changes


def test_허용_경로_안의_변경은_통과한다() -> None:
    report = check_changes(
        "ops_backend",
        {"services/ingest/service.py": 40, "tests/test_ingest.py": 30},
    )
    assert report.ok, report.as_json()


def test_scope_밖의_경로는_막는다() -> None:
    report = check_changes(
        "config_only",
        {"config/kiosk.yaml": 5, "services/ingest/service.py": 10, "tests/x_test.py": 5},
    )
    rules = {v.rule for v in report.violations}
    assert "path_whitelist" in rules


def test_금지_경로는_scope_와_무관하게_막는다() -> None:
    report = check_changes(
        "ops_backend",
        {"db/migrations/sql/0001_initial_up.sql": 3, "tests/test_x.py": 3},
    )
    assert "forbidden_path" in {v.rule for v in report.violations}


def test_가드레일_자기_자신은_수정할_수_없다() -> None:
    report = check_changes(
        "ops_backend",
        {"agents/developer/guardrails.py": 2, "tests/test_guardrails.py": 2},
    )
    assert "forbidden_path" in {v.rule for v in report.violations}


def test_ci_설정은_수정할_수_없다() -> None:
    report = check_changes(
        "ops_backend",
        {".github/workflows/ci.yml": 1, "tests/test_x.py": 1},
    )
    assert "forbidden_path" in {v.rule for v in report.violations}


def test_변경_규모_상한() -> None:
    changed = {f"services/mod_{i}.py": 10 for i in range(25)}
    changed["tests/test_x.py"] = 5
    report = check_changes("ops_backend", changed)
    assert "max_files" in {v.rule for v in report.violations}


def test_변경_줄수_상한() -> None:
    report = check_changes(
        "ops_backend",
        {"services/big.py": 900, "tests/test_big.py": 50},
    )
    assert "max_lines" in {v.rule for v in report.violations}


def test_테스트_없는_변경은_막는다() -> None:
    report = check_changes("ops_backend", {"services/ingest/service.py": 20})
    assert "require_tests" in {v.rule for v in report.violations}


def test_테스트_요구는_끌_수_있다() -> None:
    report = check_changes(
        "ops_backend",
        {"services/ingest/service.py": 20},
        GuardrailConfig(require_tests=False),
    )
    assert report.ok


def test_윈도우_경로_구분자도_판정된다() -> None:
    report = check_changes(
        "ops_backend",
        {"agents\\developer\\guardrails.py": 2, "tests\\test_x.py": 2},
    )
    assert "forbidden_path" in {v.rule for v in report.violations}
