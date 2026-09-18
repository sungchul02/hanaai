"""비슷한 질문 묶기 + 기존 메뉴로 답할 수 있는지 판정.

LLM 을 쓰지 않는다. 이 계층의 존재 이유는 질문 수천 건을 주제 수십 개로 줄여서
LLM 에게 판단할 거리만 넘기는 것이다. 질문을 하나씩 LLM 에 넣으면 비용도 시간도 감당이 안 되고,
"자주 묻는다" 라는 빈도 자체를 LLM 이 셀 수도 없다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from agents.analyst import textutil

# 표현이 다양해서 0.45 는 너무 빡빡했다. "주차 가능한가요" 와 "주차비 얼마예요" 가
# 따로 놀아서 같은 주제가 여섯 조각으로 쪼개졌다.
DEFAULT_THRESHOLD = 0.42
# 대표 질문 하나가 아니라 여러 구성원과 비교한다. 대표만 보면 표현이 조금만 달라도 튕긴다.
COMPARE_MEMBERS = 6
# 전체 질문의 이 비율 이상에 나오는 낱말은 주제를 가르지 못한다고 보고 버린다.
# 손으로 불용어를 계속 늘리는 대신 말뭉치가 알려주게 한다. "있어요" 같은 말이 여기 걸린다.
# 0.2 로 잡았더니 '주차'(전체의 29퍼센트) 같은 주제어까지 날아갔다.
# 어미 규칙이 말투를 거르므로, 여기는 정말 어디에나 나오는 말만 잡는다.
CORPUS_STOPWORD_DF = 0.55


@dataclass
class QuestionItem:
    question_id: int
    text: str
    normalized: str
    answered: bool  # 기존 메뉴가 답했는가
    matched_menu_id: int | None = None


@dataclass
class Cluster:
    """같은 의도로 보이는 질문 묶음."""

    label: str
    items: list[QuestionItem] = field(default_factory=list)
    similarities: dict[int, float] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def unanswered(self) -> int:
        return sum(1 for item in self.items if not item.answered)

    @property
    def unanswered_ratio(self) -> float:
        return self.unanswered / self.size if self.size else 0.0

    @property
    def keywords(self) -> list[str]:
        return textutil.keywords([item.text for item in self.items])

    def dominant_menu_id(self) -> tuple[int | None, float]:
        """이 묶음의 질문에 실제로 답한 메뉴. 추측이 아니라 기록이다.

        커버리지를 키워드 유사도로 '추정' 했더니 실제 응답 로직과 어긋났다.
        답이 나간 기록이 있으면 그게 곧 커버리지의 정답이다.
        """
        counts: dict[int, int] = {}
        for item in self.items:
            if item.matched_menu_id is not None:
                counts[item.matched_menu_id] = counts.get(item.matched_menu_id, 0) + 1
        if not counts:
            return None, 0.0
        menu_id = max(counts, key=lambda key: counts[key])
        return menu_id, counts[menu_id] / self.size

    def samples(self, limit: int = 5) -> list[str]:
        """대표 질문. 중복 표현을 빼고 다양하게 보여준다."""
        picked: list[str] = []
        for item in self.items:
            if any(textutil.similarity(item.text, seen) > 0.85 for seen in picked):
                continue
            picked.append(item.text)
            if len(picked) >= limit:
                break
        return picked


def _tokenize(items: list[QuestionItem]) -> tuple[dict[int, list[str]], set[str]]:
    """질문별 내용어를 한 번만 뽑고, 말뭉치에서 너무 흔한 낱말을 걸러낸다.

    "있어요" 처럼 주제와 무관한데 여러 질문에 공통으로 나오는 말이 남아 있으면
    "수유실 있어요" 와 "엘리베이터 어디 있어요" 가 한 묶음이 된다. 실제로 그랬다.
    """
    tokens = {item.question_id: textutil.content_token_list(item.text) for item in items}
    frequency: Counter[str] = Counter()
    for token_list in tokens.values():
        frequency.update(set(token_list))

    total = max(len(items), 1)
    common = {word for word, count in frequency.items() if count / total >= CORPUS_STOPWORD_DF}
    # 순서를 지킨 채로 걸러낸다. 앞머리 비교가 순서에 의존한다(textutil.head_affinity).
    # 전부 흔한 낱말뿐인 질문은 원래 목록을 쓴다. 비어버리면 비교가 불가능하다.
    return {
        qid: [t for t in tl if t not in common] or tl for qid, tl in tokens.items()
    }, common


def build_clusters(
    items: list[QuestionItem], threshold: float = DEFAULT_THRESHOLD
) -> list[Cluster]:
    """탐욕적 병합. 각 질문을 가장 비슷한 기존 묶음에 넣고, 없으면 새 묶음을 만든다.

    O(n * 묶음수) 라서 수만 건까지는 충분히 빠르다. 그보다 커지면 먼저 키워드로
    후보를 좁히거나 임베딩 + 근사 최근접으로 바꿔야 한다.

    입력 순서에 결과가 의존하므로, 호출부에서 순서를 고정해 넘긴다.
    """
    tokens, _common = _tokenize(items)
    clusters: list[Cluster] = []
    for item in items:
        best: Cluster | None = None
        best_score = threshold
        for cluster in clusters:
            score = max(
                textutil.token_similarity(tokens[item.question_id], tokens[member.question_id])
                for member in cluster.items[:COMPARE_MEMBERS]
            )
            if score >= best_score:
                best, best_score = cluster, score
        if best is None:
            clusters.append(
                Cluster(label=item.text, items=[item], similarities={item.question_id: 1.0})
            )
        else:
            best.items.append(item)
            best.similarities[item.question_id] = round(best_score, 4)

    # 큰 묶음이 먼저 오게. 관리자도 AI 도 자주 묻는 것부터 본다.
    clusters.sort(key=lambda c: c.size, reverse=True)
    return clusters


@dataclass
class MenuRef:
    menu_id: int
    title: str
    keywords: list[str]
    body: str


def find_covering_menu(
    cluster: Cluster, menus: list[MenuRef], threshold: float = 0.35
) -> tuple[MenuRef | None, float]:
    """이 주제를 이미 답할 수 있는 메뉴가 있는지 본다.

    1순위는 '실제로 답이 나갔는가' 다. 기록이 있으면 추측할 이유가 없다.
    2순위로만 키워드·제목 유사도를 본다. 아직 답한 적은 없지만 내용상 겹치는 경우다.
    """
    by_id = {menu.menu_id: menu for menu in menus}
    menu_id, share = cluster.dominant_menu_id()
    if menu_id is not None and share >= 0.5 and menu_id in by_id:
        return by_id[menu_id], round(share, 4)

    best: MenuRef | None = None
    best_score = 0.0
    label = cluster.label
    cluster_keywords = set(cluster.keywords)

    for menu in menus:
        keyword_hit = len(cluster_keywords & set(menu.keywords))
        keyword_score = keyword_hit / max(len(cluster_keywords), 1)
        title_score = textutil.similarity(label, menu.title)
        score = max(keyword_score, title_score)
        if score > best_score:
            best, best_score = menu, score

    if best_score < threshold:
        return None, best_score
    return best, best_score
