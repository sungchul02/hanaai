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
    # 연결어미. "지갑 분실했는데 도와줘" 가 분실물 안내와 안 맞아서 넣었다.
    "했는데",
    "하는데",
    "했습니다",
    "했어요",
    "했어",
    "해서",
    "하고",
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
    # 요청 표현. "지갑 분실했는데 도와줘" 에서 '도와줘' 가 낱말로 잡혀 평균을 끌어내렸다.
    "도와",
    "부탁",
    "해주",
    "주세",
)


# 서술어 종결 글자. 이걸로 끝나는 낱말은 주제어로 보지 않는다.
_PREDICATE_ENDINGS = frozenset("요까죠군네")


def clean(value: str) -> str:
    """문장부호를 없애고 공백을 하나로 만든다."""
    return _SPACES.sub(" ", _PUNCT.sub(" ", value)).strip()


def _strip_tail(word: str) -> str:
    for tail in _TAIL_PATTERNS:
        # 조사와 같은 기준. +1 을 두면 "분실했는데"(5) 에서 "했는데"(4) 를 못 뗀다.
        if len(word) > len(tail) and word.endswith(tail):
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


def content_token_list(value: str) -> list[str]:
    """주제를 가르는 낱말만 **나온 순서대로** 남긴다.

    유사도를 글자 단위로만 재면 말투가 같은 다른 주제가 붙어버린다. 주제는 결국 명사가 가른다.
    순서를 보존하는 이유는 head_affinity() 에 있다. 한국어 질문은 주제를 앞에 놓는다.
    """
    tokens: list[str] = []
    for word in clean(value).lower().split():
        stem = _strip_tail(word)
        if len(stem) < 2 or stem in STOPWORDS or stem in tokens:
            continue
        if any(stem.startswith(prefix) for prefix in STOPWORD_PREFIXES):
            continue
        # 서술어로 끝나면 주제어가 아니다. '있어요' '주세요' '놀까' 같은 것들.
        # 이런 말이 남아 있으면 "수유실 있어요" 와 "엘리베이터 어디 있어요" 가 붙는다.
        # 한국어 명사는 이 글자로 끝나는 일이 드물어서 규칙 하나로 대부분 걸러진다.
        if stem[-1] in _PREDICATE_ENDINGS:
            continue
        tokens.append(stem)
    return tokens


def content_tokens(value: str) -> set[str]:
    return set(content_token_list(value))


def is_filler(value: str) -> bool:
    """주제를 가리키지 않는 말인가. '어디', '알려주세요', '몇 층' 같은 것들.

    메뉴 키워드를 거를 때 쓴다. 실제로 이것 때문에 무인민원발급기 안내의 키워드에
    '어디' 가 들어갔고, "화장실 어디야" 가 그 메뉴로 답해졌다.
    질문에서 말투를 걸러내는 것만으로는 부족하다. 메뉴 쪽에 남아 있으면
    아무 질문이나 걸리는 그물이 된다.

    content_tokens() 가 비었다는 것만으로는 판단할 수 없다. 한 글자 낱말이나
    낯선 표기도 비게 되는데, 그건 말투가 아니라 그냥 짧은 것이다.
    말투라고 확신할 수 있는 근거가 있을 때만 True 를 돌려준다.
    """
    words = clean(value).lower().split()
    if not words:
        return True
    for word in words:
        stem = _strip_tail(word)
        # 한 글자는 주제를 지탱하지 못한다. '몇 층' 이 여권 안내의 키워드로 들어가서
        # "세정과 몇 층이에요" 가 여권 안내로 답해졌다. 다른 곳에서 쓰는 기준과 같다.
        if len(stem) < 2:
            continue
        if stem in STOPWORDS:
            continue
        if any(stem.startswith(prefix) for prefix in STOPWORD_PREFIXES):
            continue
        if stem and stem[-1] in _PREDICATE_ENDINGS:
            continue
        return False  # 말투로 설명되지 않는 낱말이 하나라도 있으면 주제어다
    return True


def _bigram_jaccard(left: str, right: str) -> float:
    a, b = bigrams(left), bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _common_prefix(left: str, right: str) -> int:
    length = 0
    for x, y in zip(left, right, strict=False):
        if x != y:
            break
        length += 1
    return length


# 관공서 부서 이름의 공통 접미사. '세정과' 와 '축산과' 는 글자가 하나도 안 겹치지만
# 둘 다 부서이고, 사람들은 둘을 같은 형태로 묻는다 — "X 몇 층이에요".
#
# 이게 없으면 부서 위치 질문이 전부 1건짜리로 흩어진다. 첫날 103건 중 13건이 그랬고,
# 주차 다음으로 많이 물어본 주제인데 표본 부족으로 통째로 빠졌다.
# 근거 문서에 1~7층 배치가 다 있는데도 답이 안 나왔다.
#
# '실' 은 넣지 않는다. 화장실 · 민원실 · 수유실이 전부 걸려서 서로 다른 주제가 뭉친다.
# 지금 그 셋은 제대로 갈려 있고, 합치면 더 나빠진다.
# '관' 도 넣지 않는다 — 감사관(부서)과 도서관(시설)이 섞인다.
_ORG_SUFFIX = "과"
_ORG_MIN_LEN = 3
# 같은 부류라는 것만으로 주는 점수. 같은 주제로 묶이되(임계 0.42) 확신하지는 않는 정도다.
#
# **묶기에서만 쓴다. 답변 매칭에서는 절대 쓰지 않는다.**
#   처음에 구분 없이 켰더니 "세정과 몇 층이에요?" 가 1층 안내로 답해졌다. 세정과는 6층이다.
#   1층 본문의 '청년정책과' 와 세정과가 0.5 로 걸린 것이다.
#   묶을 때는 "부서 위치를 묻는 같은 질문" 이라는 뜻이지만,
#   답할 때는 세정과가 곧 세정과여야 한다. 자신 있게 틀린 답은 답 안 하는 것보다 나쁘다.
ORG_KINSHIP = 0.5


def _same_org_kind(left: str, right: str) -> bool:
    return (
        len(left) >= _ORG_MIN_LEN
        and len(right) >= _ORG_MIN_LEN
        and left.endswith(_ORG_SUFFIX)
        and right.endswith(_ORG_SUFFIX)
    )


def token_score(left: str, right: str, kinship: bool = False) -> float:
    """낱말 두 개가 같은 말인지. 0~1.

    글자 겹침만 보면 "분실" 과 "분실물" 이 0.5 에 그친다. 한국어는 어간이 앞에 오므로
    앞부분이 같으면 같은 말일 가능성이 높다. 그 점을 반영한다.

    kinship: 같은 부류의 부서명을 닮은 것으로 볼지. **묶기에서만 켠다.**
    """
    if left == right:
        return 1.0
    score = _bigram_jaccard(left, right)
    prefix = _common_prefix(left, right)
    if prefix >= 2:
        score = max(score, prefix / max(len(left), len(right)))
    if kinship and _same_org_kind(left, right):
        score = max(score, ORG_KINSHIP)
    return score


# 앞에 놓인 낱말이 이만큼도 통하지 않으면 같은 주제로 보지 않는다.
HEAD_MIN = 0.2
HEAD_PENALTY = 0.55
HEAD_DEPTH = 1


def head_affinity(
    left: list[str], right: list[str], depth: int = HEAD_DEPTH, kinship: bool = False
) -> float:
    """두 질문의 '앞머리' 가 서로 통하는 정도.

    한국어 질문은 주제를 앞에 놓는다. "주차 무료인가요" 와 "등본 무료인가요" 는
    낱말 두 개 중 하나가 같아서 유사도가 0.5 나오지만, 주제는 정반대다.
    실제로 여권·등본·주차 질문이 한 묶음으로 뭉쳐서 엉뚱한 제안이 나왔다.

    뒤에 붙는 '무료', '얼마', '시간' 같은 말은 어느 주제에나 붙는다.
    주제를 가르는 것은 맨 앞 명사다. 그것이 통하는지만 따로 본다.

    depth 를 2 로 두면 뒤쪽의 흔한 낱말이 다시 끼어들어 판단이 무력해진다.
    '주차 무료' 와 '등본 무료' 가 '무료' 로 통해버린다. 기본값이 1 인 이유다.
    """
    if not left or not right:
        return 0.0
    heads_l, heads_r = left[:depth], right[:depth]
    return max(token_score(a, b, kinship) for a in heads_l for b in heads_r)


def _coverage(source: set[str], target: set[str], kinship: bool = False) -> float:
    return sum(max(token_score(s, t, kinship) for t in target) for s in source) / len(source)


def _pair_score(left: set[str], right: set[str], kinship: bool = False) -> float:
    return (_coverage(left, right, kinship) + _coverage(right, left, kinship)) / 2


def similarity(left: str, right: str, kinship: bool = False) -> float:
    """0~1 유사도. 클러스터링이 의존하는 유일한 함수다.

    내용어끼리 비교하되 낱말도 글자 단위로 견준다.
    '주차' 와 '주차장' 은 붙어야 하고, '화장실' 과 '주차장' 은 떨어져야 한다.
    """
    left_list, right_list = content_token_list(left), content_token_list(right)
    if not left_list or not right_list:
        # 내용어가 없으면(짧은 잡담 등) 통문장을 글자 단위로 견준다
        return _bigram_jaccard(normalize(left), normalize(right))
    return token_similarity(left_list, right_list, kinship)


def token_similarity(
    left: list[str] | set[str], right: list[str] | set[str], kinship: bool = False
) -> float:
    """이미 뽑아둔 낱말끼리 견준다. 같은 질문을 반복해서 토큰화하지 않기 위해 분리했다.

    순서 있는 목록을 주면 앞머리까지 본다. 집합을 주면 낱말 겹침만 본다.
    """
    if not left or not right:
        return 0.0
    score = _pair_score(set(left), set(right), kinship)
    ordered = isinstance(left, list) and isinstance(right, list)
    if ordered and score and head_affinity(list(left), list(right), kinship=kinship) < HEAD_MIN:
        score *= HEAD_PENALTY
    return score


def keywords(values: list[str], limit: int = 5) -> list[str]:
    """여러 질문에서 자주 나오는 낱말을 뽑는다. 메뉴 매칭 키워드로 쓴다."""
    counts: dict[str, int] = {}
    for value in values:
        for token in content_tokens(value):
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


_JAMO = re.compile(r"^[ㄱ-ㅎㅏ-ㅣ\s]+$")


def is_jamo_only(value: str) -> bool:
    """자음·모음만으로 된 입력인가. 'ㅋㅋㅋㅋ' 'ㅇㅇ' 'ㅠㅠ' 같은 것.

    길이가 충분해서 '너무 짧음' 규칙을 통과한다. 남겨두면 주제로 묶여
    LLM 이 "이게 안내할 가치가 있나" 를 판단하느라 돈을 쓴다. 판단할 것이 없다.
    """
    stripped = clean(value)
    return bool(stripped) and bool(_JAMO.match(stripped))


def is_abusive(value: str) -> bool:
    lowered = clean(value).lower().replace(" ", "")
    return any(bad in lowered for bad in ABUSIVE)


# 질문이 '무엇을' 묻는가. 시간인가 요금인가 위치인가.
#
# 메뉴를 항목별로 쪼개고 나니 이게 필요해졌다. "주차장 몇 시까지 해요?" 의 내용어는
# '주차장' 하나뿐이다. '몇', '시까지', '해요' 가 전부 걸러지기 때문이다.
# 그러면 '주차장 규모' 와 '주차장 운영시간' 이 똑같이 1.00 으로 걸려서
# 순서상 먼저 있는 쪽이 이긴다. 실제로 규모 안내가 시간 질문에 답했다.
#
# 이 말들을 내용어로 되살리면 안 된다. 어느 주제에나 붙어서 묶기가 망가진다.
# 대신 '무엇을 묻는지' 라는 별도 신호로 뽑아, 같은 주제 안에서 항목을 가를 때만 쓴다.
_INTENT_MARKERS: dict[str, tuple[str, ...]] = {
    "시간": (
        "몇시", "시까지", "시부터", "언제", "마감", "영업시간", "운영시간",
        "업무시간", "휴무", "문여", "문열", "문닫", "야간", "주말", "공휴일",
    ),
    "요금": ("얼마", "요금", "비용", "수수료", "무료", "유료", "가격", "얼마나내"),
    "위치": ("어디", "어딨", "위치", "장소", "몇층", "어느층", "가는길", "찾아가"),
    "규모": ("몇대", "대수", "면수", "규모", "몇개", "몇명", "자리"),
    "방법": ("어떻게", "방법", "절차", "준비물", "가져가", "신청", "예약"),
    "연락": ("전화", "연락처", "번호", "문의"),
}


def intents(value: str) -> set[str]:
    """이 문장이 무엇을 묻는지(또는 무엇에 대한 것인지). 없으면 빈 집합.

    공백을 없앤 뒤 본다. "몇 시까지" 와 "몇시까지" 를 따로 적을 이유가 없다.
    """
    flat = clean(value).lower().replace(" ", "")
    if not flat:
        return set()
    return {
        name for name, markers in _INTENT_MARKERS.items() if any(m in flat for m in markers)
    }
