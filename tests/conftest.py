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
