"""W-1 자원 원장 보강

실행: uv run python notebooks/w1_resource_ledger.py   (원본 데이터 없이 돎)
결과: reports/w1_자원원장/ (README.md, 원장_구성.csv, 미상_비율.csv, 필요유형_자원수.csv, 동별_접근거리.csv)
원장 v2를 먼저 다시 만든다 (bigcontest.resources.ledger.build).
"""

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.report import Report
from bigcontest.resources import ledger
from bigcontest.resources.collect import load_corrections
from bigcontest.resources.match import access_gap, group_eligibility, load_needs, need_mask, select_resources, to_frame
from bigcontest.telecom import adm_names

d = ledger.build()
v = ledger.validate(d)
n_src = len(ledger.read_source())
base = d.iloc[:n_src]          # 원래 원장 745건 (기존 칸·순서 그대로)
sup = d.iloc[n_src:]           # 보강 자원 (resources/sources.py)
assert not v["errors"], v["errors"]
cfg = load_needs()
corr = load_corrections()
CITIES = ["강남", "춘천"]

r = Report("w1_자원원장", "W-1 자원 원장 보강")
r.p("복지자원 원장에 **상태 칸**(이용 방법, 좌표 상태, 자격 기준, 운영 상태, 운영 시간, 휴일 운영, 서비스 범위)을 붙였습니다. "
    "목적은 \"가장 가까운 시설\"이 아니라 **필요에 맞고 실제 이용 가능한 자원**을 출처와 함께 고르는 것입니다.")
r.p()
r.p("- 원본 `external/resources/복지자원_원장.csv`는 그대로 두고, 칸을 붙인 원장을 `복지자원_원장_v2.csv`로 따로 만듭니다(기존 칸·값·행 순서 동일). EDA-4 결과는 바뀌지 않습니다.")
r.p("- 칸을 채우는 규칙은 [`src/bigcontest/resources/ledger.py`](../../src/bigcontest/resources/ledger.py) 맨 위에 있습니다. 원장에 있는 칸만 보고 정하며, 알 수 없으면 **미상**으로 둡니다(추측하지 않음).")
r.p("- 손으로 고친 값은 [`정정_기록.csv`](../../external/resources/정정_기록.csv)에 새 줄로 쌓습니다(덮어쓰지 않음).")
r.p(f"- 검사 결과: 오류 {len(v['errors'])}건. 주의: {'; '.join(v['warnings']) or '없음'}")
r.p(f"- 보강 자원 **{len(sup)}건**을 원래 원장 뒤에 붙였습니다(휴일 운영 약국·병의원·응급의료기관, 복지로 제도·사업, 노인맞춤돌봄 수행기관, 사람 자원). 7절에 출처·건수, 6절에 남은 것을 적었습니다.")
r.p("- **두 도시 목록은 구성이 달라**(강남은 경로당 포함, 춘천은 행정복지센터·보건지소 포함) 도시끼리 수를 비교하지 않습니다.")

# ------------------------------------------------------------ 1. 전체 vs 공간
r.h("1. 원래 원장 745건 vs 거리 계산 738건")
v0 = ledger.validate(base)
c = v0["counts"]
r.p(f"전체 **{c['total']}건** 중 좌표가 있어 거리 계산에 쓰는 자원은 **{c['spatial']}건**, 좌표가 없는 자원은 **{c['no_coord']}건**입니다. "
    "`delivery_channel`(이용 방법)과 `coordinate_status`(좌표 상태)는 따로 정합니다. 좌표가 없다고 전화형이 되지 않습니다.")
t = pd.crosstab([base["city"], base["delivery_channel"]], base["coordinate_status"], margins=True, margins_name="합계")
t.index = [" · ".join(x for x in i if x) if isinstance(i, tuple) else i for i in t.index]
t.index.name = "도시 · 이용 방법"
r.table(t)
juso = (base["coordinate_status"] == "확인(juso)").sum()
r.p(f"- 좌표 출처가 도로명주소(juso)로 확인된 비율: **{juso}/{c['spatial']} ({juso / c['spatial']:.0%})**. 지금 좌표는 모두 탐색용(ArcGIS 732, 원천 제공 5, 주변 장소 1)입니다.")
r.p("- 이용 방법 '미상'은 모두 재가노인복지시설입니다. 방문요양(집으로 찾아옴)·주야간보호(시설에 감)·복지용구가 한 분류에 섞여 있어 이름에 단서가 없으면 정하지 않았습니다.")

r.h3("좌표 없는 7건")
nc = base[base["lat"] == ""].set_index("resource_id")
t = nc[["city", "name", "subtype", "delivery_channel", "coordinate_status", "service_area", "phone"]]
t.columns = ["도시", "이름", "원장 분류", "이용 방법", "좌표 상태", "서비스 범위", "전화"]
r.table(t)
n_phone = (nc["coordinate_status"] == "해당 없음").sum()
n_unk = (nc["coordinate_status"] == "미확인").sum()
r.p(f"- 전화형 {n_phone}건: 좌표 `해당 없음`. 거리 계산에서 빠지지만 오류가 아니고 추천에 그대로 남습니다. "
    "확인일은 공식 페이지로 다시 확인해 정정 기록으로 넣었습니다(아래 '전화형 자원 확인일').")
n_out = (nc["coordinate_status"] == "권역 외").sum()
r.p(f"- 물리 거점 {n_unk + n_out}건: 거리 계산에서 빠집니다.")
r.p(f"  - 좌표 `미확인` {n_unk}건 (R0267 더드림직업재활원): 주소가 '춘천시 중앙로'까지만 있어(번지 없음) 좌표를 못 얻었습니다. 추천에는 **\"위치 확인 필요\"**로 남습니다.")
r.p(f"  - 좌표 `권역 외` {n_out}건 (R0740 서울잇다플레이스): **강남구 밖, 위치·연락처 미확인.** 원장 분류는 '전화·광역(비공간)'이지만 물리 거점이라, 원래 칸은 두고 정정 기록으로 이용 방법·좌표 상태·서비스 범위를 고쳤습니다. 관할 밖 거점이라 강남 동 매칭에서 **추천하지 않고 최대 \"확인 필요\"**로만 나옵니다.")
r.h3("정정 기록")
r.table(corr.set_index("correction_id")[["resource_id", "field", "old_value", "new_value", "corrected_by", "corrected_at"]])
r.p("정정 기록은 덧붙이기만 합니다. 같은 칸을 여러 번 고치면 마지막 줄이 적용됩니다(C0002 → C0003).")

r.h3("전화형 자원 확인일 (공식 페이지)")
r.p("원장 확인일이 \"2026 공식 안내(재확인 필요)\"처럼 날짜가 아니면 추천에서 뺍니다(G-07). 실행한 날을 확인일로 넣지 않고, "
    "**공식 페이지에서 서비스·번호를 실제로 확인한 경우에만** 정정 기록으로 넣었습니다. 칸을 나눴습니다: "
    "`verified_url`(확인한 공식 페이지), `page_collected_at`(그 페이지를 읽은 날), `operation_confirmed_at`(페이지가 운영을 밝힌 날짜, 없으면 빈 값). "
    "운영 상태(`operating_status`)는 전화로 확인하기 전이라 그대로 미상입니다.")
ph = d.set_index("resource_id").loc[["R0739", "R0741", "R0742", "R0743", "R0744", "R0737"]]
t = ph[["name", "verified_date", "last_verified", "verified_url", "page_collected_at", "operation_confirmed_at"]].replace("", "-")
t.columns = ["이름", "원장 확인일(원래 칸)", "확인일", "확인한 공식 페이지", "페이지 확인", "운영 안내 날짜"]
r.table(t)
r.p("- R0739 외로움안녕120: 서울시 공식 안내(2026-09-23 발행)에 \"다산콜(02-120) 전화 후 5번\", \"365일 24시간\"이 있어 휴일 운영도 '예'로 고쳤습니다.")
r.p("- R0741·R0742(1577-0199), R0743·R0744(109): 보건복지부 보건복지상담센터 109 누리집에 \"24시간 자살예방상담전화 109\", \"정신건강 상담전화(1577-0199) 24시간\"이 있습니다. 원장 출처(mentalhealth.go.kr 첫 화면)에는 번호가 없어 확인한 페이지를 따로 적었습니다. 페이지에 날짜가 없어 운영 안내 날짜는 비웠습니다.")
r.p("- R0737 강남구 1인가구 커뮤니티센터: 서울시 '내 손안에 서울' 기사(2026-07-27 발행)로 주소(테헤란로8길 36, 3층)와 운영 시간(월·수·금 10~19시, 화·목 10~21시, 토 10~15시, 일 휴무)을 확인해 정정 기록으로 넣었습니다. 공휴일 운영은 기사에 없어 휴일 운영은 미상 그대로입니다.")
r.p("- 원래 원장에서 확인일이 아직 날짜가 아닌 곳: R0736 강남구보건소(수기), R0738 청소년심리지원센터 사이쉼(보도 기준), 춘천 정신건강 기관 일부('2026(분기갱신)') 등. 전화형 자원은 모두 확인했습니다.")

# ------------------------------------------------------------ 2. 구성
r.h("2. 도시 × 유형 × 이용 방법 × 좌표 상태")
comp = d.groupby(["city", "type", "delivery_channel", "coordinate_status"]).size().rename("자원 수").reset_index()
comp.to_csv(r.dir / "원장_구성.csv", index=False, encoding="utf-8-sig")
t = pd.crosstab(d["type"], [d["city"], d["delivery_channel"]])
t.columns = [f"{a} {b}" for a, b in t.columns]
t.index.name = "유형"
r.table(t)
r.p("전체 표(좌표 상태까지)는 `원장_구성.csv`에 있습니다.")

# ------------------------------------------------------------ 3. 미상 비율
r.h("3. 아직 모르는 칸 (미상 비율)")
UNKNOWN = {
    "이용 방법 (delivery_channel)": d["delivery_channel"] == "미상",
    "좌표 상태: 위치 미확인·권역 외": d["coordinate_status"].isin(["미확인", "지오코딩 실패", "권역 외"]),
    "좌표: 도로명주소 확인 전 (탐색용)": d["coordinate_status"] == "탐색용",
    "자격 기준 (eligibility_rule_status)": d["eligibility_rule_status"] == "미상",
    "운영 상태 (operating_status)": d["operating_status"].isin(["미상", "확인 필요"]),
    "운영 시간 (schedule)": d["schedule"] == "미상",
    "휴일 운영 (holiday_open)": d["holiday_open"] == "미상",
    "서비스 범위 (service_area)": d["service_area"] == "미상",
    "원천 행 ID (source_record_id)": d["source_record_id"] == "",
    "확인일이 날짜가 아님 (재확인 필요)": d["last_verified"] == "",
}
un = pd.DataFrame({k: s.groupby(d["city"]).mean() for k, s in UNKNOWN.items()}).T[CITIES]
un["전체"] = [s.mean() for s in UNKNOWN.values()]
un.index.name = "칸"
un.to_csv(r.dir / "미상_비율.csv", encoding="utf-8-sig")
r.table(un, {k: "{:.1%}" for k in un.columns})
r.p("- **운영 상태는 전부 미상**입니다. 공공 목록에 있다고 지금 운영 중이라고 볼 수 없습니다. 매칭에서는 빼지 않고 \"운영 상태 미확인\"으로 표시합니다.")
r.p("- 자격 기준 '확인됨'은 원장의 대상 문구가 구체적이라는 뜻입니다. 문구는 시설 유형별 일반 기준이라 **시설마다 확인한 값은 아닙니다.** '시설별'(장애·아동 시설 등)은 미상입니다.")
r.p("- 휴일 운영 '예'는 보강 자원(E-Gen 공휴일 진료시간을 등록한 약국·병의원, 응급의료기관)과 외로움안녕120입니다. 원래 원장에서 휴일 운영을 아는 곳은 운영 시간이 '평일'로만 적힌 곳(휴일 미운영)뿐입니다.")

# ------------------------------------------------------------ 4. 필요 유형
r.h("4. 필요 유형별 자원 수와 집단 자격")
r.p("필요 유형 사전은 [`configs/need_types.yaml`](../../configs/need_types.yaml)입니다. 집단 자격은 자원마다 `해당됨 / 해당 안 됨 / 확인 필요`로 판정합니다(원장 칸이 아니라 함수 `group_eligibility`).")
rows = []
for need in cfg["need_types"]:
    m = need_mask(d, need, cfg)
    for city in CITIES:
        sub = d[m & (d["city"] == city)]
        row = {"필요 유형": need, "도시": city, "자원 수": len(sub)}
        for g in cfg["groups"]:
            vs = sub.apply(lambda x: group_eligibility(x, g)[0], axis=1) if len(sub) else pd.Series(dtype=str)
            row[f"{g} 해당됨"] = int((vs == "해당됨").sum())
            row[f"{g} 확인 필요"] = int((vs == "확인 필요").sum())
        rows.append(row)
need_tab = pd.DataFrame(rows)
need_tab.to_csv(r.dir / "필요유형_자원수.csv", index=False, encoding="utf-8-sig")
r.table(need_tab, index=False)
for need, spec in cfg["need_types"].items():
    r.p(f"- {need}: {spec.get('note', '')}")

# ------------------------------------------------------------ 5. 매칭 예시
r.h("5. 매칭 예시")
r.p("순서: 출처·확인일(G-07) → 관할 → 대상 연령·자격 → 필요 유형 → 운영 상태 → 거리/전화형. "
    "자격을 모르면 \"확인 필요\", 전화형은 거리와 무관, 좌표 없는 거점은 \"위치 확인 필요\", 관할 밖(권역 외) 물리 거점은 최대 \"확인 필요\". 거리는 행정동 중심점에서 잰 직선거리입니다.")
r.p("추천은 **제외 규칙(확인일·출처 없음, 관할 밖 물리 거점)을 먼저 적용한 뒤 상위 5건**을 고릅니다. 빈자리는 다음 자원으로 채우고, "
    "건너뛴 자원은 \"확인할 자원\"으로 사유와 함께 보입니다. 같은 분류는 상위에 2곳까지만 둡니다(경로당 5곳만 나오지 않게). "
    "위기 전화(자살예방·정신건강 위기상담)는 순위에 넣지 않고 맨 아래 **\"위급 시 안내\"**로 따로 둡니다. "
    "고령층 안부·관계는 노인맞춤돌봄서비스(제도) → 행정복지센터 → 노인맞춤돌봄 수행기관 → 경로당·사람 자원 → 노인 돌봄·안전 확인 → (거점 원거리형이면 위기 전화가 아닌 전화형) → 찾아가는 서비스 → 노인복지관 순서입니다. "
    "필요 유형이 여럿이면 유형마다 가장 앞 자원을 하나씩 먼저 넣고 나머지를 순서대로 채웁니다(연휴: 휴일 약국·의원 + 안부 전화 + 생필품 창구). 연휴(달력 공백형)에는 휴일 운영이 '예'인 곳을 앞에 둡니다.")
names = adm_names().set_index("adm_nm")
cases = [
    ("역삼1동", "중장년 남성 1인가구", "안부·관계", None),
    ("역삼1동", "중장년 남성 1인가구", None, "달력 공백형"),
    ("역삼1동", "고령층", None, "거점 원거리형+달력 공백형"),
    ("북산면", "고령층", None, "거점 원거리형+계절 공동화형"),
]
COLS = {"name": "이름", "decision": "결정", "거리(km)": "거리(km)", "delivery_channel": "이용 방법", "checks": "확인 필요 이유"}


def _show(ms):
    f = to_frame(ms)
    f["거리(km)"] = f["distance_km"].map(lambda x: "-" if pd.isna(x) else f"{x:.1f}")
    return f.set_index("resource_id")[list(COLS)].rename(columns=COLS)


for dong, group, need, cause in cases:
    a = names.loc[dong]
    sel = select_resources(d, a["adm_cd"], group, need, cause, origin=(float(a["lat"]), float(a["lon"])))
    r.h3(f"{a['city']} {dong} · {group} · {need or cause}")
    r.p(f"매칭 통과 {sel.n_candidates}건, 제외 규칙 통과 {sel.n_valid}건 중 상위 {len(sel.ranked)}건")
    r.table(_show(sel.ranked))
    if sel.crisis:
        r.p(f"**위급 시 안내** (순위 밖): " + ", ".join(f"{m.resource_id} {m.name}" for m in sel.crisis))
    if sel.excluded:
        r.p("**확인할 자원** (추천 제외): " + "; ".join(f"{m.resource_id} {m.name} — {', '.join(w)}" for m, w in sel.excluded))
r.p()
r.p("- 이 결과는 담당자가 볼 **후보 목록**입니다. 이용 가능 여부와 자격은 담당자가 확인합니다.")
r.p("- '확인 필요 이유'는 섞지 않고 따로 적습니다: 자격 미상 / 휴일 운영 미상 / 위치 미확인 / 운영 상태 미상 / 좌표 탐색용 / 관할 밖 거점.")
r.p("- 원인 유형은 기상 하위 유형을 따릅니다: R4 폭염 → 폭염형, R6 한파·대설 → 한파·대설형(난방·안부·이동 지원·생필품), "
    "R6 호우 → 호우형(안부·이동 지원·주거 안전), 하위 유형을 모르면 '기상 대응 확인 필요'. 관측 거점이 멀어 거점 원거리형 순서를 쓸 때도 원래 원인을 함께 남깁니다(예: 거점 원거리형+계절 공동화형). "
    "난방은 행정복지센터·에너지바우처·긴급복지(연료비)·강남구 한파쉼터 안내, 주거 안전은 행정복지센터·긴급복지(주거지원)로 연결합니다.")

r.h3("동별 가까운 자원까지 거리 (고령층)")
gap = []
for adm_nm, a in names.iterrows():
    row = {"도시": a["city"], "동": adm_nm}
    for need in ["의료 접근", "안부·관계", "정신건강 상담"]:
        g = access_gap(d, a["adm_cd"], (float(a["lat"]), float(a["lon"])), need, "고령층", cfg)
        row[need] = g["nearest_km"]
        if need == "정신건강 상담":
            row["전화형 대안 수"] = len(g["phone_alternatives"])
    gap.append(row)
gap = pd.DataFrame(gap)
gap.to_csv(r.dir / "동별_접근거리.csv", index=False, encoding="utf-8-sig")
for city in CITIES:
    t = gap[gap["도시"] == city].sort_values("의료 접근", ascending=False).set_index("동").drop(columns="도시").head(5)
    r.p(f"**{city}**: 의료 접근 거리가 먼 동 5곳 (km, 자격 '해당 안 됨'은 뺌)")
    r.table(t, {"의료 접근": "{:.1f}", "안부·관계": "{:.1f}", "정신건강 상담": "{:.1f}", "전화형 대안 수": "{:.0f}"})
r.p("- 탐색용 좌표로 잰 직선거리입니다. 거리가 멀다고 실제 서비스가 부족하다고 말하지 않습니다(설계 §5.2).")
r.p("- 강남 '의료 접근'은 강남구보건소에 더해 E-Gen 휴일 운영 약국·병의원과 응급의료기관이 들어갑니다(공휴일 진료시간을 등록한 곳만). 공휴일 시간을 등록하지 않은 민간 의원·약국은 없습니다.")

# ------------------------------------------------------------ 6. 남은 수집
r.h("6. 남은 수집")
r.p("수집 규칙: 원문은 `external/resources/raw/`에 보관(git 추적 안 함), 실행마다 `수집_기록.csv`에 기록, 실패하면 이전 값 유지, "
    "목록에서 빠지면 \"확인 필요\"(종료로 단정하지 않음), 손으로 고친 칸은 정정 기록이 이김.")
r.p()
r.p("| 자료 | 상태 | 신청·확인할 것 |")
r.p("|---|---|---|")
r.p("| 무더위쉼터 (행정안전부) | 미수집 | 공공데이터포털 「행정안전부_무더위쉼터」(15138456)는 LINK형이라 지금 키로는 안 됨. 재난안전데이터공유플랫폼(safetydata.go.kr) 「무더위쉼터」(DSSP-IF-10942) 활용 신청·인증키 필요 |")
r.p("| 한파쉼터 (행정안전부) | 미수집 | 「행정안전부_공유플랫폼_한파쉼터」(15139703, LINK형) → safetydata.go.kr 「한파쉼터」(DSSP-IF-10804) 활용 신청·인증키 필요 |")
r.p("| 춘천 쉼터 목록 | 미수집 | 춘천시 누리집에서 목록을 찾지 못함. 위 API 승인 뒤 춘천시분을 거름 |")
r.p("| juso 좌표 | 대기 | 도로명주소 좌표제공 API 승인키(JUSO_COORD_KEY) 미승인. 승인 뒤 탐색용 좌표를 확인(juso)으로 바꿈 |")
r.p("| 노인맞춤돌봄 수행기관 2026 목록·담당 구역 | 일부 | 2024-04-23 기준 목록만 있음(위수탁기간 종료). 강남구·춘천시 돌봄 부서에 2026 재지정·담당 읍·면·동 확인 |")
r.p("| 사람 자원 (돌봄단·명예사회복지공무원·통·이장) | 일부 | 강남 명예사회복지공무원만 공식 보도로 확인. 나머지 4건은 '미확인'으로 넣고 추천하지 않음 |")
r.p("| 춘천 경로당, 강남 동주민센터 | 미수집 | 강남 한파쉼터 목록에 주민센터 21곳 주소가 있음 (원장 행으로는 아직 안 넣음) |")
r.p("| 청년 자원 (서울·강원) | 일부 | 복지로 '위기청년(고립은둔)'·춘천 은둔청년 고독사 예방만 들어감 |")
r.p("| 동별 기초생활수급자·임대주택·고시원 | 미수집 | 구조 취약성 축 보강 (자원 원장 칸 아님) |")
r.p()
r.p("- 이 자료(동별 기초생활수급자·임대주택·고시원)는 **낙인 점수로 만들지 않습니다.** 동별 비율·규모·기준일을 따로 보여 줍니다.")
r.p("- 운영 상태(operating_status)는 공식 목록 재수집과 전화 확인이 있어야 채울 수 있습니다. 보강 자원도 모두 미상입니다.")
r.p("- 임베딩 검색 평가는 정답 자원 세트(열린 질문 Q9)가 정해진 뒤에 합니다.")
r.p("- 2026년 원장을 2025년 신호에 붙이면 \"현재 대응안 예시\"로 표시합니다. 자원·접근거리 근거에는 \"현재 원장 기준 — 2025년 당시 운영·위치는 확인되지 않음\"을 붙이고 공개 시점은 '모름'으로 둡니다.")
r.p("- 아직 없는 자원: 푸드마켓·푸드뱅크, 지역 정신건강·자살예방센터 일부, 춘천 쉼터.")

# ------------------------------------------------------------ 7. 보강 자원
r.h("7. 보강 자원")
r.p("수집: `uv run python -m bigcontest.resources.sources` (.env 의 DATA_GO_KR_API_KEY). 키 없이 다시 만들기는 `--offline`(raw 의 마지막 성공 원문). "
    "결과 `external/resources/추가자원_원장.csv`(API 행 + 손으로 정리한 `수동_추가자원.csv`)를 원래 원장 뒤에 붙입니다. 규칙은 `src/bigcontest/resources/sources.py` 맨 위.")
t = sup.groupby(["resource_kind", "subtype", "city"]).size().unstack("city", fill_value=0)
t = t.reindex(columns=CITIES, fill_value=0)
t["합계"] = t.sum(axis=1)
t.index = [f"{a} · {b}" for a, b in t.index]
t.index.name = "묶음 · 분류"
t.to_csv(r.dir / "보강자원_구성.csv", encoding="utf-8-sig")
r.table(t)
runs = pd.read_csv(ROOT / "external/resources/수집_기록.csv", dtype=str, keep_default_na=False)
runs = runs[runs["status"] == "성공"].drop_duplicates("source", keep="last").set_index("source")
r.p("API 수집 (받은 건수 → 원장에 넣은 건수):")
r.p()
r.p("| 출처 | 데이터셋 | 받은 건수 | 원장에 넣은 것 | 조회일 |")
r.p("|---|---|---|---|---|")
SRC_INFO = {
    "egen_pharmacy": ("국립중앙의료원_전국 약국 정보 조회 서비스 (15000576)", "공휴일 진료시간 등록 약국"),
    "egen_clinic": ("국립중앙의료원_전국 병·의원 찾기 서비스 (15000736)", "공휴일 진료시간 등록 종합병원·병원·의원 (응급의료기관과 겹치면 뺌)"),
    "egen_emergency": ("국립중앙의료원_전국 응급의료기관 정보 조회 서비스 (15000563)", "모두"),
    "bokjiro_national": ("한국사회보장정보원_중앙부처복지서비스 (15090532)", "이름이 고립 관련 규칙에 맞는 사업 (두 도시에 한 줄씩)"),
    "bokjiro_local": ("한국사회보장정보원_지자체복지서비스 (15108347)", "시·도 본청과 강남구·춘천시 부서 사업 중 규칙에 맞는 것"),
}
for src, run in runs.iterrows():
    key = src.rsplit("_", 1)[0] if src.startswith(("egen", "bokjiro_local")) else src
    name, rule = SRC_INFO[key]
    n_in = int((sup["src"] == src).sum())
    r.p(f"| {src} | {name} | {run['n_records']} | {n_in} ({rule}) | {run['started_at'][:10]} |")
r.p()
hol = sup[sup["subtype"].isin(["휴일 운영 약국", "휴일 운영 병·의원"])]
r.p(f"- 휴일 운영 약국·병의원 {len(hol)}곳: E-Gen 에 공휴일 진료시간을 등록한 곳만 넣었습니다. 등록하지 않은 곳을 '휴일 미운영'으로 단정하지 않으려고 아예 넣지 않았습니다. "
    "공휴일 진료시간은 평소 공휴일 기준이라 **명절 당일 운영은 응급의료포털 '명절 문 여는 병의원·약국'에서 다시 확인**하라고 자원마다 적었습니다(note).")
r.p("- 좌표는 E-Gen 제공 위경도(탐색용)입니다. 행정동은 `ref/adm_boundary.geojson`(vuski/admdongkor, CC BY 4.0) 경계로 정했고, 강남역 지하상가처럼 경계선 위에 있는 2곳은 0.3km 안의 가까운 동으로 넣었습니다.")
r.p("- 복지로 사업은 위치가 없는 **제도·사업**(이용 방법 '신청', 좌표 '해당 없음')입니다. 거리와 상관없이 남고, 자격은 노인맞춤돌봄(만 65세 이상 돌봄 필요), 긴급복지(위기 사유·소득 기준), 에너지(소득·세대원 기준), 그 밖에는 복지로 생애주기로 판정합니다(대상 집단이 생애주기에 없으면 '해당 안 됨').")
mc = sup[sup["subtype"] == "노인맞춤돌봄 수행기관"]
r.p(f"- 노인맞춤돌봄 수행기관 {len(mc)}곳(강남 {int((mc['city'] == '강남').sum())}, 춘천 {int((mc['city'] == '춘천').sum())}): 보건복지부 노인돌봄서비스 기관 현황(공공데이터포털 15004317, 2024-04-23 기준)입니다. "
    "**위수탁기간이 2025년에 끝나 2026년 재지정·담당 구역은 미확인**입니다. 좌표는 원장의 같은 주소 기관 좌표를 썼습니다. 고령층 순서에서 노인맞춤돌봄서비스(제도)·행정복지센터 다음에 둡니다.")
pp = sup[sup["resource_kind"] == "사람"]
t = pp.set_index("resource_id")[["city", "name", "verified_date", "last_verified", "source_url"]].replace("", "-")
t.columns = ["도시", "이름", "원장 확인일", "확인일(날짜)", "출처"]
r.p()
r.p("사람 자원 (공식 자료로 지금 활동을 확인한 것만 추천, 나머지는 '확인할 자원'):")
r.table(t)
cp = sup[(sup["src"] == "official_page") & (sup["resource_kind"] == "제도·사업")]
t = cp.set_index("resource_id")[["city", "name", "operation_confirmed_at", "verified_url"]].replace("", "-")
t.columns = ["도시", "이름", "페이지 날짜", "공식 페이지"]
r.p("공식 페이지로 넣은 지역 사업·안내:")
r.table(t)
r.p("- 강남구 한파쉼터·무더위쉼터는 구청 목록 페이지를 가리키는 안내 한 줄입니다(쉼터마다 행을 만들지 않음). 페이지에 날짜·운영 기간이 없어 2026~27 겨울 목록은 다시 확인해야 합니다.")
r.p("- 출처 라이선스: E-Gen·복지로·노인돌봄 기관 현황은 공공데이터포털 '이용허락범위 제한 없음'. 구청·시청 페이지는 출처 URL 과 확인일을 함께 적었습니다. API 원문은 올리지 않습니다.")

out = r.save()
print("saved", out.relative_to(ROOT))
