"""Report 도구 (에이전트 설계 §5): 근거 카드(결정론적), 대응안(템플릿 + LLM 설명은 write_report 에서), 실행 메타데이터.

숫자·사실은 근거 카드에서만 만든다. 대응안은 설계 문서 §5.4 시점 규칙·§5.5 원인 유형의 템플릿이다.

담당자용 출력 (configs/action_templates.yaml)
- 확인할 영역: 근거·원인 → 현장에서 먼저 물어볼 영역 (판단도구 문항 채점 아님)
- 대응 템플릿 5칸: 주체 / 방법 / 기한 / 확인할 영역 / 연계 자원 (+ 보고서용 한 줄)
- 내일 아침 체크리스트: 꺼낼 명단 / 연락할 사람·기관 / 방법 / 기한 / 확인할 영역. 시스템은 명단을 만들지 않음
- 청년 남성은 방문이 아니라 안내 강화
- action-v2 선택 칸: 폭염 특보 단계별 안부 횟수(R4, 후보 heat_stage 칸 → 없으면 트리거 글 → 없으면 '확인 필요'),
  명절 전·중·후 한 묶음(R1·R2·R3), 연락할 곳의 사전 안부 협력자(임대인·관리사무소·고시원 운영자, 주거 맥락일 때),
  중장년 남성 연결 묶음(고용복지+센터·중독관리통합지원센터·1인가구 지원)과 사회적 처방형 3단계(안내 → 연결 → 확인)
등급 설명 `explain_level`: 후보의 rule_checks(등급 엔진이 채움)로 조건별 충족·기준까지 거리·바뀔 등급을 쓴다.
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from bigcontest.agent.schemas import (
    ActionPlan,
    BundleItem,
    Candidate,
    CandidateAction,
    CheckArea,
    EvidenceCard,
    Fact,
    HolidayPhase,
    LevelExplanation,
    LinkStep,
    MorningChecklist,
    RuleCheck,
    RuleCheckView,
    ToolResult,
)


@lru_cache
def action_templates() -> dict:
    """configs/action_templates.yaml (대응 템플릿·확인할 영역·체크리스트)."""
    from bigcontest.contracts import load_yaml

    return load_yaml("action_templates.yaml")


# 설계 문서 §5.4 시점 규칙 → 할 일 (configs/action_templates.yaml rules.*.task)
PLAN_OF_RULE = {k: v["task"] for k, v in action_templates()["rules"].items()}
PLAN_DEVIATION = action_templates()["deviation"]["task"]
PLAN_FAR = action_templates()["far"]["task"]
CRISIS_GROUP = "위급 시 안내"


def _period(e: ToolResult, as_of: date) -> str:
    if e.observed_from and e.observed_to:
        if e.observed_from == e.observed_to:
            if "월" in (e.measured_grain or ""):
                return f"{e.observed_to:%Y-%m}"
            return str(e.observed_to)
        return f"{e.observed_from}~{e.observed_to}"
    return f"기준일 {as_of}"


def fact_of(e: ToolResult, as_of: date) -> Fact:
    if e.merchant_region:
        # 카드 데이터1 은 거주지 칸이 없음 → "거주지 모름"
        region = f"{e.merchant_region} 가맹점 · " + (f"{e.customer_residence} 거주" if e.customer_residence else "거주지 모름")
        if e.analysis_cohort:
            region += f" {e.analysis_cohort}"
    else:
        region = e.applied_scope or "-"
    return Fact(metric=e.metric, region=region.replace(",", " "), period=_period(e, as_of), value=e.value,
                unit=e.unit, direction=e.direction, evidence_id=e.evidence_id)


def is_numeric(e: ToolResult) -> bool:
    return (e.status == "OK" and isinstance(e.value, (int, float)) and not isinstance(e.value, bool)
            and e.metric != "복지 자원 후보")


def build_evidence_card(run_id: str, snapshot_id: str, analysis_mode: str, as_of: date, evidence_revision: int,
                        evidence: list[ToolResult], title: str, limitations: list[str] | None = None,
                        candidate: Candidate | None = None, sort_desc: bool = False) -> EvidenceCard:
    """결정론적 근거 카드. candidate 를 주면 그 후보가 쓴 근거만."""
    if candidate is not None:
        ids = set(candidate.evidence_ids)
        evidence = [e for e in evidence if e.evidence_id in ids]
    numeric = [e for e in evidence if is_numeric(e)]
    if sort_desc:
        numeric.sort(key=lambda e: -float(e.value))
    lim = list(limitations or [])
    for e in evidence:
        lim += [w for w in e.warnings if not w.startswith("가정한 공개 시점")]
    return EvidenceCard(
        card_id=f"CARD-{run_id}-{evidence_revision}" + (f"-{candidate.candidate_id}" if candidate else ""),
        run_id=run_id, candidate_id=candidate.candidate_id if candidate else None, title=title,
        snapshot_id=snapshot_id, analysis_mode=analysis_mode, as_of=as_of, evidence_revision=evidence_revision,
        facts=[fact_of(e, as_of) for e in numeric], evidence=evidence, limitations=list(dict.fromkeys(lim)))


# ---------------------------------------------------------------- 담당자용 출력


@lru_cache
def _adm_names() -> dict[str, str]:
    from bigcontest.config import ROOT

    try:
        with open(ROOT / "ref" / "adm_cd_names.csv", encoding="utf-8") as f:
            return {r["adm_cd"]: r["adm_nm"] for r in csv.DictReader(f)}
    except OSError:
        return {}


def adm_name(adm_cd: str | None) -> str | None:
    """행정동 코드 → 이름 (ref/adm_cd_names.csv). 모르면 코드 그대로."""
    if not adm_cd:
        return None
    return _adm_names().get(adm_cd, adm_cd)


def rule_keys(trigger: str | None) -> list[str]:
    """트리거 문장('; '로 이음) → 템플릿 키 (R1·R2·R3·R4·R6 하위 유형·W). 순서 유지, 중복 없음."""
    out: list[str] = []
    for t in (trigger or "").split(";"):
        t = t.strip()
        if not t:
            continue
        rule = t.split(" ")[0].split("-")[0]
        if rule == "R6":  # 기상특보는 하위 유형별 문구. 모르면 공통 문구
            rule = next((f"R6 {k}" for k in ("호우", "대설", "한파") if k in t), "R6")
        if rule in action_templates()["rules"] and rule not in out:
            out.append(rule)
    return out


@lru_cache
def _label_to_group() -> dict[str, str]:
    from bigcontest.contracts import load_yaml

    return {g["service"]["label"]: name for name, g in load_yaml("cohorts.yaml")["groups"].items()}


def group_key(c: Candidate) -> str:
    """후보 → 대응 템플릿 집단 (고령층 / 중장년 남성 1인가구 / 청년 남성 / 기본).
    서비스 대상 이름(cohorts.yaml label)이나 집단 이름을 먼저 보고, 없으면 분석 코호트 말로."""
    groups = action_templates()["groups"]
    seg = getattr(c, "service_eligibility_group", None) or ""
    if seg in groups:
        return seg
    if seg in _label_to_group():
        return _label_to_group()[seg]
    coh = getattr(c, "analysis_cohort", None) or ""
    if any(k in coh for k in ("60", "65", "70", "80")):
        return "고령층"
    if "남" in coh and any(k in coh for k in ("40", "50")):
        return "중장년 남성 1인가구"
    if "남" in coh and any(k in coh for k in ("20", "30")):
        return "청년 남성"
    return "기본"


def _is_far(far_km: float | None, access_km: float | None) -> bool:
    return far_km is not None and access_km is not None and access_km >= far_km


def _structure_top(c: Candidate) -> bool:
    return "구조 취약성 상위" in (getattr(c, "level_reason", None) or "")


def check_areas(c: Candidate, far: bool = False) -> list[CheckArea]:
    """확인할 영역: 시점 규칙·이탈·거점 거리·구조 취약성·집단 → 영역 (이유를 함께). 순서 유지."""
    t = action_templates()
    found: dict[str, list[str]] = {}

    def add(table: dict | None) -> None:
        for area, why in (table or {}).items():
            found.setdefault(area, [])
            if why not in found[area]:
                found[area].append(why)

    if c.candidate_type == "scheduled_access_review":
        for k in rule_keys(c.trigger):
            add(t["rules"][k]["areas"])
    else:
        add(t["deviation"]["areas"])
    if far:
        add(t["far"]["areas"])
    if _structure_top(c):
        add(t["structure"]["areas"])
    add(t["groups"][group_key(c)].get("extra_areas"))
    return [CheckArea(area=a, reasons=why, ask=t["areas"].get(a)) for a, why in found.items()]


def due_of(c: Candidate, as_of: date | None) -> tuple[str, date | None]:
    """기한 (트리거 기준). 여러 트리거면 가장 이른 날. D-n 연휴 트리거는 연휴 전날, 동절기 2주, 이탈 3일."""
    t = action_templates()
    specs: list[tuple[dict, str]] = []
    if c.candidate_type == "scheduled_access_review":
        parts = [p.strip() for p in (c.trigger or "").split(";") if p.strip()]
        for k in rule_keys(c.trigger):
            raw = next((p for p in parts if p.startswith(k.split(" ")[0])), "")
            specs.append((t["rules"][k]["due"], raw))
    else:
        specs.append((t["deviation"]["due"], ""))
    best: tuple[str, date | None] | None = None
    for spec, raw in specs:
        d: date | None = None
        if as_of is not None:
            if spec["kind"] == "before_holiday":
                m = re.search(r"D-(\d+)", raw)
                d = as_of + timedelta(days=int(m.group(1)) - 1) if m else None
            elif spec["kind"] == "days":
                d = as_of + timedelta(days=int(spec["days"]))
        label = spec["label"] + (f" ({d}까지)" if d else "")
        if best is None or (d is not None and (best[1] is None or d < best[1])):
            best = (label, d)
    return best or ("담당자가 정함", None)


def _mine(c: Candidate, resources: list[dict]) -> tuple[list[dict], list[dict]]:
    """이 후보의 자원 → (연계 자원, 위급 시 안내). 위급 시 안내(위기 전화)는 순위 밖."""
    mine = [r for r in resources if r.get("candidate_id") == c.candidate_id]
    crisis = [r for r in mine if r.get("group") == CRISIS_GROUP
              or any(CRISIS_GROUP in w for w in (r.get("warnings") or []))]
    return [r for r in mine if r not in crisis], crisis


def _resource_label(r: dict) -> str:
    kind = "전화형" if r.get("phone_based") else r.get("resource_type")
    return f"{r['name']}({kind}, 자격 {r.get('eligibility', '확인 필요')}, 운영 {r.get('operating_status', '확인 필요')})"


def where_of(c: Candidate) -> str:
    if c.adm_cd:
        return f"{c.city} {adm_name(c.adm_cd)}"
    first = [adm_name(a) for a in c.check_first_adm]
    return f"{c.city} 먼저 확인할 동 {', '.join(first)}" if first else f"{c.city} 전체"


# ---------------------------------------------------------------- action-v2: 폭염 단계·명절 전중후·협력자·연결 묶음

HEAT_RULE = "R4"
HOLIDAY_RULES = ("R1", "R2", "R3")


def heat_stage_key(c: Candidate) -> str | None:
    """R4 후보의 폭염 특보 단계 키 (중대경보 / 경보 / 주의보 / unknown). R4 가 아니면 None.
    후보의 heat_stage 칸(규칙 엔진이 채울 때)을 먼저 보고, 없으면 트리거 글에서 단계 말을 찾는다. 단계를 지어내지 않음."""
    if HEAT_RULE not in rule_keys(c.trigger):
        return None
    for raw in (getattr(c, "heat_stage", None), c.trigger):
        text = str(raw or "")
        if "중대" in text:
            return "중대경보"
        if "경보" in text:
            return "경보"
        if "주의" in text:
            return "주의보"
    return "unknown"


def heat_contact(c: Candidate) -> tuple[str | None, str | None]:
    """(단계 이름, 안부 횟수 + 순서). R4 가 아니면 (None, None)."""
    key = heat_stage_key(c)
    if key is None:
        return None, None
    h = action_templates()["heat_stages"]
    st = h["stages"].get(key) or h["unknown"]
    return st["label"], f"{st['frequency']} ({h['order']})"


def _holiday_current(c: Candidate) -> set[str]:
    """이 후보가 서 있는 명절 단계의 규칙 (R1·R2·R3). 후보의 holiday_phase 칸(전·중·후)이 있으면 그것도."""
    keys = {k for k in rule_keys(c.trigger) if k in HOLIDAY_RULES}
    phase = getattr(c, "holiday_phase", None)
    by_phase = {p["phase"]: p["rule"] for p in action_templates()["holiday_phases"]["phases"]}
    if phase in by_phase:
        keys.add(by_phase[phase])
    return keys


def holiday_timeline(c: Candidate, gk: str) -> list[HolidayPhase]:
    """명절 트리거(R1·R2·R3)면 전·중·후 3칸을 한 묶음으로. 지금 단계는 current. 명절이 아니면 빈 목록."""
    cur = _holiday_current(c)
    if not cur:
        return []
    out = []
    for p in action_templates()["holiday_phases"]["phases"]:
        contact = p.get("contact") or {}
        out.append(HolidayPhase(phase=p["phase"], rule=p["rule"], label=p["label"], window=p["window"],
                                tasks=list(p.get("tasks") or []), contact=contact.get(gk) or contact.get("기본"),
                                current=p["rule"] in cur))
    return out


def housing_partners(c: Candidate, gk: str) -> list[str]:
    """주거 맥락이면 사전 안부 협력자 (임대인·관리사무소·경비원·고시원 운영자). 1인세대 비율 상위, 중장년·청년 남성,
    명절 당일·폭염·한파·대설, 1인가구 밀집형·경제 취약형 원인 중 하나라도 맞으면."""
    hp = action_templates()["housing_partners"]
    w = hp.get("when") or {}
    causes = set((getattr(c, "cause_type", None) or "").split("+"))
    hit = (w.get("structure_top") and _structure_top(c)) or gk in (w.get("groups") or [])         or any(k in (w.get("rules") or []) for k in rule_keys(c.trigger)) or bool(causes & set(w.get("causes") or []))
    return list(hp["contacts"]) if hit else []


@lru_cache
def _ledger_names() -> dict[str, dict]:
    """resource_id → 원장 v2 행 (이름·전화). 연결 묶음 이름을 원장에서 읽음 (설정에 이름을 따로 적지 않음)."""
    from bigcontest.resources.ledger import V2

    try:
        with open(V2, encoding="utf-8") as f:
            return {r["resource_id"]: r for r in csv.DictReader(f)}
    except OSError:
        return {}


def link_bundle(c: Candidate, gk: str) -> tuple[list[LinkStep], list[BundleItem]]:
    """집단 설정에 link_steps 가 켜져 있으면 3단계 + 그 도시의 연결 묶음 (원장에 없는 자원은 건너뜀)."""
    g = action_templates()["groups"][gk]
    if not g.get("link_steps"):
        return [], []
    steps = [LinkStep(step=x["step"], text=x["text"]) for x in action_templates()["link_steps"]]
    names = _ledger_names()
    items = []
    for x in (g.get("bundle") or {}).get(c.city, []):
        r = names.get(x["resource_id"])
        if r is None:
            continue
        items.append(BundleItem(need=x["need"], resource_id=x["resource_id"], name=r["name"], phone=r.get("phone") or None))
    return steps, items


def build_action_plan(c: Candidate, resources: list[dict], far_km: float | None = None,
                      access_km: float | None = None, as_of: date | None = None) -> ActionPlan | None:
    """대응 템플릿 5칸. Level 2·3 후보만. 청년 남성은 방문이 아니라 안내 강화."""
    if c.candidate_level not in ("Level 2", "Level 3"):
        return None
    t = action_templates()
    gk = group_key(c)
    g = t["groups"][gk]
    far = _is_far(far_km, access_km)
    tasks = ([PLAN_OF_RULE[k] for k in rule_keys(c.trigger)] if c.candidate_type == "scheduled_access_review"
             else [PLAN_DEVIATION])
    if far:
        tasks.append(PLAN_FAR)
    method = g["method"]
    if far and gk != "청년 남성":
        method = f"{t['far_method_prefix']} → {method}"
    due, due_date = due_of(c, as_of)
    areas = [a.area for a in check_areas(c, far)]
    linked, crisis = _mine(c, resources)
    linked_labels = [_resource_label(r) for r in linked[:3]]
    note = None if linked else "자원 후보 없음 (원장에서 조건에 맞는 자원을 찾지 못함)"
    res_text = f"연계 자원: {', '.join(linked_labels)}" if linked_labels else note
    stage, freq = heat_contact(c)
    timeline = holiday_timeline(c, gk)
    steps, bundle = link_bundle(c, gk)
    partners = housing_partners(c, gk)
    text = (f"대응안 {c.candidate_id}: 주체 {'·'.join(g['owner'])} / 방법 {method} / 기한 {due} / "
            f"확인할 영역 {', '.join(areas)} / 할 일 {'; '.join(tasks)} / {res_text}")
    if freq:
        text += f" / 안부 횟수({stage}) {freq}"
    return ActionPlan(owner=list(g["owner"]), method=method, due=due, due_date=due_date, check_areas=areas,
                      linked_resources=linked_labels, tasks=tasks, resource_note=note,
                      emergency_contacts=[r["name"] for r in crisis], text=text, heat_stage=stage,
                      contact_frequency=freq, holiday_timeline=timeline, partners=partners,
                      partner_note=action_templates()["housing_partners"]["ask"] if partners else None,
                      link_steps=steps, bundle=bundle)


def build_checklist(c: Candidate, plan: ActionPlan) -> MorningChecklist:
    """내일 아침 체크리스트. 기존 명단을 이 동·집단으로 걸러 보라고 안내만 한다 (명단을 만들지 않음)."""
    g = action_templates()["groups"][group_key(c)]
    who = getattr(c, "service_eligibility_group", None) or c.analysis_cohort
    where = where_of(c)
    lists = [f"{name}: {where} · {who} 대상만 걸러 보기" for name in g["lists"]]
    contacts = list(g["contacts"]) + [f"연계 자원 {x}" for x in plan.linked_resources]
    contacts += [f"{p} (사전 안부 협력: 이상 신호만 알려 달라고 요청)" for p in plan.partners]
    contacts += [f"연결 묶음 {b.need}: {b.name}" for b in plan.bundle]
    return MorningChecklist(lists=lists, contacts=contacts, method=plan.method, due=plan.due,
                            check_areas=plan.check_areas, note=action_templates()["list_note"])


def build_candidate_action(c: Candidate, resources: list[dict], far_km: float | None = None,
                           access_km: float | None = None, as_of: date | None = None) -> CandidateAction | None:
    """후보 하나의 담당자용 출력 (Level 2·3): 확인할 영역 + 대응 템플릿 + 체크리스트."""
    plan = build_action_plan(c, resources, far_km, access_km, as_of)
    if plan is None:
        return None
    return CandidateAction(candidate_id=c.candidate_id, candidate_level=c.candidate_level, group=group_key(c),
                           where=where_of(c), check_areas=check_areas(c, _is_far(far_km, access_km)),
                           check_area_note=action_templates()["check_area_note"], action_plan=plan,
                           checklist=build_checklist(c, plan))


def generate_response_plan(c: Candidate, resources: list[dict], far_km: float | None = None,
                           access_km: float | None = None, as_of: date | None = None) -> str | None:
    """대응안 한 줄 (보고서용). Level 2·3 후보만. 구조는 build_action_plan."""
    plan = build_action_plan(c, resources, far_km, access_km, as_of)
    return plan.text if plan else None


# ---------------------------------------------------------------- 등급 설명

_CMP_KO = {">=": "이상", "<=": "이하", ">": "초과", "<": "미만"}


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _fmt(x, unit: str | None = None, gap: bool = False) -> str:
    """값 + 단위. 비율은 %로 (차이는 %p): 0.153 → 15.3%. km·일·위는 숫자 뒤에 붙임."""
    if unit and "비율" in unit and isinstance(x, (int, float)) and not isinstance(x, bool):
        return f"{round(float(x) * 100, 1):g}{'%p' if gap else '%'}"
    s = f"{x:g}" if isinstance(x, float) else str(x)
    return s + (unit or "")


def _view(chk: RuleCheck) -> RuleCheckView:
    dist = None
    if _num(chk.value) and _num(chk.threshold) and chk.comparator:
        gap = (float(chk.threshold) - float(chk.value) if chk.comparator in (">=", ">")
               else float(chk.value) - float(chk.threshold))
        dist = round(abs(gap), 4)
    state = "충족" if chk.met else ("자료 없음" if chk.met is None else "미충족")
    thr = None
    if chk.threshold is not None:
        thr = _fmt(chk.threshold, chk.unit) + (f" {_CMP_KO[chk.comparator]}" if chk.comparator else "")
    inside = []
    if chk.value is not None:
        inside.append(_fmt(chk.value, chk.unit))
    if thr:
        inside.append(f"기준 {thr}")
    if dist is not None and chk.met is not None:
        inside.append(f"{'여유' if chk.met else '부족'} {_fmt(dist, chk.unit, gap=True)}")
    text = f"{chk.condition} 조건: {state}" + (f"({', '.join(inside)})" if inside else "")
    return RuleCheckView(condition=chk.condition, met=chk.met, value=chk.value, threshold=chk.threshold,
                         distance=dist, text=text)


def explain_level(candidate: Candidate, rule_trace: list | None = None) -> LevelExplanation:
    """등급 설명 (결정론). rule_trace 를 안 주면 candidate.rule_checks.

    - 어떤 규칙이 맞았나 (rule_id·rule_version)
    - 조건마다 충족 / 미충족 / 자료 없음 + 기준까지 거리 (값·기준이 수일 때)
    - 무엇이 바뀌면 등급이 바뀌나: 자료 없는 조건·미충족 조건의 level_if_met, 빠진 필수 근거
    LLM 은 이 결과를 근거 ID 로만 풀어 쓴다 (등급을 바꾸지 못함)."""
    checks = [x if isinstance(x, RuleCheck) else RuleCheck(**x)
              for x in (rule_trace if rule_trace is not None else candidate.rule_checks)]
    views = [_view(x) for x in checks]
    level = candidate.candidate_level
    now = level or "등급 전"
    if candidate.rule_id:
        lines = [f"적용 규칙 {candidate.rule_id}"
                 + (f" ({candidate.rule_version})" if candidate.rule_version else "") + f" → {now}"]
    else:
        lines = [f"적용 규칙 기록 없음 → {now} (등급 이유: {candidate.level_reason or '-'})"]
    lines += [v.text for v in views]
    what_if: list[str] = []
    covered: set[str] = set()
    for x, v in zip(checks, views):
        if x.met is None:
            if x.input:
                covered.add(x.input)
            need = f"{x.condition} 자료" + (f"({x.input})" if x.input else "")
            what_if.append(f"{need}가 들어와 충족되면 " + (f"{x.level_if_met} 가능" if x.level_if_met else "다시 판정"))
        elif x.met is False and x.level_if_met and x.level_if_met != level:
            gap = f" (기준까지 {_fmt(v.distance, x.unit, gap=True)} 부족)" if v.distance is not None else ""
            what_if.append(f"{x.condition} 조건을 충족하면 {x.level_if_met} 가능{gap}")
    for m in candidate.missing_inputs:
        if not any(k in m for k in covered):
            what_if.append(f"빠진 필수 근거 {m}: 들어오면 다시 판정 (지금 {now})")
    lines += what_if
    return LevelExplanation(candidate_id=candidate.candidate_id, candidate_level=level, rule_id=candidate.rule_id,
                            rule_version=candidate.rule_version, checks=views, what_if=what_if, lines=lines)


def write_run_metadata(ctx, run_id: str, out_dir: Path, thresholds: dict | None = None):
    """runmeta 로 실행 메타데이터 저장 (run_id 키로 한 번만). (메타데이터, 새로 썼는지)."""
    from bigcontest.agent.tools.common import rule_version
    from bigcontest.agent.tools.datasource import open_datasource
    from bigcontest.runmeta import write_run_metadata as _write

    meta = open_datasource(ctx.snapshot_id).run_metadata(run_id, ctx.analysis_mode, ctx.as_of,
                                                        thresholds=thresholds, rule_version=rule_version())
    return _write(meta, Path(out_dir))
