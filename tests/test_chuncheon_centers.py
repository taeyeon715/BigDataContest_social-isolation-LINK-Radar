"""공식 경로당 보강이 접근성 계산에 반영되고 미확인 운영·주소를 확정하지 않는지 검사."""
from pathlib import Path
import pandas as pd
from bigcontest.config import ROOT
from bigcontest.resources import ledger
from bigcontest.resources.senior_centers import rows, LIST
from bigcontest.resources.match import access_gap


def test_official_centers_survive_rebuild_without_claiming_operation():
    full = ledger.build(write=False)
    centers = full[full.src == 'chuncheon_senior_centers_2026_05']
    source = pd.read_csv(LIST, dtype=str, keep_default_na=False)
    assert len(centers) == len(source) == 372
    assert centers.resource_id.is_unique
    assert centers.operating_status.eq('미상').all() and centers.holiday_open.eq('미상').all()
    assert set(centers.adm_nm) == set(source.adm_nm)
    assert ledger.validate(full)['errors'] == []


def test_address_warning_never_gets_usable_coordinates():
    full = ledger.build(write=False)
    source = pd.read_csv(LIST, dtype=str, keep_default_na=False)
    bad_ids = set(source.loc[source.address_warning != '', 'source_record_id'])
    bad = full[full.source_record_id.isin(bad_ids)]
    assert len(bad) == len(bad_ids) > 0
    assert bad.lat.eq('').all() and bad.lon.eq('').all()
    assert bad.coordinate_status.eq('미확인').all()


def test_public_center_counterexamples_remove_elderly_distance_threshold():
    full = ledger.build(write=False)
    base = full[full.src != 'chuncheon_senior_centers_2026_05']
    names = pd.read_csv(ROOT/'ref/adm_cd_names.csv', dtype=str).set_index('adm_nm')
    for nm in ['남면','남산면','북산면']:
        n = names.loc[nm]; origin = (float(n.lat),float(n.lon))
        assert access_gap(base,n.adm_cd,origin,'안부·관계','고령층')['nearest_km'] > 5
        assert access_gap(full,n.adm_cd,origin,'안부·관계','고령층')['nearest_km'] < 5
        # 경로당을 중장년·청년 대상으로 확대하지 않는다.
        assert access_gap(base,n.adm_cd,origin,'안부·관계','청년 남성')['nearest_km'] == access_gap(full,n.adm_cd,origin,'안부·관계','청년 남성')['nearest_km']
