"""첫 화면(GET /home) 한 번에: 요약 → 먼저 볼 곳 지도 → 근거 → 질문.

- 실제 백엔드: 대시보드와 같은 발행된 정기 감시 실행의 후보·근거 (dashboard_real.RealDashboard)
- 정적 배포(Vercel): scripts/export_static.py 가 같은 build_home 으로 기준일·도시별 JSON 을 만든다.
  원자료는 싣지 않고 후보·등급·근거 문장·자원 이름 같은 집계 결과만 (대회 자료 재배포 금지, NOTICE.md)
- 동 색은 그 동 예정된 점검 후보 중 가장 높은 등급 (척도는 Level 하나). 이탈 경보의 '먼저 볼 동'은 따로 표시
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from bigcontest.agent import nodes
from bigcontest.agent.schemas import Candidate, ToolResult
from bigcontest.rules import candidates as RC

from api.services import fake_data as fd

RANK = {"Level 0": 0, "Level 1": 1, "판단 보류": 1, "Level 2": 2, "Level 3": 3}
TRIGGER_METRIC = "달력 트리거"
RESOURCES_PER_CANDIDATE = 3


class HomeResource(BaseModel):
    name: str
    resource_type: str
    eligibility: str
    distance_km: float | None = None
    phone_based: bool = False


class HomeCandidate(BaseModel):
    candidate_id: str
    candidate_type: str
    adm_cd: str | None = None
    adm_nm: str | None = None
    check_first_adm: list[str] = Field(default_factory=list)
    group: str | None = None
    analysis_cohort: str
    candidate_level: str | None = None
    level_reason: str | None = None
    trigger: str | None = None
    response_type: str | None = None
    evidence_confidence: str | None = None
    input_status: str
    missing_inputs: list[str] = Field(default_factory=list)
    counter_result: str
    counter_reason: str | None = None
    queue_rank: int | None = None
    selected_for_review: bool = False
    cause_type: str | None = None
    resources: list[HomeResource] = Field(default_factory=list)


class HomeArea(BaseModel):
    adm_cd: str
    adm_nm: str
    level: str | None = None  # 예정된 점검 후보 중 가장 높은 등급, 후보가 없으면 None
    candidate_ids: list[str] = Field(default_factory=list)
    deviation_first: bool = False  # 이탈 경보의 먼저 볼 동 (도시 단위 신호, 공유 맥락)


class HomeTrigger(BaseModel):
    code: str
    name: str
    starts_on: date | None = None


class HomeSummary(BaseModel):
    counts: dict[str, int]
    triggers: list[HomeTrigger] = Field(default_factory=list)
    holiday_watch: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)  # 먼저 볼 후보의 대응 종류 (순서대로, 중복 없음)


class HomeResponse(BaseModel):
    as_of: date
    city: str
    snapshot_id: str
    rule_version: str
    selection_budget: int
    summary: HomeSummary
    areas: list[HomeArea]
    candidates: list[HomeCandidate]
    monitoring_run_id: str | None = None


def _triggers(evidence: list[ToolResult]) -> list[HomeTrigger]:
    out: dict[str, HomeTrigger] = {}
    for e in evidence:
        if e.metric == TRIGGER_METRIC and e.status == "OK":
            text = str(e.value)
            code = RC.trigger_code(text) or "R?"
            out.setdefault(code, HomeTrigger(code=code, name=text.removeprefix(code).strip() or text,
                                             starts_on=e.observed_from))
    return list(out.values())


def _resources(c: Candidate, cause: str) -> list[HomeResource]:
    from api.services.dashboard_real import find_ledger_resources

    adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
    found = find_ledger_resources(c.city, adm, None, c.service_eligibility_group, cause)
    return [HomeResource(name=r.name, resource_type=r.resource_type, eligibility=r.eligibility,
                         distance_km=r.distance_km, phone_based=r.phone_based)
            for r in found if r.eligibility != "부적합"][:RESOURCES_PER_CANDIDATE]


def build_home(as_of: date, city: str, cands: list[Candidate], evidence: list[ToolResult], snapshot_id: str,
               rule_version: str, selection_budget: int, run_id: str | None = None,
               with_resources: bool = True) -> HomeResponse:
    names = {a.adm_cd: a.adm_nm for a in fd.city_areas(city)}
    cands = sorted(cands, key=lambda c: (c.queue_rank is None, c.queue_rank or 0, -RANK.get(c.candidate_level or "", 0),
                                         c.candidate_id))
    out_c = []
    for c in cands:
        cause = nodes.candidate_cause(c, evidence)
        out_c.append(HomeCandidate(
            candidate_id=c.candidate_id, candidate_type=c.candidate_type, adm_cd=c.adm_cd,
            adm_nm=names.get(c.adm_cd) if c.adm_cd else None, check_first_adm=c.check_first_adm,
            group=c.service_eligibility_group, analysis_cohort=c.analysis_cohort, candidate_level=c.candidate_level,
            level_reason=c.level_reason, trigger=c.trigger, response_type=c.response_type,
            evidence_confidence=c.evidence_confidence, input_status=c.input_status, missing_inputs=c.missing_inputs,
            counter_result=c.counter_result, counter_reason=c.counter_reason, queue_rank=c.queue_rank,
            selected_for_review=c.selected_for_review, cause_type=cause,
            resources=_resources(c, cause) if with_resources else []))
    areas = []
    for adm, nm in sorted(names.items()):
        mine = [c for c in cands if c.candidate_type == "scheduled_access_review" and c.adm_cd == adm]
        top = max(mine, key=lambda c: RANK.get(c.candidate_level or "", 0), default=None)
        areas.append(HomeArea(adm_cd=adm, adm_nm=nm, level=top.candidate_level if top else None,
                              candidate_ids=[c.candidate_id for c in mine],
                              deviation_first=any(c.candidate_type == "unexpected_activity_deviation"
                                                  and c.counter_result != "기각" and adm in c.check_first_adm
                                                  for c in cands)))
    counts = {lv: sum(c.candidate_level == lv for c in cands) for lv in ("Level 3", "Level 2", "Level 1", "판단 보류")}
    actions = list(dict.fromkeys(c.response_type for c in cands
                                 if c.response_type and RANK.get(c.candidate_level or "", 0) >= 2))
    summary = HomeSummary(counts=counts, triggers=_triggers(evidence), holiday_watch=nodes.holiday_watch_lines(evidence),
                          actions=actions)
    return HomeResponse(as_of=as_of, city=city, snapshot_id=snapshot_id, rule_version=rule_version,
                        selection_budget=selection_budget, summary=summary, areas=areas, candidates=out_c,
                        monitoring_run_id=run_id)


def home_real(dash, as_of: date, city: str, mode: str, who) -> HomeResponse:
    """실제 백엔드: 대시보드와 같은 발행된 정기 감시 실행."""
    from api.services.dashboard_real import rules_version

    rid = dash.monitoring_run(as_of, city, mode, who)
    v = dash._values(rid)
    return build_home(as_of, city, dash._candidates(rid, city), dash._evidence(rid, city),
                      dash.runs.store.get_run(rid)["snapshot_id"], rules_version(),
                      int(v.get("selection_budget") or 0), rid)


def home_fake(as_of: date, city: str, snapshot_id: str) -> HomeResponse:
    """가짜 백엔드: 시연용 가짜 후보 (근거 없음 → 연휴 주의 블록 없음)."""
    cands = fd.candidates_for(city, as_of)
    return build_home(as_of, city, cands, [], snapshot_id, fd.FAKE_RULE_VERSION, fd.SELECTION_BUDGET[city],
                      with_resources=False)
