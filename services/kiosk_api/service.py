"""가상 키오스크의 질문 응답 + 로그 적재.

답변은 승인된 CMS 콘텐츠에서만 나온다. 생성형 답변을 붙이지 않는 이유가 있다.
모르는 질문에 그럴듯하게 답해버리면 "어떤 주제에 콘텐츠가 없는가" 를 영원히 알 수 없게 되고,
그건 CMS 를 채우는 이 시스템의 목적과 정면으로 어긋난다.

매칭에는 세 단계가 있다.
  cms_menu        질문의 내용어를 메뉴가 충분히 설명한다  → 응답 완료
  low_confidence  스치듯 걸렸다                        → 답은 보여주되 공백으로도 센다
  fallback        아무것도 못 찾았다                    → 안내 불가

가운데 단계가 중요하다. "주차장 자리 얼마나 남았어" 는 '주차장' 이 들어 있다는 이유로
주차 안내(위치·요금)가 답해버렸고, 그게 응답 완료로 기록되어 '실시간 주차 대수' 라는
진짜 공백이 묻혔다. 확신이 낮으면 낮다고 남겨야 분석이 그걸 찾아낸다.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agents.analyst import textutil
from services.common.models import CmsMenu, Kiosk, QuestionLog, Site

FALLBACK_ANSWER = "죄송합니다. 해당 내용은 아직 안내해 드릴 수 없습니다. 직원에게 문의해 주세요."
WEAK_PREFIX = "정확히 일치하는 안내를 찾지 못했습니다. 관련된 안내를 보여드립니다.\n\n"

# 질문의 내용어 중 이만큼을 메뉴가 설명하면 답한 것으로 본다.
STRONG_MATCH = 0.6
# 이 아래라도 스친 흔적이 있으면 참고용으로 보여준다.
WEAK_MATCH = 0.3


def _primary_terms(menu: CmsMenu) -> set[str]:
    """이 메뉴가 '무엇에 대한' 것인지 나타내는 낱말. 제목과 키워드.

    말투 키워드는 버린다. '어디' 가 키워드에 있으면 "어디" 가 든 모든 질문이 걸린다.
    실제로 무인민원발급기 안내가 "화장실 어디야" 에 답했다.
    내용어가 없는 키워드를 통문장으로 되살리던 예비 처리가 원인이었다.
    그 예비 처리 자체는 '3층' 같은 짧은 키워드에 필요하므로 남기되, 말투만 먼저 뺀다.
    """
    terms: set[str] = set()
    for keyword in menu.keywords:
        if textutil.is_filler(keyword):
            continue
        terms |= textutil.content_tokens(keyword) or {textutil.normalize(keyword)}
    terms |= textutil.content_tokens(menu.title)
    return {term for term in terms if term}


def _body_terms(menu: CmsMenu) -> set[str]:
    """본문에 나오는 낱말. 관리자가 키워드를 빠뜨려도 본문에 답이 있으면 답이 된 것이다.

    다만 본문에는 지나가는 말이 섞인다. 화장실 안내의 "엘리베이터 옆에 있습니다" 때문에
    엘리베이터 질문에 화장실 안내가 답한 적이 있다. 그래서 본문은 보조 근거로만 쓰고,
    제목·키워드가 맞는 메뉴를 먼저 고른다.
    """
    return {term for term in textutil.content_tokens(menu.body) if term}


def _keyword_coverage(normalized_question: str, menu: CmsMenu) -> float:
    """질문 글자 중 키워드가 덮는 비율.

    한 글자 키워드('차')는 무시한다. '자동차' '차이' '기차' 어디에나 걸려서
    신호가 아니라 잡음이 된다.
    """
    if not normalized_question:
        return 0.0
    matched = max(
        (
            len(normalized)
            for keyword in menu.keywords
            if not textutil.is_filler(keyword)
            and len(normalized := textutil.normalize(keyword)) >= 2
            and normalized in normalized_question
        ),
        default=0,
    )
    return matched / len(normalized_question)


def _coverage(tokens: set[str], terms: set[str]) -> float:
    if not tokens or not terms:
        return 0.0
    return sum(
        max(textutil.token_similarity({token}, {term}) for term in terms) for token in tokens
    ) / len(tokens)


def score_menu(question: str, menu: CmsMenu) -> tuple[float, float]:
    """(전체 점수, 주제 적합도) 를 돌려준다.

    전체 점수는 "질문이 묻는 것을 이 메뉴가 얼마나 덮는가" 다. '키워드가 하나라도 걸리면
    통과' 로 두면 '주차장' 한 단어 때문에 잔여 대수 질문에까지 위치 안내가 나간다.

    주제 적합도는 제목·키워드만으로 잰다. 점수가 같을 때 어느 메뉴를 고를지 가르는 데 쓴다.
    본문에 우연히 언급된 메뉴보다 그 주제를 다루는 메뉴가 먼저다.
    """
    tokens = textutil.content_tokens(question)
    normalized = textutil.normalize(question)
    keyword_score = _keyword_coverage(normalized, menu)
    primary = _primary_terms(menu)

    if not tokens:
        # "몇 시까지 해요?" 처럼 내용어가 없는 관용 질문. 이때는 키워드가 유일한 신호다.
        if keyword_score > 0:
            return 1.0, 1.0
        title_score = textutil.similarity(question, menu.title)
        return title_score, title_score

    primary_score = _coverage(tokens, primary)
    total = max(primary_score, _coverage(tokens, primary | _body_terms(menu)), keyword_score)
    return total, primary_score


def find_answer(question: str, menus: list[CmsMenu]) -> tuple[CmsMenu | None, float, str]:
    """(메뉴, 점수, 판정) 을 돌려준다. 판정은 answer_source 값이 된다."""
    best: CmsMenu | None = None
    best_key = (0.0, 0.0)
    for menu in menus:
        key = score_menu(question, menu)
        # 전체 점수가 같으면 주제 적합도가 높은 쪽을 고른다.
        if key > best_key:
            best, best_key = menu, key
    best_score = best_key[0]

    if best is None:
        return None, 0.0, "fallback"
    if best_score >= STRONG_MATCH:
        return best, best_score, "cms_menu"

    if best_score >= WEAK_MATCH:
        return best, best_score, "low_confidence"
    return None, best_score, "fallback"


def customer_of(session: Session, kiosk: Kiosk) -> int:
    return session.execute(
        select(Site.customer_id).where(Site.site_id == kiosk.site_id)
    ).scalar_one()


def published_menus(session: Session, customer_id: int) -> list[CmsMenu]:
    return list(
        session.scalars(
            select(CmsMenu)
            .where(CmsMenu.customer_id == customer_id, CmsMenu.status == "published")
            .order_by(CmsMenu.menu_id)
        )
    )


def ask(
    session: Session,
    kiosk: Kiosk,
    question_text: str,
    session_id: uuid.UUID | None = None,
    input_mode: str = "touch",
) -> QuestionLog:
    """질문을 받아 답하고, 반드시 기록한다.

    답을 못 했거나 확신이 낮았던 기록이 오히려 제일 값지다.
    """
    started = time.perf_counter()
    menus = published_menus(session, customer_of(session, kiosk))
    menu, score, verdict = find_answer(question_text, menus)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    now = dt.datetime.now(dt.UTC)

    if verdict == "cms_menu" and menu is not None:
        answer = menu.body
    elif verdict == "low_confidence" and menu is not None:
        answer = WEAK_PREFIX + menu.body
    else:
        answer = FALLBACK_ANSWER

    entry = QuestionLog(
        asked_at=now,
        kiosk_id=kiosk.kiosk_id,
        session_id=session_id,
        question_text=question_text.strip(),
        normalized_text=textutil.normalize(question_text),
        answer_text=answer,
        answer_source=verdict,
        # 약하게 맞았을 때도 어느 메뉴였는지는 남긴다. 임계값을 조정할 때 필요하다.
        matched_menu_id=menu.menu_id if menu is not None else None,
        match_score=Decimal(str(round(score, 4))),
        response_ms=elapsed_ms,
        input_mode=input_mode,
    )
    session.add(entry)
    session.execute(update(Kiosk).where(Kiosk.kiosk_id == kiosk.kiosk_id).values(last_seen_at=now))
    session.commit()
    return entry
