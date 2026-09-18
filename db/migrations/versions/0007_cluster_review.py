"""cluster_review - admin approval gate between triage and generation

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from sqlfiles import run_sql_file

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0007_cluster_review_up.sql")


def downgrade() -> None:
    run_sql_file("0007_cluster_review_down.sql")
