"""Alembic 환경 설정.

- DB URL 은 alembic.ini 가 아니라 HANAAI_DATABASE_URL 환경변수에서 읽는다.
- 이 프로젝트의 스키마 원본은 db/migrations/sql/*.sql 이다. autogenerate 는 쓰지 않는다.
  (event 테이블이 파티션 테이블이라 autogenerate 가 올바른 DDL 을 만들지 못한다.)
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 리포 루트와 마이그레이션 디렉터리를 import 경로에 추가한다.
# 후자는 버전 스크립트가 sqlfiles 헬퍼를 import 할 수 있게 하기 위함이다.
_MIGRATIONS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _MIGRATIONS_DIR.parents[1]
for _p in (str(_REPO_ROOT), str(_MIGRATIONS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.common.config import get_settings  # noqa: E402
from services.common.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB 연결 없이 SQL 만 출력한다 (alembic upgrade head --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
