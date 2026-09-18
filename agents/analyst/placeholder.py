"""'(확인 후 입력 필요)' 문장 정리.

모델에게 "다른 메뉴가 다루는 내용에는 확인 후 입력 필요를 쓰지 마라" 고 두 번 일렀고
두 번 다 지키지 않았다. 26건 중 25건이 이랬다.

    [주차장 운영시간] 평일 08시부터 18시까지입니다.
                     주차 요금과 무료 주차 시간은 (확인 후 입력 필요)입니다.
                     ^ '주차 요금' 메뉴가 같은 묶음에 있다.

왜 모델이 계속 틀리는가: 모델은 자기가 쓰는 메뉴 하나만 본다. 그 본문에 없으면
'없는 것' 으로 느낀다. 메뉴를 쪼개라고 시킨 순간 이 실수는 구조적으로 따라온다.
그래서 프롬프트로 더 애원하지 않고 여기서 확인한다.

**기준은 '근거에 있는가' 가 아니라 '옆 메뉴가 이미 다루는가' 다.**
  처음에 근거 문서 낱말로 판단했더니 33문장 중 3문장만 걸렀다.
  이런 문장은 '여부' '대수' '배치도' 같은 추상 명사로 채워지는데,
  사실을 적은 문서에는 그런 말이 안 나오기 때문이다.
  반면 옆 메뉴의 제목은 바로 그 주제어로 되어 있어 정확히 맞는다.

지울지 애매하면 남긴다. 잘못 지우면 안내가 틀리고, 잘못 남기면 관리자가 한 번 더 볼 뿐이다.
"""

from __future__ import annotations

import re

from agents.analyst import textutil

PLACEHOLDER = "확인 후 입력 필요"

# 문장 끝. 한국어 안내문은 '-니다.' 로 끝나는 것이 대부분이다.
_SENTENCE = re.compile(r"[^.!?]*[.!?]|[^.!?]+$")

# 옆 메뉴 제목의 낱말이 이 비율 이상 문장에 들어 있으면, 그 메뉴가 다루는 이야기로 본다.
# 0.6 까지 낮추면 '여권 창구 층수' 처럼 정말 문서에 없는 빈칸까지 지운다.
# 그건 관리자가 채워야 할 숙제라서 지우면 안 된다. 0.7 이 그 경계다.
TITLE_COVERED = 0.7
TOKEN_MATCH = 0.6
# 지운 뒤 본문이 이보다 짧아지면 되돌린다. 계약 최소 길이(10자)보다 넉넉히 잡는다.
MIN_BODY = 20

# 문장에서 빼고 볼 낱말. 주제가 아니라 '모른다' 를 길게 말하기 위한 껍데기다.
_IGNORE = frozenset(
    {
        "확인",
        "입력",
        "필요",
        "정보",
        "내용",
        "사항",
        "여부",
        "관련",
        "나머지",
        "자료",
        "안내",
        "청사",
    }
)

# 다른 메뉴를 가리키는 말. "2층을 제외한 나머지 층", "그 밖의 부서" 처럼
# 스스로 '여기 말고 저기' 라고 밝히는 문장이다. 저기가 실제로 있으면 지운다.
_CROSS_REFERENCE = ("나머지", "그 밖", "그밖", "이외", "그 외", "다른 층", "외 다른", "제외한")


def _sentences(body: str) -> list[str]:
    return [m.group(0) for m in _SENTENCE.finditer(body) if m.group(0).strip()]


def _subject_tokens(value: str) -> set[str]:
    """이 문장(또는 제목)이 무엇에 대해 말하는가."""
    return {t for t in textutil.content_tokens(value) if t not in _IGNORE}


def _mentions(sentence_tokens: set[str], title_tokens: set[str]) -> bool:
    """옆 메뉴의 제목이 이 문장 안에 들어 있는가."""
    if not title_tokens or not sentence_tokens:
        return False
    hits = sum(
        1
        for t in title_tokens
        if max(textutil.token_score(t, s) for s in sentence_tokens) >= TOKEN_MATCH
    )
    return hits / len(title_tokens) >= TITLE_COVERED


def clean_body(
    body: str,
    sibling_titles: list[str],
    has_siblings: bool = True,
    own_title: str = "",
) -> tuple[str, list[str]]:
    """옆 메뉴가 이미 다루는 내용을 '모른다' 고 쓴 문장을 지운다.

    sibling_titles: 같은 분석에서 함께 만들어진 다른 메뉴들의 제목.
    has_siblings:   이 메뉴가 쪼개진 묶음의 일부인가. 혼자면 교차 참조를 지우지 않는다.

    (정리된 본문, 지운 문장들) 을 돌려준다. 지운 것을 함께 돌려주는 이유는
    로그에 남겨야 이 규칙이 과하게 동작하는지 알 수 있기 때문이다.
    """
    if PLACEHOLDER not in body:
        return body, []

    titles = [_subject_tokens(t) for t in sibling_titles]
    own_intents = textutil.intents(own_title)
    sibling_intents = [textutil.intents(t) for t in sibling_titles]
    kept: list[str] = []
    dropped: list[str] = []

    for sentence in _sentences(body):
        if PLACEHOLDER not in sentence:
            kept.append(sentence)
            continue
        tokens = _subject_tokens(sentence)
        covered = any(_mentions(tokens, title) for title in titles)
        # 낱말이 안 겹쳐도 '무엇을 묻는 말인가' 로는 잡힌다.
        # "주차 가능 대수는 (확인 후 입력 필요)" 와 '주차장 규모' 는 글자가 하나도 안 겹치지만
        # 둘 다 규모를 말한다. 이 문장이 남아 있으면 대수 질문을 운영시간 메뉴가 가로챈다.
        asked = textutil.intents(sentence)
        if asked and not (asked & own_intents) and any(asked & other for other in sibling_intents):
            covered = True
        # "나머지 층은..." 처럼 스스로 다른 메뉴를 가리키는 문장. 그 메뉴가 있으면 군더더기다.
        cross = has_siblings and any(marker in sentence for marker in _CROSS_REFERENCE)
        if covered or cross:
            dropped.append(sentence.strip())
        else:
            kept.append(sentence)

    cleaned = " ".join(s.strip() for s in kept).strip()
    # 전부 지웠거나 너무 짧아졌으면 손대지 않는다. 빈 안내문보다 군더더기가 낫다.
    if len(cleaned) < MIN_BODY:
        return body, []
    return cleaned, dropped


def prune_keywords(
    title: str, keywords: list[str], sibling_titles: list[str]
) -> tuple[list[str], list[str]]:
    """옆 메뉴가 다루는 항목의 키워드를 뺀다.

    메뉴는 쪼갰는데 키워드는 안 쪼개진다. '주차장 운영시간' 메뉴에
    '주차 가능 대수', '주차요금', '카드 결제' 가 들어 있었다.
    그러면 어느 메뉴나 아무 주차 질문이나 잡아서, 쪼갠 의미가 사라진다.

    판단은 '무엇을 묻는 말인가'(textutil.intents)로 한다. 낱말 겹침으로는 안 된다 —
    '대수' 와 '규모' 는 같은 것을 가리키지만 글자가 하나도 안 겹친다.

    양쪽 의도를 모두 알 때만 손댄다. 모르면 남긴다. 키워드를 잘못 빼면
    답할 수 있는 질문을 못 잡게 되고, 그건 남겨두는 것보다 나쁘다.
    """
    own = textutil.intents(title)
    if not own:
        return keywords, []
    sibling_intents = [textutil.intents(t) for t in sibling_titles]

    kept: list[str] = []
    dropped: list[str] = []
    for keyword in keywords:
        asked = textutil.intents(keyword)
        # 의도가 어긋나고, 그 의도를 실제로 다루는 옆 메뉴가 있을 때만 뺀다
        if asked and not (asked & own) and any(asked & other for other in sibling_intents):
            dropped.append(keyword)
        else:
            kept.append(keyword)
    # 전부 빠지면 손대지 않는다. 키워드 없는 메뉴는 아무것도 못 잡는다.
    return (kept or keywords), (dropped if kept else [])
