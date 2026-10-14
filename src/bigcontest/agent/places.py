"""질문 속 장소 읽기: 동 이름 사전(ref/adm_cd_names.csv) → 동을 먼저 찾고, 그 동으로 도시를 정한다.

- "춘천 강남동"의 "강남"을 서울 강남구로 읽지 않도록 동 이름을 먼저 찾아 지운 뒤 남은 글자에서 도시를 찾는다
- 사전: 공식 이름(역삼1동) + 흔한 줄임(역삼동 → 역삼1동·역삼2동, 일원동 → 일원본동·일원1동, 역삼 → 같은 두 동)
- 긴 이름부터 찾는다 (남산면이 남면보다 먼저). 두 글자 이름·줄임은 앞뒤가 낱말 경계일 때만 ("서면으로" 는 서면 아님)
- 한 이름이 여러 동을 가리키거나(역삼동), 동과 적힌 도시가 다르면(강남 남면) 고르지 않고 선택지를 돌려준다
- 같은 함수를 질문 해석(nodes.normalize_question), Guard 도시 검사(guard._cities_in), 이어 묻기(followup)가 쓴다
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache

from bigcontest.config import ROOT

CITY_WORDS = {"강남": ("강남구", "강남"), "춘천": ("춘천시", "춘천")}
# 두 글자 이름·줄임 뒤에 올 수 있는 말 (조사 첫 글자). 이 밖의 한글이 붙으면 다른 낱말로 본다
_PARTICLE_START = set("은는이가의에도만과와을를로쪽부까서엔")
_HANGUL = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class Dong:
    city: str
    adm_cd: str
    name: str
    kind: str  # 동 / 읍 / 면


@lru_cache
def dongs() -> tuple[Dong, ...]:
    with open(ROOT / "ref" / "adm_cd_names.csv", encoding="utf-8") as f:
        return tuple(Dong(r["city"], r["adm_cd"], r["adm_nm"], r["adm_type"]) for r in csv.DictReader(f))


@lru_cache
def by_code() -> dict[str, Dong]:
    return {d.adm_cd: d for d in dongs()}


@lru_cache
def aliases() -> dict[str, tuple[str, ...]]:
    """이름 → 행정동 코드들. 공식 이름은 한 동, 줄임은 여러 동일 수 있음 (역삼동 → 역삼1동·역삼2동)."""
    official = {d.name: (d.adm_cd,) for d in dongs()}
    short: dict[str, list[str]] = {}
    for d in dongs():
        if d.kind != "동":
            continue
        base = re.sub(r"\d+|본(?=동$)", "", d.name)  # 역삼1동 → 역삼동, 일원본동 → 일원동
        stem = base[:-1]  # 역삼동 → 역삼
        for a in {base, stem}:
            if len(a) < 2 or a in official or any(a in words for words in CITY_WORDS.values()):
                continue
            short.setdefault(a, []).append(d.adm_cd)
    return {**{k: tuple(v) for k, v in short.items()}, **official}


def _boundary_ok(text: str, i: int, j: int, strict: bool) -> bool:
    before = text[i - 1] if i > 0 else ""
    if before and _HANGUL.match(before):
        head = text[:i]
        if not any(head.endswith(w) for ws in CITY_WORDS.values() for w in ws):  # "춘천남면" 은 허용
            return False
    if not strict:
        return True
    after = text[j] if j < len(text) else ""
    return not after or not _HANGUL.match(after) or after in _PARTICLE_START


def find_dongs(text: str) -> list[tuple[str, tuple[str, ...], tuple[int, int]]]:
    """[(찾은 이름, 행정동 코드들, (시작, 끝))]. 긴 이름부터, 겹치지 않게."""
    text = text or ""
    names = sorted(aliases(), key=len, reverse=True)
    taken: list[tuple[int, int]] = []
    out = []
    for nm in names:
        strict = len(nm) <= 2 or nm not in {d.name for d in dongs()}
        for m in re.finditer(re.escape(nm), text):
            i, j = m.span()
            if any(i < b and a < j for a, b in taken) or not _boundary_ok(text, i, j, strict):
                continue
            taken.append((i, j))
            out.append((nm, aliases()[nm], (i, j)))
    return sorted(out, key=lambda x: x[2][0])


def _without(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for i, j in spans:
        chars[i:j] = [" "] * (j - i)
    return "".join(chars)


def explicit_cities(text: str, words: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """동 이름을 지운 글자에서 찾은 도시 (강남 → 춘천 순서)."""
    rest = _without(text or "", [s for _, _, s in find_dongs(text or "")])
    words = words or CITY_WORDS
    return [c for c, ws in words.items() if any(w in rest for w in ws)]


def cities_in(text: str, words: dict[str, tuple[str, ...]] | None = None) -> set[str]:
    """글에 나온 도시: 동 이름을 먼저 지운 뒤 찾은 도시 + 찾은 동의 도시 ("춘천 강남동" → {춘천})."""
    found = set(explicit_cities(text, words))
    for _, cds, _ in find_dongs(text or ""):
        found |= {by_code()[c].city for c in cds}
    return found


@dataclass
class Place:
    city: str | None = None
    adm_cds: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)  # 질문에 적힌 이름 그대로
    ambiguous: str | None = None  # 고르지 못한 이유 + 선택지 (있으면 되묻기)
    options: list[str] = field(default_factory=list)


def _label(cd: str) -> str:
    d = by_code()[cd]
    return f"{d.city} {d.name}"


def resolve_place(text: str) -> Place:
    """질문 → 도시·동. 동을 먼저 찾고 그 동으로 도시를 정한다. 고를 수 없으면 ambiguous + 선택지."""
    hits = find_dongs(text)
    explicit = explicit_cities(text)
    p = Place(names=[nm for nm, _, _ in hits])
    multi = [(nm, cds) for nm, cds, _ in hits if len(cds) > 1]
    if multi:
        nm, cds = multi[0]
        p.options = [_label(c) for c in cds]
        p.ambiguous = f"'{nm}'은 {'·'.join(by_code()[c].name for c in cds)} 중 어느 동인지 알 수 없음"
        return p
    cds = list(dict.fromkeys(c for _, cs, _ in hits for c in cs))
    dong_cities = list(dict.fromkeys(by_code()[c].city for c in cds))
    if len(dong_cities) > 1:
        p.options = [_label(c) for c in cds]
        p.ambiguous = "서로 다른 도시의 동을 한 질문에서 함께 볼 수 없음"
        return p
    if dong_cities and explicit and dong_cities[0] not in explicit:
        p.options = [_label(c) for c in cds] + [f"{c} 전체" for c in explicit]
        p.ambiguous = (f"{'·'.join(by_code()[c].name for c in cds)}은 {dong_cities[0]}에 있음. "
                       f"질문의 {'·'.join(explicit)}와 맞지 않음")
        return p
    p.adm_cds = cds
    p.city = dong_cities[0] if dong_cities else (explicit[0] if explicit else None)
    return p
