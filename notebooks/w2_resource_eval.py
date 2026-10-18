"""W-2 자원 추천 평가: 작은 정답 세트로 상위 5개 충족도(k-정규화 Recall@5)·정답 전체 재현율·정밀도·Hit@5 와 부적격 추천 수

실행: uv run python notebooks/w2_labels.py   (정답 세트, 이미 커밋돼 있으면 건너뛰어도 됨)
      uv run python notebooks/w2_resource_eval.py   (원본 데이터 없이 됨: 원장 v2 + 행정동 중심점)
결과: reports/w2_자원추천평가/ (README.md, 쌍별_결과.csv, 추천_상위5.csv)

- 정답 규칙과 평가 쌍 30개는 configs/resource_eval.yaml 에 먼저 적고, 정답 세트(정답세트.csv)를 추천 결과를 보기 전에 커밋했다.
- 추천은 에이전트 도구(agent/tools/welfare.find_resources)와 같은 함수(resources/match.select_resources)·같은 입력
  (도시 원장, 행정동 중심점, 집단, 필요 유형, 원인 유형, 상위 5)으로 낸다.
- 정답은 팀이 공개 원장 칸에 규칙을 적용해 만든 것이라, 자원이 실제로 도움이 되는지가 아니라
  "원장이 맞다면 추천이 미리 적은 기준대로 고르나"를 본다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from bigcontest.config import ROOT
from bigcontest.report import Report
from bigcontest.resources import match as M
from bigcontest.resources.ledger import load as load_ledger

CFG = yaml.safe_load((ROOT / "configs" / "resource_eval.yaml").read_text(encoding="utf-8"))
OUT = ROOT / "reports" / "w2_자원추천평가"
KK = CFG["metrics"]["k"]
AGE_OK = CFG["relevance"]["age"]
UNSURE_STAGE = {"해당 대상", "사업별"}

lab = pd.read_csv(OUT / "정답세트.csv", dtype=str, keep_default_na=False)
led = load_ledger()
cen = pd.read_csv(ROOT / "ref" / "adm_cd_names.csv", dtype={"adm_cd": str})
rows_by_id = led.set_index("resource_id")
pairs = pd.DataFrame(CFG["pairs"], columns=["도시", "동", "집단", "필요 유형", "원인 유형"])
# 지표 이름 (2026-10-14 외부 점검 K-03): 분모가 min(5, 정답 수)라 "정답의 몇 %를 찾았나"가 아니다.
# 계산은 처음부터 같고 이름만 정의를 붙였다. 정답 전체를 분모로 하는 재현율은 혼동을 막으려고 함께 적는다(사후 추가 지표).
R5 = f"k-정규화 Recall@{KK}"          # 상위 5개 충족도: 맞은 수 ÷ min(5, 정답 수)
RF = "정답 전체 재현율"                # 맞은 수 ÷ 정답 수
H5, P5 = f"Hit@{KK}", f"정밀도@{KK}"
R5_LABEL = f"상위 {KK}개 충족도 (k-정규화 Recall@{KK}, 분모 min({KK}, 정답 수))"


def ineligible_why(r, city: str, adm: str, group: str, holiday: bool) -> list[str]:
    why = []
    if r["city"] != city:
        why.append("다른 시·구")
    if r["service_area"] == "관할 행정동" and r["adm_cd"] != adm:
        why.append("관할 행정동이 아님")
    if r["target_stage"] not in AGE_OK[group] and r["target_stage"] not in UNSURE_STAGE:
        why.append(f"대상 아님({r['target_stage']})")
    if r["operating_status"] == "중단":
        why.append("운영 중단")
    if holiday and r["holiday_open"] == "아니오":
        why.append("휴일 미운영")
    return why


per, top_rows = [], []
for p in pairs.itertuples(index=False):
    city, dong, group, need, cause = p
    c = cen[(cen["city"] == city) & (cen["adm_nm"] == dong)].iloc[0]
    adm, origin = c["adm_cd"], (float(c["lat"]), float(c["lon"]))
    sub = led[led["city"] == city]
    sel = M.select_resources(sub, adm, group, need, cause, origin=origin, limit=KK, records=M.ledger_records(sub))
    gold = set(lab[(lab["도시"] == city) & (lab["동"] == dong) & (lab["집단"] == group) & (lab["필요 유형"] == need)]["resource_id"]) - {""}
    got = [m.resource_id for m in sel.ranked]
    hit = [x for x in got if x in gold]
    holiday = "달력 공백형" in cause
    inel = 0
    unsure = 0
    for rank, m in enumerate(sel.ranked, start=1):
        r = rows_by_id.loc[m.resource_id]
        why = ineligible_why(r, city, adm, group, holiday)
        inel += bool(why)
        unsure += m.eligibility == "확인 필요"
        top_rows.append({"도시": city, "동": dong, "집단": group, "필요 유형": need, "순위": rank, "resource_id": m.resource_id,
                         "이름": m.name, "분류": m.subtype, "정답": m.resource_id in gold, "부적격 사유": "; ".join(why) or "-",
                         "자격": m.eligibility or "-", "거리(km)": round(m.distance_km, 2) if m.distance_km is not None else None,
                         "전달 방식": m.delivery_channel})
    denom = min(KK, len(gold))
    per.append({"도시": city, "동": dong, "집단": group, "필요 유형": need, "원인 유형": cause, "정답 수": len(gold),
                "추천 수": len(got), "맞은 수": len(hit), R5: len(hit) / denom if denom else np.nan,
                RF: len(hit) / len(gold) if gold else np.nan,
                f"Hit@{KK}": float(len(hit) > 0) if gold else np.nan,
                f"정밀도@{KK}": len(hit) / len(got) if got else np.nan,
                "부적격 추천": inel, "자격 확인 필요 추천": unsure, "위급 시 안내(순위 밖)": len(sel.crisis)})
res = pd.DataFrame(per)
tops = pd.DataFrame(top_rows)

r = Report("w2_자원추천평가", "W-2 자원 추천 평가 (작은 정답 세트)")
r.p(f"복지자원 추천이 미리 적은 기준대로 자원을 고르는지 **(동 × 집단 × 필요 유형) {len(pairs)}쌍**으로 봅니다. "
    "정답 규칙·쌍은 [`configs/resource_eval.yaml`](../../configs/resource_eval.yaml)에, 정답 세트는 [`정답세트.csv`](정답세트.csv)에 있고 "
    "**추천 결과를 보기 전에 커밋**했습니다(규칙 → 정답 → 평가 순서, git 기록으로 확인).")
r.p()
r.p("**방법을 정직하게**")
r.p("- 정답은 팀이 공개 정보(복지자원 원장 v2 의 공개 출처 칸: 유형·대상·관할·휴일 운영·좌표·확인일)에 규칙을 적용해 만든 것입니다. "
    "담당자가 자원마다 전화·현장으로 확인한 정답이 아닙니다.")
r.p("- 정답 규칙은 추천 코드(`resources/match.py`, `configs/need_types.yaml`)를 옮겨 쓰지 않고 따로 적었습니다. 그래도 같은 원장 칸을 쓰므로 "
    "이 평가는 \"원장이 맞다면 추천이 기준에 맞게 고르나\"를 보고, 자원이 실제로 도움이 되는지는 보지 않습니다.")
r.p(f"- **{R5_LABEL}** = 상위 {KK}개 중 정답 수 ÷ min({KK}, 정답 수). 추천 칸이 {KK}개뿐이라 그 안에서 채울 수 있는 만큼 채웠나를 봅니다. "
    f"정답이 {KK}개보다 많으면 정답의 일부만 맞혀도 1이 될 수 있어 **\"정답의 몇 %를 찾았다\"는 뜻이 아닙니다.**")
r.p(f"- {RF} = 상위 {KK}개 중 정답 수 ÷ 정답 수 (정답 전체 기준). {P5} = 상위 {KK}개 중 정답 수 ÷ 추천 수. {H5} = 정답이 하나라도 든 쌍 비율.")
r.p(f"- 부적격 추천 = 상위 {KK}개 중 다른 시·구, 대상 연령 밖, 다른 관할 동, 운영 중단, 연휴인데 휴일 미운영 자원 수.")
r.p("- 이름 변경 기록: 처음 판(w2-1)은 첫 지표를 'Recall@5'라는 짧은 이름으로 적었습니다. 계산은 같고, 정답 전체 기준 재현율과 혼동되지 않도록 "
    "2026-10-14 외부 점검(K-03) 뒤 이름에 분모를 붙였습니다. 정답 전체 재현율은 이때 함께 적기 시작한 값입니다.")

r.h("1. 전체")
tot = pd.DataFrame([{
    "쌍": len(res), f"평균 {R5}": res[R5].mean(), f"평균 {RF}": res[RF].mean(), f"{H5}": res[H5].mean(), f"평균 {P5}": res[P5].mean(),
    "추천 수 합": int(res["추천 수"].sum()), "부적격 추천 합": int(res["부적격 추천"].sum()),
    "자격 확인 필요 추천 합": int(res["자격 확인 필요 추천"].sum()),
    f"{KK}개를 못 채운 쌍": int((res["추천 수"] < KK).sum())}]).set_index("쌍")
r.table(tot, fmt={f"평균 {R5}": "{:.2f}", f"평균 {RF}": "{:.2f}", H5: "{:.0%}", f"평균 {P5}": "{:.2f}", "추천 수 합": "{:.0f}", "부적격 추천 합": "{:.0f}",
                 "자격 확인 필요 추천 합": "{:.0f}", f"{KK}개를 못 채운 쌍": "{:.0f}"})
by = res.groupby(["도시", "필요 유형"]).agg(쌍=("동", "size"), recall=(R5, "mean"), full=(RF, "mean"), hit=(H5, "mean"),
                                         부적격=("부적격 추천", "sum"), 확인필요=("자격 확인 필요 추천", "sum")).reset_index()
by = by.rename(columns={"recall": f"평균 {R5}", "full": f"평균 {RF}", "hit": H5, "부적격": "부적격 추천", "확인필요": "자격 확인 필요 추천"})
r.table(by.set_index("도시"), fmt={f"평균 {R5}": "{:.2f}", f"평균 {RF}": "{:.2f}", H5: "{:.0%}"})
r.p(f"- 읽는 법: 평균 {R5}({res[R5].mean():.4f})는 \"추천 {KK}칸을 정답으로 채울 수 있는 만큼 채운 정도\"입니다. 정답 전체 기준으로는 평균 "
    f"{res[RF].mean():.4f}, 추천 중 정답 비율(정밀도)은 {res[P5].mean():.2f}, 정답이 하나라도 든 쌍은 {res[H5].mean():.0%}입니다. "
    "어느 값도 실제 연계 성공률이나 자원 이용 가능성을 뜻하지 않습니다.")

r.h("2. 쌍별 결과")
r.table(res.set_index("도시"), fmt={R5: "{:.2f}", RF: "{:.2f}", H5: "{:.0f}", P5: "{:.2f}"})
res.to_csv(OUT / "쌍별_결과.csv", index=False, encoding="utf-8-sig")
tops.to_csv(OUT / "추천_상위5.csv", index=False, encoding="utf-8-sig")

r.h("3. 정답이 아닌데 추천된 자원 (기준이 다른 곳)")
miss = tops[~tops["정답"]]
mc = miss.groupby(["필요 유형", "분류"]).size().rename("추천 수").reset_index().sort_values("추천 수", ascending=False)
if len(mc):
    r.table(mc.head(15).set_index("필요 유형"))
r.p("- 위 표는 추천 코드와 정답 규칙이 다르게 본 자원 분류입니다. 정답 규칙이 틀렸을 수도, 추천 순서가 틀렸을 수도 있습니다. "
    "어느 쪽을 고칠지는 담당자 검토로 정합니다(자동으로 고치지 않음).")
low = res[res[R5] < 0.6].sort_values(R5)
if len(low):
    r.p(f"- {R5} 0.6 미만 쌍: " + "; ".join(f"{a} {b} {c} {d} ({e:.2f})" for a, b, c, d, e in
                                             low[["도시", "동", "집단", "필요 유형", R5]].itertuples(index=False)))

r.h("4. 사후 민감도: 거리 기준을 10km로 넓히면 (사전 규칙 아님)")
r.p("결과를 본 뒤 찾은 정답 규칙의 약점입니다. 강남구 정신건강복지센터처럼 **구에 한 곳뿐인 기관**은 5km 밖이면, 5km 안에 같은 기능의 "
    "다른 자원(예: 1인가구 커뮤니티센터)이 하나라도 있을 때 정답에서 빠집니다. 사전 규칙은 바꾸지 않고, 거리 기준만 10km로 넓힌 정답으로 "
    "**참고 값**을 함께 적습니다. 본 결과는 위 1~3절(사전 규칙)입니다.")
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("w2_labels", ROOT / "notebooks" / "w2_labels.py")
W2L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W2L)
lab_now = W2L.build()  # 같은 사전 규칙을 지금 원장에 다시 적용 (원장이 커밋 뒤 바뀌었는지 확인)
W2L.CFG["relevance"]["distance"]["walk_km"] = 10.0
lab10 = W2L.build()
sens = []
for (city, dong, group, need), g in tops.groupby(["도시", "동", "집단", "필요 유형"], sort=False):
    gold10 = set(lab10[(lab10["도시"] == city) & (lab10["동"] == dong) & (lab10["집단"] == group) & (lab10["필요 유형"] == need)]["resource_id"]) - {""}
    got = list(g["resource_id"])
    den = min(KK, len(gold10))
    sens.append({"도시": city, "필요 유형": need, R5: len([x for x in got if x in gold10]) / den if den else np.nan})
sens = pd.DataFrame(sens)
cmp10 = pd.DataFrame({"사전 규칙(5km)": res.groupby(["도시", "필요 유형"])[R5].mean(),
                      "사후 민감도(10km)": sens.groupby(["도시", "필요 유형"])[R5].mean()}).reset_index()
r.table(cmp10.set_index("도시"), fmt={"사전 규칙(5km)": "{:.2f}", "사후 민감도(10km)": "{:.2f}"})
r.p(f"- 전체 평균 {R5}: 사전 규칙 {res[R5].mean():.2f} → 10km {sens[R5].mean():.2f}. 다음 판 정답 규칙에서는 "
    "'구·시에 한 곳뿐인 기관은 거리와 무관하게 정답'을 추가할지 담당자와 정합니다.")

kk_ = ["도시", "동", "집단", "필요 유형", "resource_id"]
old_set = set(map(tuple, lab[kk_].values))
now_set = set(map(tuple, lab_now[kk_].astype(str).values))
added, removed = sorted(now_set - old_set), sorted(old_set - now_set)
r.h3("원장 변경 확인")
if added or removed:
    gold_now = {}
    for t_ in now_set:
        gold_now.setdefault(t_[:4], set()).add(t_[4])
    rec_now = []
    for (city, dong, group, need), g in tops.groupby(["도시", "동", "집단", "필요 유형"], sort=False):
        gs = gold_now.get((city, dong, group, need), set()) - {""}
        den = min(KK, len(gs))
        rec_now.append(len([x for x in g["resource_id"] if x in gs]) / den if den else np.nan)
    r.p(f"정답 세트를 만든 뒤 원장이 바뀌었습니다. 같은 사전 규칙을 지금 원장에 다시 적용하면 정답이 {len(added)}건 늘고 {len(removed)}건 줄어듭니다 "
        f"({', '.join(sorted({a_[4] for a_ in added})[:5]) or '-'} 등 일부 예시). 이 정답으로 다시 재면 평균 {R5} {np.nanmean(rec_now):.2f}입니다(참고). "
        "본 결과는 커밋한 정답 세트 기준입니다.")
else:
    r.p("정답 세트를 만든 원장과 지금 원장에서 같은 규칙의 정답이 같습니다.")

r.h("5. 한계")
r.p("- 30쌍, 정답은 공개 원장 칸 기준. 운영 상태는 원장 전부 '미상'이라 운영 중단 자원은 걸러지지 않습니다(부적격 0이 '운영 확인'을 뜻하지 않음).")
r.p("- 원장 좌표는 도로명주소 건물 출입구 좌표(일부 원천 제공·탐색용)입니다. 직선거리라 5km 기준의 경계 근처는 좌표 출처에 따라 바뀔 수 있습니다.")
r.p("- 정답 규칙의 거리·기능 기준도 팀이 정한 운영 기준입니다. 현장 담당자 정답(동별 실제 연계 기관)이 생기면 그 정답으로 다시 냅니다.")
r.save()
print(tot.to_string())
print(by.to_string())
