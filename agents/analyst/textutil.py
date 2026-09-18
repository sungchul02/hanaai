"""한국어 짧은 질문을 다루기 위한 텍스트 유틸.

형태소 분석기나 임베딩을 쓰지 않는다. 이유가 있다.
- 키오스크 질문은 "주차장 어디예요?" 처럼 짧고 구어체라 형태소 분석기가 자주 틀린다.
- 설치와 모델 파일이 필요해서 학생 프로젝트 환경에서 굴리기 무겁다.

대신 '내용어(주로 명사)' 를 뽑아 글자 단위로 견준다. 정확도가 더 필요해지면
similarity() 하나만 임베딩 기반으로 갈아끼우면 된다. 나머지 코드는 이 함수만 본다.
"""

from __future__ import annotations

import re

# 조사·어미. 단어 끝에서만 떼어낸다. 단어 중간에서 지우면 뜻이 망가진다.
_TAIL_PATTERNS = (
    "입니까",
    "인가요",
    "일까요",
    "예요",
    "이에요",
    "에요",
    "해요",
    "하죠",
    "나요",
    "가요",
    "는지",
    "은지",
    "습니까",
    "습니다",
    "인가",
)
_PARTICLES = ("은", "는", "이", "가", "을", "를", "에", "의", "도", "로", "으로", "까지", "부터")

_PUNCT = re.compile(r"[^\w가-힣]+")
_SPACES = re.compile(r"\s+")

# 명백한 쓰레기 질문. 규칙으로 거를 수 있는 것만 여기서 거른다.
# 나머지 '안내와 무관한가' 판단은 클러스터 단위로 LLM 이 한다. (analyst/runner 참조)
ABUSIVE = ("바보", "멍청", "시발", "씨발", "병신", "개새", "죽어", "꺼져")

# 질문의 '말투' 에 해당하는 낱말. 주제를 가르는 데 쓸모가 없다.
# 이게 부실하면 "화장실 위치 알려주세요" 와 "주차장 위치 알려주세요" 가 한 묶음이 된다.
# 실제로 그렇게 잘못 묶였고, 섞인 묶음은 엉뚱한 메뉴 제안으로 이어진다.
STOPWORDS = frozenset(
    {
        "좀",
        "저기",
        "여기",
        "그거",
        "이거",
        "그럼",
        "근데",
        "혹시",
        "그리고",
        "해서",
        "언제",
        "누가",
        "얼마",
        "위치",
        "하는",
        "있는",
        "없는",
        "이에",
        "일까",
    }
)

# 어미가 붙어 변형되는 요청 표현. 앞부분만 맞으면 말투로 본다.
# '알려주세요' '알려주시겠어요' '알려줄래요' 를 일일이 적을 수는 없다.
STOPWORD_PREFIXES = (
    "알려주",
    "알려줘",
    "가르쳐",
    "어디",
    "어딨",
    "어딘",
    "있나",
    "없나",
    "되나",
    "가능한",
    "궁금",
    "찾고",
    "무엇",
    "뭐예",
    "뭐죠",
    "어떻게",
    "어느",
)


# 서술어 종결 글자. 이걸로 끝나는 낱말은 주제어로 보지 않는다.
_PREDICATE_ENDINGS = frozenset("요까죠군네")


def clean(value: str) -> str:
    """문장부호를 없애고 공백을 하나로 만든다."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", value)).strip()


def _strip_tail(word: str) -> str:
    for tail in _TAIL_PATTERNS:
        if len(word) > len(tail) + 1 and word.endswith(tail):
            return word[: -len(tail)]
    for particle in _PARTICLES:
        # '시까지' 처럼 조사를 떼면 한 글자만 남는 경우도 떼야 한다.
        # 남겨두면 그게 내용어로 잡혀서 "몇 시까지 해요" 가 운영시간 메뉴와 안 맞는다.
        if len(word) > len(particle) and word.endswith(particle):
            return word[: -len(particle)]
    return word


def normalize(value: str) -> str:
    """비교·저장용 문자열. 공백을 없애고 어미·조사를 떼어낸다.

    DB 의 question_log.normalized_text 에 이 값이 들어간다.
    저장해두는 이유는 분석할 때마다 다시 계산하지 않기 위해서다.
    """
    words = [_strip_tail(word) for word in clean(value).lower().split()]
    return "".join(word for word in words if word)


def bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[i : i + 2] for i in range(len(value) - 1)}


def content_tokens(value: str) -> set[str]:
    """주제를 가르는 낱말만 남긴다.

    유사도를 글자 단위로만 재면 말투가 같은 다른 주제가 붙어버린다. 주제는 결국 명사가 가른다.
    """
    tokens: set[str] = set()
    for word in clean(value).lower().split():
        stem = _strip_tail(word)
        if len(stem) < 2 or stem in STOPWORDS:
            continue
        if any(stem.startswith(prefix) for prefix in STOPWORD_PREFIXES):
            continue
        # 서술어로 끝나면 주제어가 아니다. '있어요' '주세요' '놀까' 같은 것들.
        # 이런 말이 남아 있으면 "수유실 있어요" 와 "엘리베이터 어디 있어요" 가 붙는다.
        # 한국어 명사는 이 글자로 끝나는 일이 드물어서 규칙 하나로 대부분 걸러진다.
        if stem[-1] in _PREDICATE_ENDINGS:
            continue
        tokens.add(stem)
    return tokens


def _bigram_jaccard(left: str, right: str) -> float:
    a, b = bigrams(left), bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(left: str, right: str) -> float:
    """0~1 유사도. 클러스터링이 의존하는 유일한 함수다.

    내용어끼리 비교하되 낱말도 글자 단위로 견준다.
    '주차' 와 '주차장' 은 붙어야 하고, '화장실' 과 '주차장' 은 떨어져야 한다.
    """
    left_tokens, right_tokens = content_tokens(left), content_tokens(right)
    if not left_tokens or not right_tokens:
        # 내용어가 없으면(짧은 잡담 등) 통문장을 글자 단위로 견준다
        return _bigram_jaccard(normalize(left), normalize(right))

    def coverage(source: set[str], target: set[str]) -> float:
        return sum(max(_bigram_jaccard(s, t) for t in target) for s in source) / len(source)

    return (coverage(left_tokens, right_tokens) + coverage(right_tokens, left_tokens)) / 2


def token_similarity(left: set[str], right: set[str]) -> float:
    """이미 뽑아둔 낱말 집합끼리 견준다. 같은 질문을 반복해서 토큰화하지 않기 위해 분리했다."""
    if not left or not right:
        return 0.0

    def coverage(source: set[str], target: set[str]) -> float:
        return sum(max(_bigram_jaccard(s, t) for t in target) for s in source) / len(source)

    return (coverage(left, right) + coverage(right, left)) / 2


def keywords(values: list[str], limit: int = 5) -> list[str]:
    """여러 질문에서 자주 나오는 낱말을 뽑는다. 메뉴 매칭 키워드로 쓴다."""
    counts: dict[str, int] = {}
    for value in values:
        for token in content_tokens(value):
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def is_abusive(value: str) -> bool:
    lowered = clean(value).lower().replace(" ", "")
    return any(bad in lowered for bad in ABUSIVE)
