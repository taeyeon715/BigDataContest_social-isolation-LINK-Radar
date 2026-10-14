"""현황·지도·후보 목록·자원 조회 (실제 그래프). `LINKRADAR_BACKEND=real` 일 때 씀.

읽는 곳 (API 명세 §5)
- 후보·트리거·지도 신호: 활성 스냅샷 · 기준일 · 도시 · 분석 모드가 같은 **발행된 정기 감시 실행**
  (도시 하나, 검토 예산 K). 없으면 요청 안에서 한 번 돌려 발행한다 (규칙·템플릿만, LLM 없음 → 비용 없음, 등급은 규칙).
  같은 열쇠의 정기 감시가 아직 도는 중이면 409 RUN_NOT_READY. 응답의 monitoring_run_id 가 그 실행이고,
  후보 ID 는 그 실행의 ID 라 POST /runs (candidate_id) 가 그대로 받는다.
- 새로 생긴 / 해제된 후보: 7일 전 기준일의 정기 감시와 후보 열쇠(종류·도시·동·코호트, 예정된 점검은 사건)로 비교.
  후보 ID 는 실행마다 새로 매기므로 ID 로 비교하지 않는다. 해제 사유는 트리거·경보가 이번 기준일에도 있는지로 정함
- 후보 수 범위: 같은 근거에 임계값만 바꿔(rules.sensitivity.THRESHOLD_GRID) 다시 낸 후보 수의 최소·최대
- 지도: 1인세대 비율과 도시 안 순위(도시 × 기준월 전체 모집단), 구조 상위 여부(rules.yaml)는 정기 감시 근거에서,
  행동 신호는 그 실행의 후보에서. 자원 점은 원장 v2 (출처·확인일 없는 것, 운영 중단, 관할 밖 물리 거점은 뺌)
- 자원 조회: 원장 v2 + find_resources 와 같은 매칭·제외 규칙 (resources.match). 동을 주면 거리, 도시만 주면 도시 전체.
  원인 유형(cause_type)을 주면 그 필요 유형 자원만. 후보 목록은 후보마다 원인 유형·필요 유형을 같이 줌
  (조사 실행의 match_resources 와 같은 nodes.candidate_cause)
"""

from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from functools import lru_cache

from bigcontest.agent.schemas import (
    ASSUMED_AVAILABILITY_LABEL,
    DATA_START,
    Candidate,
    CandidateCountRange,
    CandidateListResponse,
    MapArea,
    MapResource,
    MapResponse,
    NormalizedRequest,
    OverviewResponse,
    ReleasedCandidate,
    ResourceListResponse,
    ResourceRecommendation,
    ToolResult,
    TriggerInfo,
    ValueState,
)
from bigcontest.agent import nodes
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.common import city_of_evidence, req_with_city
from bigcontest.rules import candidates as RC
from bigcontest.rules import load_rules, rules_version

from api.auth import DEV_IDENTITY, Identity
from api.errors import ApiError
from api.services import fake_data as fd
from api.services.agent_runs import AgentRunService

STRUCTURE_METRIC = "1인세대 비율"
ACCESS_METRIC = "관측 거점까지 직선거리"
TRIGGER_METRIC = "달력 트리거"
COMPARE_DAYS = 7
RESOURCE_LIMIT = 50
CACHE_RUNS = 8
TYPE_KO = {"scheduled_access_review": "예정된 점검", "unexpected_activity_deviation": "예상 밖 이탈"}


def cand_key(c: Candidate) -> tuple:
    """주 사이 비교 열쇠. 예정된 점검은 사건(event_id)까지, 이탈은 도시 × 코호트 (관측 끝 날짜는 주마다 바뀜)."""
    if c.candidate_type == "scheduled_access_review":
        return (c.candidate_type, c.city, c.adm_cd, c.analysis_cohort, c.event_id)
    return (c.candidate_type, c.city, c.analysis_cohort)


def _codes(trigger: str | None) -> list[str]:
    return [code for part in (trigger or "").split(";") if (code := RC.trigger_code(part.strip()))]


class RealDashboard:
    def __init__(self, runs: AgentRunService):
        self.runs = runs
        self._locks: dict[tuple, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()
        self._ranges: dict[str, dict] = {}
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------ 정기 감시 실행 고르기

    def _lock(self, key: tuple) -> threading.Lock:
        with self._guard:
            return self._locks[key]

    def _find(self, as_of: date, city: str, mode: str, sid: str, statuses: tuple[str, ...]) -> str | None:
        for r in self.runs.store.monitoring_runs(as_of.isoformat(), mode):
            if r["snapshot_id"] != sid or r["status"] not in statuses:
                continue
            if self.runs._run_city(r["run_id"]) == city:
                return r["run_id"]
        return None

    def monitoring_run(self, as_of: date, city: str, mode: str, who: Identity = DEV_IDENTITY) -> str:
        """발행된 정기 감시 실행 id. 없으면 지금 돌려 발행 (같은 열쇠는 한 번만)."""
        sid = self.runs.snapshots.active().snapshot_id  # 없으면 503
        with self._lock((as_of, city, mode, sid)):
            rid = self._find(as_of, city, mode, sid, ("published",))
            if rid:
                return rid
            if self._find(as_of, city, mode, sid, ("queued", "running")):
                raise ApiError(409, "RUN_NOT_READY", "이 기준일의 정기 감시 실행이 아직 끝나지 않았습니다. 잠시 뒤 다시 보세요.")
            rid = self.runs.monitor_now(as_of, city, mode, who)
            if self.runs.store.get_run(rid)["status"] != "published":
                raise ApiError(500, "INTERNAL_ERROR", "정기 감시 실행을 끝내지 못했습니다.")
            return rid

    def _values(self, run_id: str) -> dict:
        """발행된 정기 감시 실행의 그래프 상태 (발행 뒤에는 바뀌지 않으므로 몇 개만 기억)."""
        with self._guard:
            v = self._cache.get(run_id)
        if v is None:
            v = self.runs._values(run_id)
            with self._guard:
                if len(self._cache) >= CACHE_RUNS:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[run_id] = v
        return v

    def _candidates(self, run_id: str, city: str) -> list[Candidate]:
        return [Candidate.model_validate(c) for c in self._values(run_id).get("candidates") or [] if c["city"] == city]

    def _evidence(self, run_id: str, city: str) -> list[ToolResult]:
        out = []
        for e in self._values(run_id).get("evidence") or []:
            ev = ToolResult.model_validate(e)
            if (city_of_evidence(ev) or city) == city:
                out.append(ev)
        return out

    # ------------------------------------------------------------------ 후보 목록

    def candidates(self, as_of: date, city: str, mode: str, who: Identity) -> CandidateListResponse:
        rid = self.monitoring_run(as_of, city, mode, who)
        v = self._values(rid)
        evidence = self._evidence(rid, city)
        items = [with_cause(c, nodes.candidate_cause(c, evidence)) for c in self._candidates(rid, city)]
        items.sort(key=lambda c: (c.queue_rank is None, c.queue_rank or 0, c.candidate_id))
        return CandidateListResponse(as_of=as_of, city=city, snapshot_id=self.runs.store.get_run(rid)["snapshot_id"],
                                     selection_budget=int(v.get("selection_budget") or 0), items=items,
                                     monitoring_run_id=rid)

    # ------------------------------------------------------------------ 현황

    def _triggers(self, evidence: list[ToolResult], as_of: date, city: str) -> list[TriggerInfo]:
        out: dict[str, TriggerInfo] = {}
        for e in evidence:
            if e.metric != TRIGGER_METRIC or e.status != "OK":
                continue
            text = str(e.value)
            code = RC.trigger_code(text) or "R?"
            tid = f"TR-{as_of:%Y%m%d}-{code}"
            out.setdefault(tid, TriggerInfo(trigger_id=tid, rule=code, name=text.removeprefix(code).strip() or text,
                                            city=city, starts_on=e.observed_from or as_of,
                                            note=e.warnings[0] if e.warnings else None))
        return list(out.values())

    def _count_ranges(self, run_id: str, city: str, cands: list[Candidate]) -> dict[str, CandidateCountRange]:
        """같은 근거에 임계값만 바꿔 다시 낸 후보 수 (기각 제외)의 최소·최대. 등급은 바꾸지 않는다 (계산만)."""
        if run_id not in self._ranges:
            from bigcontest.rules.compare import run_rules
            from bigcontest.rules.sensitivity import THRESHOLD_GRID

            v = self._values(run_id)
            ev = self._evidence(run_id, city)
            as_of = date.fromisoformat(v["as_of"])
            req = req_with_city(NormalizedRequest(**v["request"]), city)
            seen: dict[str, list[int]] = defaultdict(list)
            for path, values in THRESHOLD_GRID.items():
                for x in values:
                    res = run_rules(ev, req, as_of, int(v.get("selection_budget") or 0), load_rules({path: x}))
                    for kind in TYPE_KO:
                        seen[kind].append(sum(c.candidate_type == kind and c.counter_result != "기각"
                                              for c in res.candidates))
            self._ranges[run_id] = dict(seen)
        out = {}
        for kind in TYPE_KO:
            n = sum(c.candidate_type == kind and c.counter_result != "기각" for c in cands)
            vals = self._ranges[run_id].get(kind, []) + [n]
            out[kind] = CandidateCountRange(count=n, low=min(vals), high=max(vals),
                                            note="임계값에 따라 달라지는 수 (rules.yaml 임계값 ± 로 다시 계산)")
        return out

    def _release_reason(self, old: Candidate, now_codes: set[str], now_dev: set[str]) -> str:
        if old.candidate_type == "scheduled_access_review":
            codes = _codes(old.trigger)
            if codes and not set(codes) & now_codes:
                return f"트리거 기간이 끝남 ({old.trigger})"
            return "트리거는 이어지지만 이번 기준일 근거로는 이 동이 진입 조건(구조 상위·접근성)을 넘지 않음"
        if old.analysis_cohort not in now_dev:
            return f"이번 기준일에는 {old.analysis_cohort} 결제 이탈 경보가 없음"
        return "이번 기준일 근거로는 후보가 되지 않음"

    def overview(self, as_of: date, city: str, mode: str, who: Identity) -> OverviewResponse:
        rid = self.monitoring_run(as_of, city, mode, who)
        cands = self._candidates(rid, city)
        evidence = self._evidence(rid, city)
        triggers = self._triggers(evidence, as_of, city)
        prev_day = as_of - timedelta(days=COMPARE_DAYS)
        new_ids, released, compared = [], [], None
        if prev_day >= DATA_START:
            compared = prev_day
            old = self._candidates(self.monitoring_run(prev_day, city, mode, who), city)
            old_keys = {cand_key(c) for c in old}
            now_keys = {cand_key(c) for c in cands}
            new_ids = sorted(c.candidate_id for c in cands if cand_key(c) not in old_keys)
            now_codes = {t.rule for t in triggers}
            now_dev = {c.analysis_cohort for c in cands if c.candidate_type == "unexpected_activity_deviation"}
            for c in old:
                if cand_key(c) in now_keys:
                    continue
                released.append(ReleasedCandidate(
                    candidate_id=c.candidate_id, candidate_type=c.candidate_type, city=c.city, adm_cd=c.adm_cd,
                    check_first_adm=c.check_first_adm, analysis_cohort=c.analysis_cohort,
                    service_eligibility_group=c.service_eligibility_group, candidate_level=c.candidate_level,
                    trigger=c.trigger, last_seen_as_of=prev_day,
                    release_reason=self._release_reason(c, now_codes, now_dev)))
        return OverviewResponse(
            as_of=as_of, city=city, snapshot_id=self.runs.store.get_run(rid)["snapshot_id"], analysis_mode=mode,
            triggers=triggers, candidate_counts=self._count_ranges(rid, city, cands), new_candidate_ids=new_ids,
            released_candidate_ids=[r.candidate_id for r in released], released_candidates=released,
            compared_as_of=compared, monitoring_run_id=rid, rule_version=rules_version(),
            time_label=ASSUMED_AVAILABILITY_LABEL if mode == "simulated_replay" else None)

    # ------------------------------------------------------------------ 지도

    def map_view(self, as_of: date, city: str, mode: str, who: Identity) -> MapResponse:
        rid = self.monitoring_run(as_of, city, mode, who)
        cands = self._candidates(rid, city)
        evidence = self._evidence(rid, city)
        structs = [e for e in evidence if e.metric == STRUCTURE_METRIC and e.status == "OK"]
        facts = S.structure_facts(structs)
        state = {S.adm_of(e): e.value_state for e in evidence if e.metric == STRUCTURE_METRIC}
        access = {S.adm_of(e): float(e.value) for e in evidence
                  if e.metric == ACCESS_METRIC and e.status == "OK" and isinstance(e.value, (int, float))}
        scheduled = {c.adm_cd for c in cands if c.candidate_type == "scheduled_access_review" and c.adm_cd}
        shared = {cd for c in cands if c.candidate_type == "unexpected_activity_deviation" and c.counter_result != "기각"
                  for cd in c.check_first_adm}
        areas = []
        for a in sorted(fd.city_areas(city), key=lambda a: a.adm_cd):
            f = facts.get(a.adm_cd)
            sig = "예정된 점검" if a.adm_cd in scheduled else ("이탈 경보(공유 맥락)" if a.adm_cd in shared else "없음")
            vs = state.get(a.adm_cd) or ValueState.MISSING
            areas.append(MapArea(
                adm_cd=a.adm_cd, adm_nm=a.adm_nm, structure_value=round(f.value, 4) if f else None,
                structure_rank=f.rank if f else None, structure_top=f.top if f else None, behavior_signal=sig,
                is_shared_context=sig == "이탈 경보(공유 맥락)",
                nearest_hub_km=round(access[a.adm_cd], 2) if a.adm_cd in access else None,
                value_state=vs if f is None else ValueState.OBSERVED))
        return MapResponse(as_of=as_of, city=city, snapshot_id=self.runs.store.get_run(rid)["snapshot_id"],
                           structure_metric="주민등록 1인세대 비율", areas=areas, resources=map_resources(city),
                           monitoring_run_id=rid, rule_version=rules_version())

    # ------------------------------------------------------------------ 자원 조회

    def resources(self, city: str | None, adm_cd: str | None, need_type: str | None, group: str | None,
                  limit: int = RESOURCE_LIMIT, cause_type: str | None = None) -> ResourceListResponse:
        view = _ledger_view()
        items = find_ledger_resources(city, adm_cd, need_type, group, cause_type, view=view)
        scope_total = None
        if cause_type and not need_type:  # "필요 유형에 맞는 m건 / 관할·대상 n건"
            scope_total = len(find_ledger_resources(city, adm_cd, None, group, view=view))
        return ResourceListResponse(adm_cd=adm_cd, need_type=need_type, group=group, items=items[:limit],
                                    ledger_version=view.version, total=len(items), cause_type=cause_type,
                                    need_types=needs_of(cause_type, need_type), scope_total=scope_total)


# ---------------------------------------------------------------- 원장 v2 (find_resources 와 같은 규칙)


LEDGER_VIEWS = 2      # 원장 판(파일 해시)을 몇 개까지 들고 있을지
RESOURCE_RESULTS = 256  # 자원 조회 결과 캐시 (원장 판마다)


class _LedgerView:
    """원장 한 판(파일 해시)에서 읽은 표와 미리 만든 것: resource_id 색인, 도시별 부분표·행 dict, 조회 결과.
    키가 원장 파일 해시라 원장을 바꾸면 서버를 다시 켜지 않아도 다음 요청부터 새 판을 읽는다."""

    def __init__(self, digest: str, led):
        from bigcontest.agent.tools import welfare as W

        self.digest, self.led = digest, led
        self.version = f"ledger@sha256:{digest[:12]}"  # runmeta.ledger_version 과 같은 값
        self.rows = led.set_index("resource_id")
        self.note = f"현재 원장(수집 {W.ledger_collected(led)}) 기준"
        self._lock = threading.Lock()
        self._city: dict[str, tuple] = {}
        self._results: OrderedDict[tuple, tuple] = OrderedDict()

    def city(self, c: str) -> tuple:
        """(도시 부분표, 그 행 dict 목록)."""
        from bigcontest.resources import match as M

        with self._lock:
            if c not in self._city:
                sub = self.led[self.led["city"] == c]
                self._city[c] = (sub, M.ledger_records(sub))
            return self._city[c]

    def result(self, key: tuple, fn) -> tuple:
        with self._lock:
            if key in self._results:
                self._results.move_to_end(key)
                return self._results[key]
        val = fn()  # 잠금 밖에서 계산 (같은 키를 두 번 계산해도 결과는 같음)
        with self._lock:
            self._results[key] = val
            while len(self._results) > RESOURCE_RESULTS:
                self._results.popitem(last=False)
        return val


_VIEWS: OrderedDict[str, _LedgerView] = OrderedDict()
_VIEWS_LOCK = threading.Lock()


def _ledger_view() -> _LedgerView:
    """지금 원장 파일(원장 v2) 판. 파일 해시는 (경로, 수정 시각, 크기)로 캐시돼 싸다."""
    from bigcontest.agent.tools.datasource import file_hash
    from bigcontest.resources.ledger import V2, load

    digest = file_hash(V2)
    with _VIEWS_LOCK:
        if digest in _VIEWS:
            _VIEWS.move_to_end(digest)
            return _VIEWS[digest]
    view = _LedgerView(digest, load(V2))
    with _VIEWS_LOCK:
        view = _VIEWS.setdefault(digest, view)
        _VIEWS.move_to_end(digest)
        while len(_VIEWS) > LEDGER_VIEWS:
            _VIEWS.popitem(last=False)
    return view


def _ledger():
    return _ledger_view().led


def need_types() -> tuple[str, ...]:
    from bigcontest.resources.match import load_needs

    return tuple(load_needs()["need_types"])


def needs_of(cause_type: str | None, need_type: str | None = None) -> list[str]:
    """원인 유형 → 필요 유형 (configs/need_types.yaml cause_types, '+'로 이은 것은 합침). 모르는 원인이면 KeyError."""
    from bigcontest.resources import match as M

    needs, _ = M._needs_for(need_type, cause_type, M.load_needs())
    return list(needs or [])


def with_cause(c: Candidate, cause: str) -> Candidate:
    return c.model_copy(update={"cause_type": cause, "need_types": needs_of(cause)})


def find_ledger_resources(city: str | None, adm_cd: str | None, need_type: str | None,
                          group: str | None, cause_type: str | None = None,
                          view: _LedgerView | None = None) -> list[ResourceRecommendation]:
    """resources.match 의 매칭(관할·대상·필요 유형·운영 상태·거리) + 제외 규칙(확인일·출처 없음, 관할 밖 물리 거점).
    위기 전화는 맨 아래 '위급 시 안내'. 동을 주면 그 동 중심점에서 직선거리.
    결과는 원장 판(파일 해시)마다 (도시, 동, 필요 유형, 집단, 원인 유형) 키로 캐시. 돌려줄 때는 항목을 얕게 복사
    (응답 쪽에서 칸을 바꿔도 캐시는 그대로)."""
    view = view or _ledger_view()
    key = (city, adm_cd, need_type, group, cause_type)
    return [r.model_copy() for r in view.result(key, lambda: tuple(_find_ledger_resources(view, *key)))]


def _find_ledger_resources(view: _LedgerView, city, adm_cd, need_type, group, cause_type):
    from bigcontest.agent.tools import welfare as W
    from bigcontest.resources import match as M

    base = fd.areas().get(adm_cd) if adm_cd else None
    cities = [base.city] if base else ([city] if city else ["강남", "춘천"])
    note = view.note
    rows = view.rows
    fields = set(ResourceRecommendation.model_fields)
    ranked, crisis = [], []
    for c in cities:
        sub, recs = view.city(c)
        origin = (base.lat, base.lon) if base else None
        found = M.find_resources(sub, adm_cd, group, need_type, cause_type=cause_type, origin=origin, city=c,
                                 records=recs)
        for m in found:
            if M.hard_exclusion(m):
                continue
            label = W.CRISIS_LABEL if m.crisis else "추천 후보"
            item = W._item(m, rows.loc[m.resource_id], group if group in W.GROUPS else None, note, label)
            (crisis if m.crisis else ranked).append(ResourceRecommendation(**{k: v for k, v in item.items()
                                                                               if k in fields}))
    return ranked + crisis


def map_resources(city: str) -> list[MapResource]:
    """지도 자원 점: 좌표가 있는 거점 + 전화형. 출처·확인일 없는 것, 운영 중단, 관할 밖 물리 거점은 뺌 (G-07, 제외 규칙).
    원장 판(파일 해시)마다 캐시."""
    view = _ledger_view()
    return _map_resources(city, view.digest, view)


@lru_cache(maxsize=4)
def _map_resources(city: str, digest: str, view: _LedgerView) -> list[MapResource]:
    from bigcontest.resources import match as M
    from bigcontest.resources.ledger import SPATIAL

    led = view.led
    out = []
    for r in led[led["city"] == city].to_dict("records"):
        if not all(str(r[k]).strip() for k in ("source_url", "verified_date", "city", "target_stage")):
            continue
        if not r["last_verified"] or r["operating_status"] == "중단":
            continue
        phone = r["delivery_channel"] in M.PHONE
        spatial = r["coordinate_status"] in SPATIAL and str(r["lat"]).strip() and str(r["lon"]).strip()
        if not (phone or spatial):
            continue
        out.append(MapResource(resource_id=r["resource_id"], name=r["name"], resource_type=r["subtype"] or r["type"],
                               lat=float(r["lat"]) if spatial else None, lon=float(r["lon"]) if spatial else None,
                               phone_based=phone, resource_kind=r.get("resource_kind") or None))
    return out
