"""analysis stats — 분석 실행 내역

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from sqlfiles import run_sql_file

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0004_analysis_stats_up.sql")


def downgrade() -> None:
    run_sql_file("0004_analysis_stats_down.sql")
