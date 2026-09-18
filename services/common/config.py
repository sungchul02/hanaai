from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 HANAAI_* 로 주입된다. .env 파일도 읽는다."""

    model_config = SettingsConfigDict(
        env_prefix="HANAAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://hanaai:hanaai@localhost:5432/hanaai"
    db_connect_timeout: int = 5

    # 수집 API
    ingest_max_batch: int = 1000

    # 디바이스 인증. 스켈레톤 단계의 공유 토큰이며 운영에서는 mTLS 로 교체한다.
    device_token: str = "dev-only-token"

    # 분석 Agent 백엔드. claude-cli / claude-api / passthrough
    # 기본은 로그인된 CLI 라 별도 키가 필요 없다. 기업 배포에서는 claude-api 로 바꾼다.
    analyst_backend: str = "claude-cli"
    # 비워두면 백엔드별 기본 모델을 쓴다 (CLI: opus, API: claude-opus-5)
    analyst_model: str = ""

    @property
    def is_local(self) -> bool:
        return self.env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
