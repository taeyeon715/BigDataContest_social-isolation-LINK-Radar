"""자원 원장 보강 수집. 공공 API로 받은 자원과 손으로 정리한 자원을 '추가 원장'으로 만든다.

실행: uv run python -m bigcontest.resources.sources   (.env 의 DATA_GO_KR_API_KEY, Decoding 키)
  1) 출처마다 collect.run_collection 으로 받는다 → 원문은 external/resources/raw/<출처>/<실행 ID>/ (git 추적 안 함),
     실행마다 수집_기록.csv 에 한 줄. 실패하면 그 출처는 이전 행을 그대로 둔다.
  2) 받은 기록 → 원장 칸 (아래 규칙) + 수동_추가자원.csv → external/resources/추가자원_원장.csv
  3) ledger.build() 가 원래 원장 745건 뒤에 붙여 복지자원_원장_v2.csv 를 다시 만든다
키 없이 다시 만들기: uv run python -m bigcontest.resources.sources --offline  (raw 에 남은 마지막 성공 원문으로)

출처 (모두 공공데이터포털, 이용허락범위 제한 없음)
- E-Gen 약국 (국립중앙의료원_전국 약국 정보 조회 서비스, 15000576)
- E-Gen 병·의원 (국립중앙의료원_전국 병·의원 찾기 서비스, 15000736): 종합병원·병원·의원만
- E-Gen 응급의료기관 (국립중앙의료원_전국 응급의료기관 정보 조회 서비스, 15000563)
- 복지로 중앙부처 (한국사회보장정보원_중앙부처복지서비스, 15090532)
- 복지로 지자체 (한국사회보장정보원_지자체복지서비스, 15108347): 서울시·강남구, 강원도·춘천시 부서 사업만

규칙
- 약국·병의원: 공휴일 진료시간(dutyTime8)을 등록한 곳만 넣는다 → holiday_open 예. 등록하지 않은 곳은 넣지 않음
  (휴일에 닫는다고 단정하지 않기 위해 '아니오'로 넣지 않음). 명절 당일 운영은 note 에 "다시 확인"
- 응급의료기관: 응급실 24시간(지정 기관) → holiday_open 예. 같은 기관이 병·의원 목록에도 있으면 응급의료기관 한 줄만
- 좌표: E-Gen 제공 위경도 (탐색용). 행정동은 ref/adm_boundary.geojson 경계로 배정, 경계 밖이면 권역 외
- 확인일(verified_date·last_verified): E-Gen 은 API 조회일, 복지로는 지자체 사업 수정일(lastModYmd)이 있으면 그 날, 없으면 조회일
- 운영 상태: 미상 (목록에 있다 ≠ 지금 운영 중. 원래 원장과 같은 기준)
- 복지로: 서비스 이름이 configs/resource_collect.yaml welfare.programs 에 맞는 사업만 (exclude 는 뺌, include_ids 는
  이름과 상관없이 넣음). subtype 도 그 규칙.
  위치가 없는 제도·사업 (resource_kind 제도·사업, delivery_channel 신청, 좌표 해당 없음). 중앙부처 사업은 두 도시에 한 줄씩
- 자격: 노인맞춤돌봄 → ELIG_CARE, 긴급복지 → ELIG_CRISIS, 에너지 → ELIG_ENERGY, 그 밖 → 복지로 생애주기 기준
- 고정 ID: 출처 앞글자 + 원천 ID (약국 EP-, 병의원 EH-, 응급 EE-, 복지로 WN-<servId>-<도시>, WL-<servId>). 다시 받아도 같음
- 새 목록에서 빠진 행은 지우지 않고 operating_status 확인 필요 (collect.run_collection 과 같은 기준)
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.resources import ledger as L
from bigcontest.resources.collect import CONFIG, Fetcher, RawPage, env_key, load_collect_config, run_collection

BASE = "https://apis.data.go.kr"
EGEN = {
    # 종류: (경로, 원장 subtype, ID 앞글자, 데이터셋 페이지)
    "pharmacy": ("/B552657/ErmctInsttInfoInqireService/getParmacyListInfoInqire", "휴일 운영 약국", "EP",
                 "https://www.data.go.kr/data/15000576/openapi.do"),
    "clinic": ("/B552657/HsptlAsembySearchService/getHsptlMdcncListInfoInqire", "휴일 운영 병·의원", "EH",
               "https://www.data.go.kr/data/15000736/openapi.do"),
    "emergency": ("/B552657/ErmctInfoInqireService/getEgytListInfoInqire", "응급의료기관", "EE",
                  "https://www.data.go.kr/data/15000563/openapi.do"),
}
WELFARE_NATIONAL = ("/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001",
                    "https://www.data.go.kr/data/15090532/openapi.do")
WELFARE_LOCAL = ("/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist",
                 "https://www.data.go.kr/data/15108347/openapi.do")
DAYS = ["월", "화", "수", "목", "금", "토", "일", "공휴일"]
EGEN_NOTE = ("공휴일 진료시간은 E-Gen 등록값. 명절 당일 운영은 응급의료포털(e-gen.or.kr) "
             "'명절 문 여는 병의원·약국'에서 다시 확인")
ER_NOTE = "응급실 24시간 (지정 응급의료기관). 경증은 휴일 운영 의원·약국을 먼저 안내"
V2_COLS = list(L.read_source().columns) if L.SRC.exists() else []


def load_config(path: Path = CONFIG) -> dict:
    cfg = load_collect_config(path)
    for k in ["supplement", "manual", "boundary"]:
        cfg[k] = ROOT / cfg[k]
    return cfg


# ------------------------------------------------------------ 행정동 배정 (경계 안 점)
def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Boundary:
    """강남·춘천 47개 행정동 경계 (vuski/admdongkor, CC BY 4.0). 점 → (adm_cd, adm_nm), 밖이면 ('', '')."""

    def __init__(self, path: Path):
        g = json.loads(Path(path).read_text(encoding="utf-8"))
        self.parts = []
        for f in g["features"]:
            p = f["properties"]
            for poly in f["geometry"]["coordinates"]:
                xs = [c[0] for c in poly[0]]
                ys = [c[1] for c in poly[0]]
                self.parts.append(((min(xs), min(ys), max(xs), max(ys)), poly, p["adm_cd"], p["adm_nm"]))

    def locate(self, lat: float, lon: float) -> tuple[str, str]:
        for (x0, y0, x1, y1), poly, cd, nm in self.parts:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_ring(lon, lat, poly[0]) \
                    and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                return cd, nm
        return "", ""

    def nearest(self, lat: float, lon: float, max_km: float = 0.3) -> tuple[str, str]:
        """경계 밖 점 → 경계선(꼭짓점)까지 max_km 안이면 가장 가까운 행정동. 경계선 위 지하상가 등 (예: 강남역)."""
        best, out = max_km, ("", "")
        k = 111.0  # 위도 1도 ≈ 111km, 경도는 cos(위도) 배
        cx = k * 0.79
        for _, poly, cd, nm in self.parts:
            for x, y in poly[0]:
                dist = ((x - lon) * cx) ** 2 + ((y - lat) * k) ** 2
                if dist ** 0.5 < best:
                    best, out = dist ** 0.5, (cd, nm)
        return out


# ------------------------------------------------------------ 받기 (공공데이터포털 XML)
def _xml_items(body: bytes, item_tag: str) -> tuple[list[dict], int]:
    root = ET.fromstring(body)
    code = root.findtext(".//resultCode")
    if code not in ("00", "0", None):
        raise RuntimeError(f"결과 코드 {code}: {root.findtext('.//resultMsg') or root.findtext('.//resultMessage')}")
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter(item_tag)]
    return items, int(root.findtext(".//totalCount") or 0)


class DataGoKrFetcher(Fetcher):
    """공공데이터포털 목록 API 하나 (pageNo·numOfRows). 키는 URL 에만 넣고 어디에도 남기지 않는다."""

    item_tag = "item"

    def __init__(self, source: str, path: str, params: dict, rows: int, key: str):
        self.source, self.path, self.params, self.rows, self._key = source, path, params, rows, key

    def fetch(self, page: int) -> RawPage:
        q = urllib.parse.urlencode({"serviceKey": self._key, "pageNo": page, "numOfRows": self.rows, **self.params})
        with urllib.request.urlopen(f"{BASE}{self.path}?{q}", timeout=90) as r:
            body = r.read()
        items, total = _xml_items(body, self.item_tag)  # 결과 코드가 오류면 예외 → 재시도
        return RawPage(body=body, ext="xml", last=page * self.rows >= total or not items)

    def parse(self, raw: RawPage) -> list[dict]:
        items, _ = _xml_items(raw.body, self.item_tag)
        key = "hpid" if self.item_tag == "item" else "servId"
        for it in items:
            it["source_record_id"] = it.get(key, "")
        return items


class WelfareFetcher(DataGoKrFetcher):
    item_tag = "servList"


def fetchers(cfg: dict, key: str) -> list[DataGoKrFetcher]:
    out: list[DataGoKrFetcher] = []
    eg, wf = cfg["egen"]["rows_per_page"], cfg["welfare"]["rows_per_page"]
    for city, c in cfg["cities"].items():
        for kind, (path, *_rest) in EGEN.items():
            out.append(DataGoKrFetcher(f"egen_{kind}_{c['code']}", path, {"Q0": c["sido"], "Q1": c["sigungu"]}, eg, key))
    out.append(WelfareFetcher("bokjiro_national", WELFARE_NATIONAL[0], {"callTp": "L", "srchKeyCode": "003"}, wf, key))
    for sido in dict.fromkeys(c["sido"] for c in cfg["cities"].values()):
        code = next(c["code"] for c in cfg["cities"].values() if c["sido"] == sido)
        out.append(WelfareFetcher(f"bokjiro_local_{code}", WELFARE_LOCAL[0], {"ctpvNm": sido}, wf, key))
    return out


def last_success(cfg: dict, source: str) -> tuple[str, list[dict]] | None:
    """수집 기록에서 그 출처의 마지막 성공 실행 → (조회일, 원문에서 다시 읽은 기록). 원문이 없으면 None."""
    log = Path(cfg["run_log"])
    if not log.exists():
        return None
    runs = pd.read_csv(log, dtype=str, keep_default_na=False)
    runs = runs[(runs["source"] == source) & (runs["status"] == "성공")]
    if runs.empty:
        return None
    run = runs.iloc[-1]
    raw_dir = ROOT / run["raw_dir"]
    if not raw_dir.exists():
        return None
    tag = "servList" if source.startswith("bokjiro") else "item"
    f = DataGoKrFetcher(source, "", {}, 0, "")
    f.item_tag = tag
    recs = []
    for p in sorted(raw_dir.glob("page*.xml")):
        recs += f.parse(RawPage(p.read_bytes(), "xml"))
    return run["started_at"][:10], recs


# ------------------------------------------------------------ 기록 → 원장 칸
def _empty_row() -> dict:
    return {c: "" for c in V2_COLS + L.NEW_COLS if c not in ("rid",)} | {"rid": ""}


def _finish(row: dict) -> dict:
    """원장 v2 칸 순서로 맞추고, 원래 원장과 같은 규칙 함수로 좌표 상태·자격 상태를 채운다."""
    row["rid"] = row["resource_id"]
    row["coordinate_status"] = L.coord_status(row)
    row["eligibility_rule_status"] = "확인됨" if row["eligibility"] in L.ELIGIBILITY_RULES else "미상"
    row["last_verified"] = row["verified_date"] if L._DATE.match(row["verified_date"]) else ""
    return {c: row.get(c, "") for c in V2_COLS + [c for c in L.NEW_COLS if c not in V2_COLS]}


def _hhmm(s: str) -> str:
    s = s.zfill(4)
    return f"{s[:2]}:{s[2:]}"


def schedule_of(it: dict) -> str:
    """dutyTime1s~8c → '월 09:00-20:00; …; 공휴일 10:00-18:00'. 시간이 없는 요일은 적지 않음."""
    out = []
    for i, day in enumerate(DAYS, 1):
        s, c = it.get(f"dutyTime{i}s", ""), it.get(f"dutyTime{i}c", "")
        if s and c:
            out.append(f"{day} {_hhmm(s)}-{_hhmm(c)}")
    return "; ".join(out)


def egen_rows(kind: str, city: str, recs: list[dict], day: str, cfg: dict, bd: Boundary,
              skip_ids: set[str] = frozenset()) -> list[dict]:
    path, subtype, prefix, page = EGEN[kind]
    rows = []
    for it in recs:
        hpid = it.get("hpid", "")
        if not hpid or hpid in skip_ids:
            continue
        holiday = bool(it.get("dutyTime8s") and it.get("dutyTime8c"))
        if kind == "clinic" and it.get("dutyDiv", "") not in cfg["egen"]["clinic_divs"]:
            continue
        if kind != "emergency" and not holiday:
            continue
        lat, lon = it.get("wgs84Lat", ""), it.get("wgs84Lon", "")
        adm_cd, adm_nm = bd.locate(float(lat), float(lon)) if lat and lon else ("", "")
        how = "좌표(원천 제공)+행정동 경계"
        if lat and not adm_cd and cfg["cities"][city]["sigungu"] in it.get("dutyAddr", ""):
            adm_cd, adm_nm = bd.nearest(float(lat), float(lon))
            how = "좌표(원천 제공)+경계 0.3km 안 가까운 행정동 (주소는 관내)"
        if kind == "emergency":
            sched, hol, note = "응급실 24시간 (지정 응급의료기관)", "예", ER_NOTE
            if it.get("dutyEmclsName"):
                note += f". 종류: {it['dutyEmclsName']}"
        else:
            sched, hol, note = schedule_of(it), "예", EGEN_NOTE
        r = _empty_row() | {
            "resource_id": f"{prefix}-{hpid}", "source_record_id": hpid, "name": it.get("dutyName", ""),
            "type": "의료", "subtype": subtype, "hub": "False", "target_stage": "전 생애",
            "eligibility": "누구나" if kind == "pharmacy" else "진료(건강보험)", "city": city,
            "address": it.get("dutyAddr", ""), "adm_cd": adm_cd, "adm_nm": adm_nm,
            "dong_method": how if adm_cd else ("경계 밖" if lat else "미배정"),
            "lat": lat, "lon": lon, "coord_source": "E-Gen 제공 위경도(WGS84)" if lat else "none",
            "phone": it.get("dutyTel1", ""), "hours": sched, "source_url": page, "verified_date": day,
            "collected_date": day, "src": f"egen_{kind}_{cfg['cities'][city]['code']}",
            "delivery_channel": "방문", "operating_status": "미상", "schedule": sched or "미상",
            "holiday_open": hol, "service_area": "누구나 이용(관할 없음)", "resource_kind": "시설", "note": note,
        }
        if kind == "clinic" and it.get("dutyDivNam"):
            r["note"] = f"{it['dutyDivNam']}. " + r["note"]
        rows.append(_finish(r))
    return rows


def _program(it: dict, cfg: dict) -> str | None:
    """서비스 이름으로 고른다 (요약까지 보면 '위안부'의 '안부'처럼 엉뚱한 것이 섞임). 고른 것이 없으면 None."""
    w = cfg["welfare"]
    name = it.get("servNm", "")
    if re.search(w["exclude"], name):
        return None
    for p in w["programs"]:
        if re.search(p["pattern"], name):
            return p["subtype"]
    return (w.get("include_ids") or {}).get(it.get("servId", ""))


def _stages(it: dict) -> list[str]:
    s = it.get("lifeArray") or it.get("lifeNmArray") or ""
    return [x.strip() for x in s.split(",") if x.strip()]


def _program_elig(subtype: str, it: dict) -> str:
    if subtype == "노인맞춤돌봄서비스":
        return L.ELIG_CARE
    if subtype == "긴급복지지원":
        return L.ELIG_CRISIS
    if subtype == "에너지 지원":
        return L.ELIG_ENERGY
    return L.program_eligibility(_stages(it))


def _ymd(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s or "") else ""


def welfare_rows(source: str, recs: list[dict], day: str, cfg: dict) -> list[dict]:
    """복지로 → 제도·사업 행. 중앙부처는 두 도시에 한 줄씩, 지자체는 시·도 본청과 강남구·춘천시 부서 사업만."""
    rows = []
    national = source == "bokjiro_national"
    for it in recs:
        sub = _program(it, cfg)
        sid = it.get("servId", "")
        if not sub or not sid:
            continue
        if national:
            targets = [(city, "전국", f"WN-{sid}-{c['code'].upper()}") for city, c in cfg["cities"].items()]
            dept = " ".join(x for x in [it.get("jurMnofNm", ""), it.get("jurOrgNm", "")] if x)
        else:
            dept = it.get("bizChrDeptNm", "")
            words = dept.split()
            targets = []
            for city, c in cfg["cities"].items():
                if it.get("ctpvNm") != c["sido"]:
                    continue
                if c["sigungu"] in words:
                    targets.append((city, f"{c['sigungu']} 전체", f"WL-{sid}"))
                elif len(words) > 1 and not re.search(r"(시|군|구)$", words[1]):  # 시·도 본청 부서
                    area = "서울시 전역" if c["sido"].startswith("서울") else "도 전체"
                    targets.append((city, area, f"WL-{sid}"))
        mod = _ymd(it.get("lastModYmd", ""))
        apply = it.get("aplyMtdNm") or ("온라인 신청 가능" if it.get("onapPsbltYn") == "Y" else "")
        note = f"담당: {dept}" + (f". 신청: {apply}" if apply else "") + ". 지원 내용·대상은 복지로 상세 안내 확인"
        for city, area, rid in targets:
            r = _empty_row() | {
                "resource_id": rid, "source_record_id": sid, "name": it.get("servNm", ""), "type": "제도·사업",
                "subtype": sub, "hub": "False", "target_stage": "·".join(_stages(it)) or "사업별",
                "eligibility": _program_elig(sub, it), "city": city, "dong_method": "비공간",
                "coord_source": "none", "phone": it.get("rprsCtadr", ""), "hours": "",
                "source_url": it.get("servDtlLink", "") or (WELFARE_NATIONAL if national else WELFARE_LOCAL)[1],
                "verified_date": mod or day, "collected_date": day, "src": source,
                "delivery_channel": "신청", "operating_status": "미상", "schedule": "미상", "holiday_open": "미상",
                "service_area": area, "resource_kind": "제도·사업", "note": note,
            }
            rows.append(_finish(r))
    return rows


def manual_rows(path: Path, bd: Boundary) -> list[dict]:
    """손으로 정리한 자원 (수동_추가자원.csv). 빈 상태 칸은 같은 규칙으로 채운다."""
    if not Path(path).exists():
        return []
    m = pd.read_csv(path, dtype=str, keep_default_na=False)
    rows = []
    for r in m.to_dict("records"):
        r = _empty_row() | r
        if r["lat"] and r["lon"] and not r["adm_cd"]:
            r["adm_cd"], r["adm_nm"] = bd.locate(float(r["lat"]), float(r["lon"]))
        for c, v in {"operating_status": "미상", "schedule": "미상", "holiday_open": "미상",
                     "coord_source": "none", "dong_method": "비공간"}.items():
            r[c] = r[c] or v
        rows.append(_finish(r))
    return rows


# ------------------------------------------------------------ 합치기
def _keep_previous(prev: pd.DataFrame, source: str, got_ids: set[str] | None) -> list[dict]:
    """이 출처의 이전 행. got_ids 가 None 이면(수집 실패) 그대로, 있으면 새 목록에서 빠진 행만 확인 필요로 남김."""
    if prev.empty:
        return []
    old = prev[prev["src"] == source]
    if got_ids is None:
        return old.to_dict("records")
    gone = old[~old["source_record_id"].isin(got_ids)].copy()
    gone["operating_status"] = "확인 필요"
    return gone.to_dict("records")


def build_supplement(cfg: dict | None = None, offline: bool = False, now: datetime | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    bd = Boundary(cfg["boundary"])
    prev = pd.read_csv(cfg["supplement"], dtype=str, keep_default_na=False) if cfg["supplement"].exists() else pd.DataFrame()
    key = "" if offline else env_key()
    if not offline and not key:
        raise SystemExit(".env 에 DATA_GO_KR_API_KEY 가 없습니다 (키 없이 다시 만들려면 --offline)")
    got: dict[str, tuple[str, list[dict]] | None] = {}
    for f in fetchers(cfg, key):
        if offline:
            got[f.source] = last_success(cfg, f.source)
        else:
            res = run_collection(pd.DataFrame(columns=["src", "source_record_id", "resource_id"]), f, [], cfg, now=now,
                                 corrections=pd.DataFrame(columns=["resource_id", "field"]))
            print(f"{f.source}: {res.run['status']} {res.run['n_records']}건 (호출 {res.run['calls']})")
            recs = [{k: v for k, v in r.items() if isinstance(v, str)} for r in res.new_records.to_dict("records")]
            got[f.source] = (res.run["started_at"][:10], recs) if res.run["status"] == "성공" else None
    rows: list[dict] = []
    for city, c in cfg["cities"].items():
        er = got.get(f"egen_emergency_{c['code']}")
        er_ids = {r["hpid"] for r in er[1]} if er else set()
        for kind in EGEN:
            src = f"egen_{kind}_{c['code']}"
            g = got.get(src)
            if g is None:
                rows += _keep_previous(prev, src, None)
                continue
            new = egen_rows(kind, city, g[1], g[0], cfg, bd, er_ids if kind == "clinic" else set())
            rows += new + _keep_previous(prev, src, {r["source_record_id"] for r in new} | {x["hpid"] for x in g[1]})
    for src in [f.source for f in fetchers(cfg, "") if f.source.startswith("bokjiro")]:
        g = got.get(src)
        if g is None:
            rows += _keep_previous(prev, src, None)
            continue
        rows += welfare_rows(src, g[1], g[0], cfg)
        rows += _keep_previous(prev, src, {x["servId"] for x in g[1]})
    rows += manual_rows(cfg["manual"], bd)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.drop_duplicates("resource_id", keep="first").reset_index(drop=True)
    return out[V2_COLS + [c for c in L.NEW_COLS if c not in V2_COLS]]


def diff_supplement(new: pd.DataFrame, old: pd.DataFrame) -> dict:
    """보강 원장 두 판 비교 (resource_id 기준). 추가·빠짐·값이 바뀐 행 수를 칸별로."""
    if old.empty or new.empty:
        return {"same": old.equals(new), "added": list(new.get("resource_id", [])), "removed": [], "changed": {}}
    a, b = new.set_index("resource_id"), old.set_index("resource_id")
    added = sorted(set(a.index) - set(b.index))
    removed = sorted(set(b.index) - set(a.index))
    both = a.index.intersection(b.index)
    changed = {}
    for c in [c for c in a.columns if c in b.columns]:
        n = int((a.loc[both, c] != b.loc[both, c]).sum())
        if n:
            changed[c] = n
    same = (not added and not removed and not changed and list(new.columns) == list(old.columns)
            and list(new["resource_id"]) == list(old["resource_id"]))
    return {"same": same, "added": added, "removed": removed, "changed": changed}


def main(argv=None):
    ap = argparse.ArgumentParser(description="자원 원장 보강 수집")
    ap.add_argument("--offline", action="store_true", help="API 를 부르지 않고 raw 의 마지막 성공 원문으로 다시 만듦")
    ap.add_argument("--check", action="store_true", help="지금 추가자원_원장.csv 와 비교만 하고 쓰지 않음")
    ap.add_argument("--write", action="store_true",
                    help="지금 파일과 달라도 새 값으로 바꿈 (기본은 같을 때만 씀. 시연 결과가 몰래 바뀌지 않게)")
    a = ap.parse_args(argv)
    cfg = load_config()
    sup = build_supplement(cfg, offline=a.offline)
    prev = pd.read_csv(cfg["supplement"], dtype=str, keep_default_na=False) if cfg["supplement"].exists() else pd.DataFrame()
    diff = diff_supplement(sup, prev)
    print(f"지금 파일과 같음: {diff['same']} (추가 {len(diff['added'])}, 빠짐 {len(diff['removed'])}, "
          f"바뀐 칸 {diff['changed']})")
    if diff["added"] or diff["removed"]:
        print("추가:", diff["added"][:20], "빠짐:", diff["removed"][:20])
    if a.check or (not diff["same"] and not a.write and not prev.empty):
        if not a.check:
            print("값이 달라 파일을 바꾸지 않았습니다 (바꾸려면 --write)")
        return diff
    sup.to_csv(cfg["supplement"], index=False, encoding="utf-8")
    print(f"{cfg['supplement'].relative_to(ROOT)}: {len(sup)}건")
    print(sup.groupby(["city", "resource_kind", "subtype"]).size().to_string())
    d = L.build()
    v = L.validate(d)
    print(f"{L.V2.relative_to(ROOT)}: {v['counts']}")
    for e in v["errors"]:
        print("오류:", e)
    for w in v["warnings"]:
        print("주의:", w)


if __name__ == "__main__":
    main()
