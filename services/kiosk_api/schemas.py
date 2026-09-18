from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class AskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kiosk_serial: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=500)
    session_id: uuid.UUID | None = None
    input_mode: str = Field(default="touch", pattern="^(touch|voice)$")


class AskOut(BaseModel):
    question_id: int
    answer: str
    # 무엇이 답했는지 화면에 그대로 보여준다. 'fallback' 이 뜨는 순간이
    # 곧 콘텐츠가 없다는 뜻이고, 그게 이 프로젝트가 찾는 신호다.
    answer_source: str
    matched_menu_title: str | None
    response_ms: int
    asked_at: dt.datetime


class MenuBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    menu_id: int
    code: str
    title: str
