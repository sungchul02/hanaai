"""event app_version — 이벤트 시점의 앱 버전 차원

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
    run_sql_file("0004_event_app_version_up.sql")


def downgrade() -> None:
    run_sql_file("0004_event_app_version_down.sql")
