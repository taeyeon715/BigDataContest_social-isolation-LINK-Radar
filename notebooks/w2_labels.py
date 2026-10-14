"""W-2 자원 추천 평가: 정답 세트 만들기 (추천 결과를 보기 전에 실행·커밋)

실행: uv run python notebooks/w2_labels.py   (원본 데이터 없이 됨: 원장 v2 + 행정동 중심점만 씀)
결과: reports/w2_자원추천평가/정답세트.csv  (쌍 × 정답 자원 한 줄씩, 공개 원장 칸만)

정답 규칙은 configs/resource_eval.yaml 에 먼저 적었다. 여기서는 그 규칙을 원장에 그대로 적용한다.
추천 코드(resources/match.py)와 필요 유형 사전(configs/need_types.yaml)은 일부러 쓰지 않는다 (같은 코드로 정답을 만들면
평가가 자기 자신을 맞히는 꼴이 되므로). 거리는 행정동 중심점(ref/adm_cd_names.csv)에서 직선거리.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from bigcontest.config import ROOT
from bigcontest.external import haversine_km
from bigcontest.resources.ledger import load as load_ledger

CFG = yaml.safe_load((ROOT / "configs" / "resource_eval.yaml").read_text(encoding="utf-8"))
OUT = ROOT / "reports" / "w2_자원추천평가"
SPATIAL_OK = {"탐색용", "확인됨", "도로명주소"}  # 좌표가 있는 물리 거점 (권역 외·미확인 제외)


def pairs() -> pd.DataFrame:
    return pd.DataFrame(CFG["pairs"], columns=["도시", "동", "집단", "필요 유형", "원인 유형"])


def centroids() -> pd.DataFrame:
    return pd.read_csv(ROOT / "ref" / "adm_cd_names.csv", dtype={"adm_cd": str})


def function_ok(r: dict, group: str, need: str) -> bool:
    for rule in CFG["relevance"]["function"][need]:
        if "only" in rule and group not in rule["only"]:
            continue
        hit = r["subtype"] in rule.get("subtypes", []) or any(n in r["name"] for n in rule.get("names", []))
        if hit and ("holiday_open" not in rule or r["holiday_open"] == rule["holiday_open"]):
            return True
    return False


def basic_ok(r: dict, city: str, adm_cd: str, group: str, holiday: bool) -> bool:
    """지역·대상·운영·확인일 규칙 (거리 전)."""
    if r["city"] != city:
        return False
    if r["service_area"] == "관할 행정동" and r["adm_cd"] != adm_cd:
        return False
    if r["target_stage"] not in CFG["relevance"]["age"][group]:
        return False
    if r["operating_status"] == "중단" or (holiday and r["holiday_open"] == "아니오"):
        return False
    if not r["last_verified"]:
        return False
    if r["coordinate_status"] == "권역 외" and r["delivery_channel"] not in ("전화", "온라인"):
        return False
    if any(k in r["name"] for k in ("자살예방", "위기상담")):  # 위기 전화: 순위 밖
        return False
    return True


def labels_for(led: pd.DataFrame, cen: pd.DataFrame, city: str, dong: str, group: str, need: str, cause: str) -> list[dict]:
    row = cen[(cen["city"] == city) & (cen["adm_nm"] == dong)]
    assert len(row) == 1, (city, dong)
    adm, lat, lon = row["adm_cd"].iloc[0], float(row["lat"].iloc[0]), float(row["lon"].iloc[0])
    holiday = "달력 공백형" in cause
    dist = CFG["relevance"]["distance"]
    cands = []
    for r in led.to_dict("records"):
        if not (basic_ok(r, city, adm, group, holiday) and function_ok(r, group, need)):
            continue
        spatial = r["coordinate_status"] in SPATIAL_OK and r["lat"] != ""
        km = haversine_km(lat, lon, float(r["lat"]), float(r["lon"])) if spatial else np.nan
        cands.append({"resource_id": r["resource_id"], "name": r["name"], "subtype": r["subtype"],
                      "물리 거점": spatial, "거리(km)": km})
    out = [c for c in cands if not c["물리 거점"] or c["거리(km)"] <= dist["walk_km"]]
    near = [c for c in cands if c["물리 거점"] and c["거리(km)"] <= dist["walk_km"]]
    if not near:  # 5km 안에 물리 거점이 없으면 가장 가까운 3곳까지 정답
        far = sorted([c for c in cands if c["물리 거점"]], key=lambda c: c["거리(km)"])[: dist["nearest_if_none"]]
        for c in far:
            c["가까운 3곳 규칙"] = True
        out += far
    for c in out:
        c.update({"도시": city, "동": dong, "adm_cd": adm, "집단": group, "필요 유형": need, "원인 유형": cause})
    return out


def build() -> pd.DataFrame:
    led = load_ledger()
    cen = centroids()
    rows = []
    for p in pairs().itertuples(index=False):
        got = labels_for(led, cen, *p)
        if not got:  # 정답이 없는 쌍도 남김 (Recall 분모 0 → 평가에서 따로 셈)
            got = [{"도시": p[0], "동": p[1], "집단": p[2], "필요 유형": p[3], "원인 유형": p[4], "resource_id": ""}]
        rows += got
    df = pd.DataFrame(rows)
    df["거리(km)"] = df["거리(km)"].round(2)
    df["가까운 3곳 규칙"] = df.get("가까운 3곳 규칙", False).fillna(False).astype(bool)
    cols = ["도시", "동", "adm_cd", "집단", "필요 유형", "원인 유형", "resource_id", "name", "subtype", "물리 거점", "거리(km)",
            "가까운 3곳 규칙"]
    return df[cols]


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    lab = build()
    lab.to_csv(OUT / "정답세트.csv", index=False, encoding="utf-8-sig")
    n = lab[lab["resource_id"] != ""].groupby(["도시", "동", "집단", "필요 유형"]).size()
    print(f"쌍 {len(pairs())}개, 정답 {int(n.sum())}건 (쌍당 중앙값 {n.median():.0f}, 정답 없는 쌍 {len(pairs()) - len(n)}개)")
