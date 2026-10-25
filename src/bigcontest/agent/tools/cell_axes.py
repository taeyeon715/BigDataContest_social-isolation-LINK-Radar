"""칸(동 × 성 × 연령) 근거 축을 기준일에 쓸 수 있던 달로 계산 (rules-v2.0 S·H·V·A).

연휴 주의(get_holiday_watch), 동절기 칸 판정, 후보 카드의 칸 근거 축 참고 칸이 모두 이 한 계산을 쓴다
(같은 지표가 화면마다 다른 값으로 나오지 않게). 기준일·모드에서 공개된 주민등록·통신 달이 없으면 None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from bigcontest.agent.tools.common import usable_months

ACCESS_GROUPS = ("고령층", "중장년 남성 1인가구", "청년 남성")  # holiday_watch.AGE_GROUP 값


@dataclass(frozen=True)
class AxesAt:
    df: pd.DataFrame  # holiday_watch.cell_axes 결과 (두 도시 470칸)
    res_month: int
    tel_months: tuple[int, ...]
    observed_from: date
    observed_to: date
    simulated_available_at: date | None

    @property
    def months_label(self) -> str:
        return f"주민등록 {self.res_month}월, 통신 {'·'.join(map(str, self.tel_months))}월"


def access_km(ds) -> dict[tuple[str, str], float]:
    """두 도시 동마다 집단별 안부·관계 거점 직선거리 (welfare.get_access_gap 과 같은 계산·캐시)."""
    from bigcontest.agent.tools.welfare import ACCESS_NEED, _ledger_city, _origin
    from bigcontest.resources import match as M

    names = ds.adm_names()
    out = {}
    for city in names["city"].dropna().unique():
        sub, recs = _ledger_city(ds, city)
        for adm in names.loc[names["city"] == city, "adm_cd"]:
            origin = _origin(ds, adm)
            if origin is None:
                continue
            for grp in ACCESS_GROUPS:
                g = ds.cached(("access", adm, ACCESS_NEED, grp),
                              lambda adm=adm, origin=origin, grp=grp:
                              M.access_gap(sub, adm, origin, ACCESS_NEED, grp, records=recs))
                if g["nearest_km"] is not None:
                    out[(adm, grp)] = float(g["nearest_km"])
    return out


_SHARED: dict[tuple, pd.DataFrame] = {}  # (스냅샷, 주민등록 달, 통신 달, 강남 V 공개 여부) → 470칸 축 (DataSource 를 다시 열어도 씀)
_SHARED_MAX = 16


def _cell_frame(ds, res_m: int, tel_m: tuple[int, ...], as_of: date) -> pd.DataFrame:
    """축은 기준일이 아니라 '쓸 수 있던 달 조합'과 강남 V 공개 여부로만 정해진다 → 그 열쇠로 한 번만 계산."""
    from bigcontest import holiday_watch as HW
    from bigcontest.rules.levels_v2 import load_rules_v2

    pub = load_rules_v2()["axes"]["v_published"].get("강남")
    gn_ok = pub is None or as_of >= date.fromisoformat(pub)
    key = (ds.snapshot_id, res_m, tel_m, gn_ok)
    if key not in _SHARED:
        if len(_SHARED) >= _SHARED_MAX:
            _SHARED.pop(next(iter(_SHARED)))
        # 1인세대·수급 통계도 스냅샷이 고정한 파일에서 읽는다 (입력 해시·변경 검사에 들어감)
        _SHARED[key] = HW.cell_axes(ds.resident_pop(), ds.resident_single(), ds.tel("tel_age"), ds.tel("tel_time"),
                                    res_m, list(tel_m), as_of, access_km(ds), v_inputs=ds.v_inputs())
    return _SHARED[key]


def axes_at(ds, as_of: date, mode: str) -> AxesAt | None:
    """기준일·모드에서 쓸 수 있던 달로 470칸 축. 같은 달 조합이면 다시 계산하지 않는다."""
    res = usable_months("resident_hh", as_of, mode)
    tel = usable_months("tel_age", as_of, mode)
    if not res or not tel:
        return None
    res_m, tel_m = int(res[-1].base_month[4:]), tuple(sorted(int(m.base_month[4:]) for m in tel))
    items = [res[-1], *tel]
    sim = [i.simulated_available_at for i in items if i.simulated_available_at]
    return AxesAt(_cell_frame(ds, res_m, tel_m, as_of), res_m, tel_m, min(i.observed_from for i in items),
                  max(i.observed_to for i in items), max(sim) if sim else None)
