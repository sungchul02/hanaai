"""triage - supervisor agent verdicts on question clusters

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from sqlfiles import run_sql_file

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0006_triage_up.sql")


def downgrade() -> None:
    run_sql_file("0006_triage_down.sql")
