from __future__ import annotations

import os

import pytest

# 설정은 import 시점에 lru_cache 로 굳는다. 테스트 값은 그 전에 넣어야 한다.
os.environ.setdefault("HANAAI_ENV", "test")
os.environ.setdefault("HANAAI_DEVICE_TOKEN", "test-token")
os.environ.setdefault(
    "HANAAI_DATABASE_URL",
    "postgresql+psycopg://hanaai:hanaai@localhost:5432/hanaai_test",
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def db_session():
    """PostgreSQL 세션. 없으면 건너뛴다.

    DB 가 필요한 테스트가 여러 파일에 흩어져서 여기로 올렸다.
    파일마다 같은 fixture 를 다시 쓰면 어느 것이 최신인지 알 수 없게 된다.
    """
    from sqlalchemy import text as _text

    from services.common.db import get_engine, get_sessionmaker

    try:
        with get_engine().connect() as conn:
            conn.execute(_text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - 환경 문제
        pytest.skip(f"PostgreSQL 없음: {exc}")
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture(scope="module")
def temp_customer(db_session):
    """이 테스트만의 고객사. 다른 테스트 데이터와 섞이면 결과를 믿을 수 없다."""
    import uuid as _uuid

    from services.common.models import Customer

    customer = Customer(code=f"T-{_uuid.uuid4().hex[:8]}", name="테스트 고객사")
    db_session.add(customer)
    db_session.commit()
    yield customer.customer_id
    db_session.delete(customer)
    db_session.commit()
