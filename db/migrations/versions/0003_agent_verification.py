"""agent verification — 초안 검증 결과 저장

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from sqlfiles import run_sql_file

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0003_agent_verification_up.sql")


def downgrade() -> None:
    run_sql_file("0003_agent_verification_down.sql")
