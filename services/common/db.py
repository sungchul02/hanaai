from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.common.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """엔진은 지연 생성한다. import 시점에 DB 가 없어도 모듈은 로드되어야 한다."""
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
        # connect_timeout 을 반드시 준다. 없으면 DB 가 죽었을 때 psycopg 가 무한 대기하고,
        # 그 순간 health check 도 같이 멈춰서 장애를 감지할 수 없게 된다.
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI 의존성. 요청 단위로 세션을 열고 닫는다."""
    with get_sessionmaker()() as session:
        yield session
