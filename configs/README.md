# 설정

판단 기준을 코드 밖에서 보고 고칠 수 있게 YAML로 둡니다.

| 파일 | 내용 |
|---|---|
| `project.yaml` | 원본 위치(`raw_dir`, 환경 변수 `BC_RAW_DIR`이 먼저), 분석 대상 시군구, 공휴일 |
| `grain_contract.yaml` | 데이터별 단위 계약: 같이 쓸 수 있는 차원, 측정 단위, 공개 시점 |
| `cohorts.yaml` | 연령 계약: 분석 코호트와 서비스 대상 집단 |
| `candidate_inputs.yaml` | 후보 종류별 필수 근거 / 선택 맥락 |
| `rules.yaml` | 후보·등급 규칙 (rules-v1.1): 임계값, 진입·해제, 연휴 뒤 기대 경로, 청년 남성 도시 단위 안내, 카드 맥락(연휴 외지 손님), 폭염 특보 단계, 표시만 하는 칸 |
| `guard_rules.yaml` | Guard 규칙 G-01~G-14 |
| `action_templates.yaml` | 확인할 영역, 대응 템플릿, 체크리스트 문구 |
| `need_types.yaml` | 필요 유형 사전과 원인 유형 → 자원 매칭 |
| `resource_collect.yaml` | 자원 수집 호출 한도·재시도 |
| `models.yaml` | 기대 활동 모델 설정과 채택 결정 (채택 기준은 비교 전에 고정) |
| `llm.yaml` | Claude 모델 ID·가격·자리별 프롬프트 판 |
| `prompts/` | LLM 프롬프트 (파일 이름에 판 번호. 쓰는 판은 `llm.yaml`이 정함) |
