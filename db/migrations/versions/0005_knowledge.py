"""knowledge — 근거 문서 저장소 (RAG)

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from sqlfiles import run_sql_file

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0005_knowledge_up.sql")


def downgrade() -> None:
    run_sql_file("0005_knowledge_down.sql")
