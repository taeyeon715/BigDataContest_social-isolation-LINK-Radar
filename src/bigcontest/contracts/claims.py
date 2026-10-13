"""주장(claim) 계약: 근거 유형별로 해도 되는 / 하면 안 되는 주장.

- 카드 지역은 두 칸: merchant_region(가맹점 시군구) + customer_residence(고객 거주 시도).
  "춘천 거주", "강남구 거주"처럼 시도보다 작은 거주지는 카드로 말할 수 없음 (G-03)
- 도시 신호를 동에 붙인 근거(is_shared_context)로 "이 동의 결제가 줄었다"고 말하지 않음
- 통신 60+를 "70대 이상"으로, 체류 인구를 "주민 수"로, 주민등록 1인세대를 "1인가구"로 부르지 않음 (G-14)
- 카드로 고립·독거를 탐지했다고 말하지 않음 (G-12), 개인 진단 문장 금지 (G-04)

Guard 의 표현 검사가 이 함수를 그대로 쓴다.
"""

import re
from dataclasses import dataclass, field

from bigcontest.agent.schemas import ToolResult
from bigcontest.contracts import grain_contract

CARD_SOURCES = {"card1", "card2"}
TEL_SOURCES = {"tel_age", "tel_time", "tel_wkdy"}

# 동 이름처럼 보이지만 동이 아닌 말
_NOT_ADM = {"활동", "행동", "이동", "변동", "자동", "공동", "운동", "노동", "연동", "출동", "가동", "작동", "감동", "아동"}


@dataclass
class ClaimCheck:
    ok: bool
    violations: list[tuple[str, str]] = field(default_factory=list)  # (코드, 설명)

    @property
    def codes(self) -> list[str]:
        return [c for c, _ in self.violations]


# (코드, Guard 규칙, 정규식, 설명, 적용 원천(None=전부))
_RULES: list[tuple[str, str, str, str, set[str] | None]] = [
    ("DIAGNOSIS", "G-04", r"고립\s*위험\s*\d+\s*점|고독사\s*(위험|예측|가능성)|고립\s*상태(이다|임)", "개인 고립·고독사 진단 문장", None),
    ("CARD_ISOLATION_DETECTION", "G-12", r"고립\s*(발생|탐지|확인|감지)|독거", "카드로 고립·독거를 탐지했다는 주장", CARD_SOURCES),
    ("ACCURACY_WITHOUT_LABEL", "G-09", r"(예측|탐지)\s*정확도|AUROC", "정답 라벨 없이 정확도 주장", None),
    ("STAY_AS_RESIDENT", "G-14", r"주민\s*(수|이|들)|거주민|거주\s*인구", "체류·활동 인구를 주민 수로 읽음", TEL_SOURCES),
    ("RANK_CORR_AS_AGREEMENT", "G-14", r"일치율|설명력", "순위상관을 일치율·설명력으로 부름", None),
    ("HOUSEHOLD_TERM", "G-14", r"1인\s*가구", "주민등록 1인세대를 1인가구로 부름", {"resident_hh", "resident_pop"}),
    ("SL_PREDICTION", "G-14", r"고립을\s*예측|독립\s*검증|춘천", "시민생활을 예측·독립 검증·춘천에 씀", {"seoul_living"}),
    ("TEL_AGE_IN_RHYTHM", "G-02", r"\d0\s*대|고령|청년|중장년", "시간대·요일 표로 특정 연령의 리듬을 말함", {"tel_time", "tel_wkdy"}),
    ("TEL_AGE_FINER_THAN_60PLUS", "G-14", r"[789]0\s*대|6[5-9]\s*세", "통신 60+를 더 잘게(70대 이상 등) 부름", {"tel_age"}),
    ("ADM_WEATHER", "G-02", r"[가-힣]+\d*동의?\s*(날씨|기온|강수|폭염)", "관측소 값을 동별 날씨로 말함", {"asos"}),
]

_RESIDENCE = re.compile(r"([가-힣]+)\s*거주")
# "거주" 앞에 와도 장소가 아닌 말. 이 말 뒤의 거주 표현은 거주지 한계를 말하는 주의 문장일 때만 통과 (G-03 오탐)
#   통과: "고객 거주지는 강원까지만 알 수 있음", "고객 거주지는 시군구·동 단위로는 알 수 없음"
#   차단: "고객 거주지는 춘천임", "고객 거주지 기준 결제가 줄었다", "고객 거주지는 강원까지만이지만 사실상 춘천"
_RESIDENT_NOUNS = {"고객", "이용자", "결제자", "소비자", "카드", "단위"}
_SIDO_LEVEL = r"시\s*[·ㆍ.]?\s*도|광역\s*시\s*[·ㆍ.]?\s*도"
_FINER_LEVEL = (r"시\s*[·ㆍ.]?\s*군\s*[·ㆍ.]?\s*구(\s*[·ㆍ.,]?\s*(읍\s*[·ㆍ.]?\s*면\s*[·ㆍ.]?\s*)?동)?"
                r"|읍\s*[·ㆍ.]?\s*면\s*[·ㆍ.]?\s*동|행정동|동")
_UNKNOWN = r"(알\s*수\s*없|모르|모름|나오지\s*않|확인할\s*수\s*없|확인되지\s*않|담기지\s*않|기록되지\s*않|없)"
_CAVEAT_HEAD = r"^\s*(지|지역|시도|정보)?\s*(의\s*)?(는|은|가|이|도)?\s*"
_HEDGE = re.compile(r"아니|넘어|사실상|실제로는|실은|아마|추정|보인다|보입니다|으로\s*보|로\s*보|대부분|거의|틀림없|분명|확실|짐작|같다|같습니다")


def residence_caveat(text: str, m: re.Match, sido: set[str]) -> tuple[str, str | None] | None:
    """장소가 아닌 말(고객 등) 뒤의 '거주'가 거주지 한계를 말하는 주의 문장인가.
    ("only", 시도 이름 또는 None) = 시도(까지)만 앎 / ("unknown", None) = 더 작은 단위·거주지는 모름 / None = 아님.
    얼버무림·다시 단정 말(사실상, 보인다, 대부분 …)이 문장에 있으면 아님."""
    if m.group(1).removesuffix("의") not in _RESIDENT_NOUNS or _HEDGE.search(text):
        return None
    after = text[m.end():]
    names = "|".join(map(re.escape, sorted(sido, key=len, reverse=True)))
    only = re.match(_CAVEAT_HEAD + rf"(?P<lvl>{_SIDO_LEVEL}|{names})\s*(\(\s*(?P<inner>[가-힣]+)\s*\))?\s*"
                    r"(단위|수준|이름)?\s*(까지만|에서만|만\s*(알|있|나|기록|담))", after)
    if only:
        name = next((x for x in (only.group("lvl"), only.group("inner")) if x in sido), None)
        bad_inner = only.group("inner") and only.group("inner") not in sido
        return None if bad_inner else ("only", name)
    finer = _CAVEAT_HEAD + rf"({_FINER_LEVEL})\s*(단위|수준)?\s*(로는|으로는|까지는|는|은)?\s*{_UNKNOWN}"
    if re.match(finer, after) or re.match(_CAVEAT_HEAD + _UNKNOWN, after):
        return ("unknown", None)
    return None


_ADM_CHANGE = re.compile(r"(이\s*동|[가-힣]+\d*[동읍면])의?\s*[^.。]*?(결제|소비|매출|활동)[^.。]*?(줄|감소|늘|증가|낮|높)")


def check_claim(text: str, source: str, evidence: ToolResult | None = None) -> ClaimCheck:
    """주장 문장 하나를 근거 원천·근거 봉투와 대조한다."""
    gc = grain_contract()
    sido = set(gc["sido"])
    out = ClaimCheck(ok=True)

    for code, _rule, pat, desc, scope in _RULES:
        if scope is not None and source not in scope:
            continue
        if re.search(pat, text):
            out.violations.append((code, desc))

    if source in CARD_SOURCES:
        for m in _RESIDENCE.finditer(text):
            place = m.group(1)
            if place.removesuffix("의") in _RESIDENT_NOUNS:
                kind = residence_caveat(text, m, sido)
                if kind is None:
                    out.violations.append(
                        ("RESIDENCE_FINER_THAN_SIDO", "카드 고객 거주지는 시도까지만: 거주지는 '시도 단위까지만 앎' 같은 "
                                                      "한계로만 쓰고 어디 사는지 단정하지 않음"))
                elif source == "card1" and kind[0] == "only":
                    out.violations.append(("RESIDENCE_NOT_IN_SOURCE", "카드 데이터1에는 고객 거주지가 없음"))
                elif kind[1] and evidence and evidence.customer_residence and kind[1] != evidence.customer_residence:
                    out.violations.append(
                        ("RESIDENCE_MISMATCH", f"근거의 거주 시도는 {evidence.customer_residence}인데 '{kind[1]}'라고 씀"))
                continue
            if place not in sido:
                out.violations.append(
                    ("RESIDENCE_FINER_THAN_SIDO", f"카드 거주지는 시도까지만: '{place} 거주'는 말할 수 없음")
                )
            elif source == "card1":
                out.violations.append(("RESIDENCE_NOT_IN_SOURCE", "카드 데이터1에는 고객 거주지가 없음"))
            elif evidence and evidence.customer_residence and place != evidence.customer_residence:
                out.violations.append(
                    ("RESIDENCE_MISMATCH", f"근거의 거주 시도는 {evidence.customer_residence}인데 '{place} 거주'라고 씀")
                )

    if source in CARD_SOURCES or (evidence is not None and evidence.is_shared_context):
        for m in _ADM_CHANGE.finditer(text):
            word = m.group(1)
            if " " not in word and word.endswith(tuple(_NOT_ADM)):  # "이 동"은 동, "이동"은 아님
                continue
            out.violations.append(
                ("ADM_LEVEL_FROM_SHARED_CONTEXT", "도시 단위 신호로 동의 변화를 말할 수 없음 (is_shared_context)")
            )
            break

    out.ok = not out.violations
    return out


def card_claim(
    customer_residence: str,
    cohort: str,
    merchant_region: str,
    category: str,
    comparison: str,
) -> str:
    """카드 데이터2 근거로 해도 되는 주장 문장 (템플릿)."""
    return f"{customer_residence} 거주 {cohort} 고객의 {merchant_region} 가맹점 {category} 결제가 비교 기준보다 {comparison}"


# 원천별 기본 목록 (반환 봉투 valid_claims / forbidden_claims 에 넣음). 설계 §2.1 표.
_CLAIM_LISTS: dict[str, tuple[list[str], list[str]]] = {
    "tel_age": (["이 생활권에 머무는 사람의 연령 구성"], ["주민 수", "70대 이상·80대 이상 (60+ 묶음)", "주민 단정"]),
    "tel_time": (["이 생활권의 리듬 유형"], ["특정 연령의 리듬"]),
    "tel_wkdy": (["이 생활권의 주말 비중"], ["특정 연령의 주말 활동"]),
    "card2": (
        ["{시도} 거주 {코호트} 고객의 {시군구} 가맹점 {업종} 결제가 비교 기준보다 낮음/높음"],
        ["춘천 거주·강남구 거주 (거주지는 시도까지만)", "행정동 단위 소비", "고립·독거 탐지"],
    ),
    "card1": (["도시 단위 코호트의 시간대 활동"], ["거주자 단정", "데이터2와 합산"]),
    "resident_pop": (["동별 인구 규모"], ["1인가구"]),
    "resident_hh": (["1인세대 비율"], ["1인가구 (개념이 다름)"]),
    "seoul_living": (
        ["통신 기반 고립 지표(상대 기준)를 부분 재현"],
        ["고립을 예측했다", "춘천 적용", "독립 검증"],
    ),
    "asos": (["{관측소} 관측소 기준 폭염일"], ["동별 날씨"]),
    "resources": (["출처·확인일이 있는 자원 후보"], ["두 도시 직접 비교"]),
}
_SHARED_FORBIDDEN = "이 동의 결제가 줄었다 (도시 신호를 동에 공유한 근거)"

# 빈칸 → 값을 모를 때 지울 말 (빈칸 뒤 낱말까지). 위에서부터 차례로 바꿈
_BLANK_DROP = (
    ("{시도} 거주 ", ""),  # 거주 시도를 모르면 거주 표현을 빼고 "고객의 …"
    ("{시군구} 가맹점", "가맹점"),
    ("{관측소} 관측소", "관측소"),
)
_BLANK = re.compile(r"\{([^{}]+)\}\s*")


def fill_claim(text: str, values: dict[str, str | None]) -> str:
    """할 수 있는 말의 빈칸({시도}·{코호트}·{시군구}·{업종}·{관측소})을 근거 값으로 채움.
    값을 모르는 빈칸은 뒤 낱말과 함께 빼서 빈칸 없는 일반 문장으로 만든다."""
    for key, val in values.items():
        if val:
            text = text.replace("{" + key + "}", str(val))
    for blank, repl in _BLANK_DROP:
        text = text.replace(blank, repl)
    return re.sub(r"\s{2,}", " ", _BLANK.sub("", text)).strip()


def claim_values(ev: ToolResult, category: str | None = None) -> dict[str, str | None]:
    """근거 봉투 → 빈칸 값. 거주는 시도(customer_residence)만 씀 (G-03). 업종은 넘겨받거나 측정 단위의 '(… 업종)'."""
    if category is None and ev.measured_grain:
        m = re.search(r"\(([^()]+?)\s*업종\)", ev.measured_grain)
        category = m.group(1) if m else None
    return {"시도": ev.customer_residence, "코호트": ev.analysis_cohort, "시군구": ev.merchant_region,
            "업종": category}


def fill_claims(ev: ToolResult, category: str | None = None) -> ToolResult:
    """근거의 valid_claims 빈칸을 채운 새 봉투 (빈칸이 없으면 그대로)."""
    if not any("{" in c for c in ev.valid_claims):
        return ev
    vals = claim_values(ev, category)
    return ev.model_copy(update={"valid_claims": list(dict.fromkeys(fill_claim(c, vals) for c in ev.valid_claims))})


def claim_lists(source: str, is_shared_context: bool = False) -> tuple[list[str], list[str]]:
    valid, forbidden = _CLAIM_LISTS[source]
    forbidden = list(forbidden)
    if is_shared_context:
        forbidden.append(_SHARED_FORBIDDEN)
    return list(valid), forbidden
