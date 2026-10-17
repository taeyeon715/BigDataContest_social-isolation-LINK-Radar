"""복지자원 좌표를 도로명주소(juso.go.kr) 좌표로 바꾼다.

실행
  uv run python -m bigcontest.resources.juso collect   # API 호출 (.env 의 JUSO_SEARCH_KEY·JUSO_COORD_KEY)
  uv run python -m bigcontest.resources.juso apply     # 좌표표 → 원장 (복지자원_원장.csv·추가자원_원장.csv·수동_추가자원.csv → v2)
  uv run python -m bigcontest.resources.juso offline   # 키 없이: raw 에 남은 마지막 성공 원문으로 좌표표를 다시 만듦

받는 방법 (주소 하나마다 두 번 부름)
  1) 검색 API (addrLinkApi.do, 승인키 JUSO_SEARCH_KEY): 주소 글자 → admCd·rnMgtSn·udrtYn·buldMnnm·buldSlno
  2) 좌표제공 API (addrCoordApi.do, 승인키 JUSO_COORD_KEY): 위 다섯 값 → 건물 출입구 좌표 entX·entY (UTM-K, EPSG:5179)
  3) UTM-K → 위경도 (utmk_to_wgs84, 횡메르카토르 역변환. GRS80 과 WGS84 차이는 무시할 만큼 작음)
  원문은 external/resources/raw/juso_coord/<실행 ID>/ (git 추적 안 함), 실행마다 수집_기록.csv 에 한 줄 (collect.run_collection).

주소 → 검색어 (keyword_of): 괄호·쉼표 뒤(층·호·건물명)를 지우고 '도로명 + 건물번호'만 남긴다. 도로명이 없으면 '동·리 + 번지'.

맞추기 (choose). 보수적으로: 하나로 정해질 때만 쓴다
  - 시군구가 원장 도시(강남구·춘천시)와 같아야 함
  - 검색어에 도로명·건물번호가 있으면 도로명(rn)·본번·부번·지하 여부가 모두 같은 결과만
    지번이면 동·리 이름, 본번·부번, 산 여부가 모두 같은 결과만
  - 남은 결과의 건물 키(admCd·rnMgtSn·udrtYn·buldMnnm·buldSlno)가 하나 → '일치'
    (같은 키의 건물이 여럿이어도 좌표 API 는 한 출입구를 줌), 둘 이상 → '여러 건', 없음 → '없음'
  - '일치'가 아니면 좌표를 쓰지 않는다 (원장 좌표는 그대로 두고 coord_source 에 '도로명주소 미매칭'을 붙임)

원장에 넣는 규칙 (apply)
  - 바꾸는 행: 지오코더 좌표(ArcGIS·Photon, 그 좌표를 빌린 행)와 좌표 없는 방문형 행
  - 그대로 두는 행: 원천 자료가 준 위경도(15167096·15074350, E-Gen). 이 행들도 비교용으로 받아 둔다
  - 일치하면 lat·lon = 도로명주소 좌표, coord_source = JUSO_SOURCE, 행정동은 build_ledger.assign_dongs 규칙으로 다시 배정
  - 일치하지 않은 지오코더 행: 좌표는 그대로, coord_source 끝에 ' · 도로명주소 미매칭' (탐색용으로 남음)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.resources import ledger as L
from bigcontest.resources.collect import Fetcher, RawPage, env_key, load_collect_config, run_collection

SEARCH_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
COORD_URL = "https://business.juso.go.kr/addrlink/addrCoordApi.do"
SOURCE = "juso_coord"
TABLE = L.RES / "좌표_도로명주소.csv"
MANUAL = L.RES / "수동_추가자원.csv"
JUSO_SOURCE = "도로명주소 좌표제공 API(juso.go.kr, 건물 출입구)"
UNMATCHED = " · 도로명주소 미매칭"
SGG = {"강남": "강남구", "춘천": "춘천시"}
KEY5 = ["admCd", "rnMgtSn", "udrtYn", "buldMnnm", "buldSlno"]
TABLE_COLS = ["keyword", "city", "status", "n_results", "road_addr", "bd_mgt_sn", "adm_cd_juso", "ent_x", "ent_y",
              "lat", "lon", "collected_at"]
KEYWORD_ERRORS = {f"E{n:04d}" for n in range(5, 14)} | {"E0015"}
# 지오코더 좌표 (바꿀 대상). 원천 제공 위경도·E-Gen 은 바꾸지 않음
GEOCODER = re.compile(r"ArcGIS|Photon")

# ------------------------------------------------------------ UTM-K → 위경도
_A, _F = 6378137.0, 1 / 298.257222101  # GRS80
_E2 = 2 * _F - _F * _F
_EP2 = _E2 / (1 - _E2)
_K0, _LAT0, _LON0, _FE, _FN = 0.9996, math.radians(38.0), math.radians(127.5), 1_000_000.0, 2_000_000.0


def _meridian(phi: float) -> float:
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


def utmk_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """EPSG:5179 (UTM-K, GRS80 횡메르카토르: 중앙 경선 127.5°, 원점 위도 38°, 축척 0.9996, 1,000,000/2,000,000) → (위도, 경도).
    Snyder(1987) 역변환 식. 한반도 안에서 오차 1cm 미만 (tests/test_juso.py 가 알려진 점으로 확인)."""
    e2 = _E2
    m = _meridian(_LAT0) + (y - _FN) / _K0
    mu = m / (_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    s, c, t = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = _EP2 * c * c, t * t
    n1 = _A / math.sqrt(1 - e2 * s * s)
    r1 = _A * (1 - e2) / (1 - e2 * s * s) ** 1.5
    d = (x - _FE) / (n1 * _K0)
    lat = phi1 - (n1 * t / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * _EP2) * d ** 4 / 24
                                  + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * _EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = _LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                   + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * _EP2 + 24 * t1 ** 2) * d ** 5 / 120) / c
    return math.degrees(lat), math.degrees(lon)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(a))


# ------------------------------------------------------------ 주소 → 검색어
# 도로명: '삼성로 51길'처럼 띄어 쓴 것도 한 도로명으로 (rn 은 빈칸을 뺀 값)
_ROAD = re.compile(r"^(?P<pre>.*?)(?P<rn>[가-힣0-9·.]+(?:로|길)(?:\s*\d+번?(?:로|길))?)\s*(?P<ug>지하\s*)?(?P<mn>\d+)"
                   r"(?:\s*-\s*(?P<sn>\d+))?")
_JIBUN = re.compile(r"^(?P<pre>.*?)(?P<dong>[가-힣]+\d*(?:동|리|가))\s+(?P<san>산\s*)?(?P<mn>\d+)(?:-(?P<sn>\d+))?(?!\d|동)")


def parse_address(addr: str) -> dict:
    """주소 → {keyword, rn, mn, sn, ug}. 도로명 주소가 아니면 rn 이 빈 값."""
    s = re.sub(r"\([^)]*\)?", " ", str(addr or ""))
    s = re.split(r"[,，]", s)[0]
    s = re.sub(r"\s+", " ", s).strip()
    m = _ROAD.match(s)
    if m:
        ug = bool(m.group("ug"))
        num = m.group("mn") + (f"-{m.group('sn')}" if m.group("sn") else "")
        rn = re.sub(r"\s+", "", m.group("rn"))
        kw = f"{m.group('pre')}{rn} {'지하 ' if ug else ''}{num}"
        return {"keyword": re.sub(r"\s+", " ", kw).strip(), "rn": rn, "mn": m.group("mn"),
                "sn": m.group("sn") or "0", "ug": "1" if ug else "0"}
    m = _JIBUN.match(s)
    if m:
        kw = f"{m.group('pre')}{m.group('dong')} {'산 ' if m.group('san') else ''}{m.group('mn')}" + (
            f"-{m.group('sn')}" if m.group("sn") else "")
        return {"keyword": re.sub(r"\s+", " ", kw).strip(), "rn": "", "mn": m.group("mn"), "sn": m.group("sn") or "0",
                "ug": "", "dong": m.group("dong"), "san": "1" if m.group("san") else "0"}
    s = re.sub(r"\s\d+동\s*\d+호?$", "", s)  # 아파트 동·호 (번지가 없어 단지 이름으로 찾음)
    return {"keyword": s, "rn": "", "mn": "", "sn": "", "ug": ""}


def keyword_of(addr: str) -> str:
    return parse_address(addr)["keyword"]


def choose(results: list[dict], city: str, parsed: dict) -> tuple[str, dict | None]:
    """검색 결과 → ('일치' | '여러 건' | '없음', 고른 결과). 하나로 정해질 때만 고른다."""
    cand = [r for r in results if r.get("sggNm", "") == SGG[city]]
    if parsed.get("rn"):
        cand = [r for r in cand if r.get("rn", "").replace(" ", "") == parsed["rn"].replace(" ", "")
                and r.get("buldMnnm") == parsed["mn"] and (r.get("buldSlno") or "0") == parsed["sn"]
                and (r.get("udrtYn") or "0") == parsed["ug"]]
    elif parsed.get("dong"):  # 지번: 동·리, 본번·부번, 산 여부가 모두 같은 결과만
        cand = [r for r in cand if parsed["dong"] in (r.get("emdNm"), r.get("liNm")) and r.get("lnbrMnnm") == parsed["mn"]
                and (r.get("lnbrSlno") or "0") == parsed["sn"] and (r.get("mtYn") or "0") == parsed["san"]]
    keys = {tuple(r.get(k, "") for k in KEY5) for r in cand}
    if not keys:
        return "없음", None
    if len(keys) > 1:
        return "여러 건", None
    return "일치", cand[0]


# ------------------------------------------------------------ 받기
class JusoFetcher(Fetcher):
    """page 번째 주소 하나 = 검색 + 좌표 두 번 호출. 원문 두 개를 JSON 하나로 묶어 저장한다. 키는 URL 에만."""

    source = SOURCE

    def __init__(self, targets: list[dict], search_key: str, coord_key: str, wait_sec: float = 0.1):
        self.targets, self._sk, self._ck, self.wait = targets, search_key, coord_key, wait_sec

    def _get(self, url: str, params: dict) -> dict:
        """응답 JSON. 검색어 자체의 문제(E0005~E0013·E0015)는 결과로 돌려주고, 키·시스템 오류는 예외 (재시도)."""
        time.sleep(self.wait)
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=30) as r:
            j = json.loads(r.read().decode("utf-8"))
        code = j["results"]["common"]["errorCode"]
        if code != "0" and code not in KEYWORD_ERRORS:
            raise RuntimeError(f"juso 오류 {code}: {j['results']['common']['errorMessage']}")
        return j

    def fetch(self, page: int) -> RawPage:
        t = self.targets[page - 1]
        p = parse_address(t["keyword"])
        s = self._get(SEARCH_URL, {"confmKey": self._sk, "currentPage": 1, "countPerPage": 20, "keyword": p["keyword"],
                                   "resultType": "json"})
        _, pick = choose(s["results"].get("juso") or [], t["city"], p)
        c = None
        for _ in range(3 if pick else 0):  # 좌표 API 가 가끔 빈 결과를 줌 → 잠깐 쉬고 다시
            c = self._get(COORD_URL, {"confmKey": self._ck, **{k: pick[k] for k in KEY5}, "resultType": "json"})
            if (c["results"].get("juso") or [{}])[0].get("entX"):
                break
            time.sleep(1)
        body = {"keyword": p["keyword"], "city": t["city"], "search": s, "coord": c}
        return RawPage(body=json.dumps(body, ensure_ascii=False).encode("utf-8"), ext="json",
                       last=page >= len(self.targets))

    def parse(self, raw: RawPage) -> list[dict]:
        return [record_of(json.loads(raw.body.decode("utf-8")))]


def record_of(body: dict, collected_at: str = "") -> dict:
    """원문 묶음 하나 → 좌표표 한 줄 (원문만 보고 다시 계산하므로 raw 로 다시 만들 수 있음)."""
    p = parse_address(body["keyword"])
    res = body["search"]["results"].get("juso") or []
    status, pick = choose(res, body["city"], p)
    if body["search"]["results"]["common"]["errorCode"] != "0":
        status = "검색어 오류"
    out = {"source_record_id": body["keyword"] + "|" + body["city"], "keyword": body["keyword"], "city": body["city"],
           "status": status, "n_results": str(len(res)), "road_addr": "", "bd_mgt_sn": "", "adm_cd_juso": "",
           "ent_x": "", "ent_y": "", "lat": "", "lon": "", "collected_at": collected_at}
    if pick:
        out.update(road_addr=pick.get("roadAddr", ""), bd_mgt_sn=pick.get("bdMgtSn", ""),
                   adm_cd_juso=pick.get("admCd", ""))
        cj = ((body.get("coord") or {}).get("results") or {}).get("juso") or []
        if cj and cj[0].get("entX") and cj[0].get("entY"):
            x, y = float(cj[0]["entX"]), float(cj[0]["entY"])
            la, lo = utmk_to_wgs84(x, y)
            out.update(ent_x=f"{x:.3f}", ent_y=f"{y:.3f}", lat=f"{la:.7f}", lon=f"{lo:.7f}")
        else:
            out["status"] = "좌표 없음"
    return out


def targets_from_ledger(d: pd.DataFrame) -> list[dict]:
    """주소가 있는 행의 (검색어, 도시). 같은 검색어는 한 번만."""
    seen, out = set(), []
    for r in d[d["address"].str.strip() != ""].itertuples():
        k = (keyword_of(r.address), r.city)
        if k not in seen:
            seen.add(k)
            out.append({"keyword": k[0], "city": k[1]})
    return out


def collect(now: datetime | None = None) -> pd.DataFrame:
    cfg = load_collect_config()
    sk, ck = env_key("JUSO_SEARCH_KEY"), env_key("JUSO_COORD_KEY")
    if not sk or not ck:
        raise SystemExit(".env 에 JUSO_SEARCH_KEY·JUSO_COORD_KEY 가 없습니다 (키 없이 다시 만들려면 offline)")
    targets = targets_from_ledger(L.load())
    jc = cfg.get("juso", {})
    cfg = cfg | {"max_calls": len(targets) * (1 + cfg["max_retries"])}
    f = JusoFetcher(targets, sk, ck, jc.get("wait_sec", 0.1))
    res = run_collection(pd.DataFrame(columns=["src", "source_record_id", "resource_id"]), f, [], cfg, now=now,
                         corrections=pd.DataFrame(columns=["resource_id", "field"]))
    print(f"{SOURCE}: {res.run['status']} {res.run['n_records']}건 (호출 {res.run['calls']}, 실패 {res.run['fail']})")
    if res.run["status"] != "성공":
        raise SystemExit(f"수집 실패: {res.run['message']} (좌표표를 바꾸지 않음)")
    return offline()


def offline(cfg: dict | None = None) -> pd.DataFrame:
    """수집 기록의 마지막 성공 실행 원문 → 좌표표 (TABLE 에 씀)."""
    cfg = cfg or load_collect_config()
    runs = pd.read_csv(cfg["run_log"], dtype=str, keep_default_na=False)
    runs = runs[(runs["source"] == SOURCE) & (runs["status"] == "성공")]
    if runs.empty:
        raise SystemExit("juso_coord 성공 기록이 없습니다")
    run = runs.iloc[-1]
    raw = ROOT / run["raw_dir"]
    rows = [record_of(json.loads(p.read_text(encoding="utf-8")), run["started_at"][:10])
            for p in sorted(raw.glob("page*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem)))]
    t = pd.DataFrame(rows)[TABLE_COLS]
    t.to_csv(TABLE, index=False, encoding="utf-8")
    print(f"{TABLE.relative_to(ROOT)}: {len(t)}건, {t['status'].value_counts().to_dict()}")
    return t


def load_table(path: Path = TABLE) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ------------------------------------------------------------ 원장에 넣기
def lookup(table: pd.DataFrame) -> dict[tuple[str, str], dict]:
    return {(r["keyword"], r["city"]): r for r in table.to_dict("records")}


def replaceable(r) -> bool:
    """도로명주소 좌표로 바꿀 행: 지오코더 좌표이거나, 주소가 있는데 좌표가 없는 행."""
    if GEOCODER.search(r["coord_source"]) and "juso" not in r["coord_source"]:
        return True
    return str(r["address"]).strip() != "" and (r["lat"] == "" or r["lon"] == "")


def new_coords(d: pd.DataFrame, table: pd.DataFrame, id_col: str) -> pd.DataFrame:
    """원장 (부분)표 → id, lat, lon, coord_source (바뀐 값). 바꿀 대상이 아닌 행은 지금 값 그대로."""
    look = lookup(table)
    out = []
    for r in d.to_dict("records"):
        la, lo, cs = r["lat"], r["lon"], r["coord_source"]
        if replaceable(r):
            hit = look.get((keyword_of(r["address"]), r["city"]))
            base = cs.replace(UNMATCHED, "")
            if hit and hit["status"] == "일치" and hit["lat"]:
                la, lo, cs = hit["lat"], hit["lon"], JUSO_SOURCE
            elif GEOCODER.search(base):
                cs = base + UNMATCHED
        out.append({id_col: r[id_col], "lat": la, "lon": lo, "coord_source": cs})
    return pd.DataFrame(out)


def apply(table: pd.DataFrame | None = None, write: bool = True) -> dict:
    """좌표표 → 원래 원장 745건 (build_ledger.assign_dongs 로 행정동 다시 배정) + 보강 원장·수동 자원 → 원장 v2."""
    from bigcontest.resources import build_ledger as B
    from bigcontest.resources.sources import Boundary

    table = load_table() if table is None else table
    src = L.read_source()
    nc = new_coords(src, table, "rid")
    s2 = src.copy()
    s2[["lat", "lon", "coord_source"]] = nc[["lat", "lon", "coord_source"]].to_numpy()
    s2 = B.assign_dongs(s2)[B.COLS].astype(str)
    bd = Boundary(B.BOUNDARY)
    names = pd.read_csv(B.NAMES, dtype=str)
    cd2nm = dict(zip(names["adm_cd"], names["adm_nm"]))

    def fix_extra(df: pd.DataFrame) -> pd.DataFrame:
        """보강 원장·수동 자원: 도로명주소 좌표로 바뀐 행만 행정동을 경계로 다시 배정 (sources.manual_rows 와 같은 방식)."""
        if df.empty:
            return df
        n = new_coords(df, table, "resource_id")
        df = df.copy()
        moved = (n["coord_source"].to_numpy() == JUSO_SOURCE) & (df["coord_source"].to_numpy() != JUSO_SOURCE)
        df[["lat", "lon", "coord_source"]] = n[["lat", "lon", "coord_source"]].to_numpy()
        for i in df.index[moved]:
            cd, _ = bd.locate(float(df.at[i, "lat"]), float(df.at[i, "lon"]))
            if cd and not cd.startswith(L.CITY_PREFIX[df.at[i, "city"]]):
                cd = ""
            df.at[i, "adm_cd"], df.at[i, "adm_nm"] = cd, cd2nm.get(cd, "")
            df.at[i, "dong_method"] = "좌표(도로명주소)+행정동 경계" if cd else "경계 밖"
        if "coordinate_status" in df:
            df["coordinate_status"] = [L.coord_status(r) for r in df.to_dict("records")]
        return df

    sup = fix_extra(L.read_supplement())
    man = fix_extra(pd.read_csv(MANUAL, dtype=str, keep_default_na=False)) if MANUAL.exists() else None
    if write:
        s2.to_csv(L.SRC, index=False, encoding="utf-8-sig" if _has_bom(L.SRC) else "utf-8")
        if len(sup):
            sup.to_csv(L.SUPPLEMENT, index=False, encoding="utf-8-sig" if _has_bom(L.SUPPLEMENT) else "utf-8")
        if man is not None:
            man.to_csv(MANUAL, index=False, encoding="utf-8-sig" if _has_bom(MANUAL) else "utf-8")
        d = L.build()
    else:
        d = None
    return {"src": s2, "supplement": sup, "manual": man, "v2": d}


def _has_bom(p: Path) -> bool:
    return p.exists() and p.read_bytes()[:3] == b"\xef\xbb\xbf"


def main(argv=None):
    ap = argparse.ArgumentParser(description="복지자원 좌표를 도로명주소 좌표로 바꾸기")
    ap.add_argument("step", choices=["collect", "offline", "apply"])
    a = ap.parse_args(argv)
    if a.step == "collect":
        collect()
    elif a.step == "offline":
        offline()
    else:
        out = apply()
        v = L.validate(out["v2"])
        print(f"{L.V2.relative_to(ROOT)}: {v['counts']}, 오류 {v['errors'] or '없음'}")
        print(out["v2"]["coordinate_status"].value_counts().to_string())


if __name__ == "__main__":
    main()
