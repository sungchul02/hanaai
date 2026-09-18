"""조정값 보기·바꾸기.

임계값이 코드 스무 곳에 흩어져 있었다. 기관마다 맞는 값이 다르다 —
질문이 하루 수십 건인 곳과 수천 건인 곳이 같은 기준을 쓸 이유가 없다.

값만 돌려주지 않는다. **무엇을 바꾸는지, 올리면/내리면 어떻게 되는지**를 함께 준다.
숫자만 뿌리면 관리자가 무엇을 만지는지 모른 채 건드리게 된다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from agents.analyst import tuning
from services.common.db import get_session
from services.common.models import Customer
from services.ops_api.schemas import KnobRow, SettingsGroup, SettingsIn, SettingsOut

router = APIRouter(prefix="/v1", tags=["settings"])
DbSession = Annotated[Session, Depends(get_session)]


def _load(session: Session, customer_id: int) -> Customer:
    row = session.get(Customer, customer_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown customer: {customer_id}")
    return row


@router.get("/settings", response_model=SettingsOut)
def read(session: DbSession, customer_id: int) -> SettingsOut:
    row = _load(session, customer_id)
    saved = dict(row.tuning or {})
    current = tuning.Tuning.from_overrides(saved).as_dict()
    return SettingsOut(
        customer_id=customer_id,
        groups=[
            SettingsGroup(
                group=bucket["group"],
                knobs=[
                    KnobRow(
                        key=knob.key,
                        label=knob.label,
                        value=current[knob.key],
                        default=knob.default,
                        minimum=knob.minimum,
                        maximum=knob.maximum,
                        step=knob.step,
                        integer=knob.integer,
                        what=knob.what,
                        higher=knob.higher,
                        lower=knob.lower,
                        # 기본값에서 바뀐 것을 화면이 눈에 띄게 표시한다
                        changed=knob.key in saved,
                    )
                    for knob in bucket["knobs"]
                ],
            )
            for bucket in tuning.groups()
        ],
    )


@router.patch("/settings", response_model=SettingsOut)
def update(payload: SettingsIn, session: DbSession) -> SettingsOut:
    """값을 바꾼다. null 을 주면 그 항목만 기본값으로 되돌린다.

    범위를 벗어나거나 모르는 항목은 거절한다. 조용히 무시하면 관리자는
    바꿨다고 생각하는데 실제로는 안 바뀐 채로 남는다.
    """
    row = _load(session, payload.customer_id)
    saved = dict(row.tuning or {})

    for key, value in payload.values.items():
        knob = tuning.BY_KEY.get(key)
        if knob is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"모르는 항목이다: {key}")
        if value is None:
            saved.pop(key, None)  # 기본값으로 되돌리기
            continue
        if not (knob.minimum <= value <= knob.maximum):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{knob.label}: {knob.minimum}~{knob.maximum} 범위여야 한다 (받은 값 {value})",
            )
        saved[key] = int(value) if knob.integer else float(value)

    row.tuning = saved
    # JSONB 는 통째로 갈아끼워야 SQLAlchemy 가 바뀐 줄 안다.
    flag_modified(row, "tuning")
    session.commit()
    return read(session, payload.customer_id)


@router.post("/settings/reset", response_model=SettingsOut)
def reset(payload: SettingsIn, session: DbSession) -> SettingsOut:
    """전부 기본값으로. 이것저것 만지다 망가졌을 때 돌아올 곳이 필요하다."""
    row = _load(session, payload.customer_id)
    row.tuning = {}
    flag_modified(row, "tuning")
    session.commit()
    return read(session, payload.customer_id)
