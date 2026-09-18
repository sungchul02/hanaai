"""initial — AI 키오스크 CMS 콘텐츠 추천 시스템

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from sqlfiles import run_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0001_initial_up.sql")


def downgrade() -> None:
    run_sql_file("0001_initial_down.sql")
