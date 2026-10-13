# 참고 자료

| 파일 | 내용 | 출처 |
|---|---|---|
| `adm_cd_names.csv` | 행정동 코드(통계청 8자리 `adm_cd`, 행안부 10자리 `adm_cd2`)와 이름. 강남 22개, 춘천 25개 | vuski/admdongkor (CC BY 4.0, 원천: 통계청 SGIS). 통신 칸 좌표로 일치 확인 |
| `adm_boundary.geojson` | 강남·춘천 47개 행정동 경계 (좌표 → 행정동 판정, 지도) | vuski/admdongkor (CC BY 4.0) |
| `adm_code_map.csv` | 원천 행정동 코드 → `adm_cd` 대응표. 칸: 원천 체계(`source_system`), 원천 코드, 대상 코드, 유효 기간, 변경 유형, 근거 | 직접 작성. `mois_adm_cd2`(주민등록 10자리), `seoul_living_adm7`(서울 시민생활 7자리) |

- 통신 `BLOCK_CD` 앞 8자리 = `adm_cd`
- `11230740`은 개포3동(옛 일원2동)
- 행정동 코드는 `adm_code_map.csv` 한 곳에서만 연결합니다. 앞자리를 잘라 맞추지 않습니다(`external.map_codes`). 결합할 때 키 중복, 미매칭, 일대다, 양방향 일대일, 결합 전후 인구 합계를 검사합니다(`tests/test_joins.py`).
- `valid_from`~`valid_to`는 그 대응을 확인한 자료 기간(2025년 7~12월)입니다. 코드가 처음 생긴 날이 아닙니다.
- 시민생활 원본은 `1123074`를 옛 이름 **일원2동**으로 적습니다. 2022-12-23 개포3동으로 이름만 바뀌었고 코드는 같습니다.
