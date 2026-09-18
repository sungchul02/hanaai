"""버전 스크립트에서 sql/*.sql 파일을 실행하기 위한 헬퍼.

DDL 을 파이썬 문자열로 흩어놓지 않고 .sql 파일 하나로 읽을 수 있게 유지한다.
리뷰할 때 스키마 전체를 한눈에 보는 것이 이 구조의 목적이다.
"""

from __future__ import annotations

from pathlib import Path

from alembic import context, op

SQL_DIR = Path(__file__).resolve().parent / "sql"


def run_sql_file(name: str) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    if context.is_offline_mode():
        op.execute(sql)
    else:
        # exec_driver_sql 은 SQLAlchemy 의 바인드 파라미터 파싱을 우회한다.
        # plpgsql 의 $$ ... $$ 와 ::date 캐스트가 그대로 전달되어야 하므로 필요하다.
        op.get_bind().exec_driver_sql(sql)
