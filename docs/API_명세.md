# API 명세

LINK-Radar 화면(React)과 에이전트가 쓰는 REST API(FastAPI)입니다. 예시 JSON은 가짜 API 응답과 테스트(`tests/test_schemas.py`)에도 그대로 씁니다.

- 요청·응답 모양은 `src/bigcontest/agent/schemas.py`의 Pydantic 모델과 같습니다. 이 문서의 JSON 예시는 `tests/test_schemas.py`가 모델로 검사합니다 (예시 위의 `<!-- schema: 모델 -->` 표시).
- 근거 한 건의 필드(반환 봉투 `ToolResult`)는 [에이전트 설계 §5.1](에이전트_설계.md#51-반환-봉투-toolresult--기준-정의)이 기준입니다.
- 실행 상태를 쓰는 곳은 이 API 하나입니다. 저장 테이블은 [데이터 모델](데이터_모델.md)에 있습니다.

## 1. 공통 규칙

| 항목 | 규칙 |
|---|---|
| 주소 | `http://localhost:8000` (개발). 경로 앞에 버전은 붙이지 않음 |
| 형식 | JSON, UTF-8. 날짜는 `YYYY-MM-DD`, 시각은 ISO 8601 |
| 도시 | `강남`, `춘천`만 받음 |
| 기준일 `as_of` | `2025-07-01` ~ `2025-12-31` |
| 분석 모드 `analysis_mode` | `retrospective` / `simulated_replay`(기본) / `strict_as_of` |
| 스냅샷 | 실행은 시작할 때 활성 `snapshot_id`를 고정하고 끝까지 같은 스냅샷을 읽음. 조회 응답에도 `snapshot_id`가 있음 |
| 고지 | 화면에 늘 "개인 판정 아님 · 최종 판단은 담당자". `simulated_replay` 결과에는 "가정한 공개 시점" 표시(`time_label`)가 붙음 |
| 오래 걸리는 일 | `POST /runs`는 바로 202와 `run_id`를 주고, 화면이 `GET /runs/{id}`로 상태를 봄 |
| 사실이 든 문장 | Guard 사실 검사가 끝난 뒤에만 응답에 넣음. 먼저 보내고 나중에 가리지 않음 |
| 오류 | 오류 코드와 사유만 보냄. 내부 예외 문장은 보내지 않음 (§4) |
| CORS | 화면 주소만 허용 |
| 인증 | `Authorization: Bearer <토큰>`. 역할은 조회 < 검토 < 승인. 조회는 읽기만. **실행 만들기(`POST /runs`)·질의응답(`POST /qa`)·이어 묻기(`POST /runs/{id}/followup`)는 LLM 비용을 쓰므로 검토 이상**, 검토 저장·현장 결과도 검토 이상, 승인·보류·추가 조사는 승인 역할. 역할이 모자라면 403 `FORBIDDEN`. `/health`만 토큰 없이 열림. 토큰 설정이 없으면 개발 모드(모두 허용, 감사 actor `dev-unauthenticated`). 검토자·승인자 이름과 감사 actor는 서버가 토큰으로 정함(본문 이름은 `claimed_actor`로만 남김). 토큰 설정은 아래 "토큰 설정" |
| 횟수 한도 | 실행 만들기·질의응답·이어 묻기는 사용자(토큰 이름)마다 `LINKRADAR_RUN_RATE`(`<횟수>/<초>`, prod 기본 `20/600`, dev 는 줄 때만) 안에서만 받음. 넘으면 429 `RATE_LIMITED` + `Retry-After`(초). 서버 메모리에만 세므로 다시 켜면 비워짐 |
| 실행 예산 | 실행 하나의 LLM 호출 수·비용 한도는 `configs/llm.yaml`의 `budget`(`max_calls_per_run`, `max_cost_usd_per_run`). 그래프 실행·검토 반영·추가 조사·이어 묻기가 같은 예산을 나눠 씀. 넘으면 LLM을 부르지 않고 규칙·템플릿으로 대신하고 `llm_calls`에 `error_code=BUDGET_EXCEEDED`(status `fallback`)를 남김. 응답 모양은 같음 |
| 개인정보 저장 | 질문(`runs.question`, 실행 입력)·검토 메모·자원 판정 메모·승인 메모는 **저장하기 전에** 전화번호·주민번호·이메일·상세 주소·이름+호칭을 `[전화번호]`·`[이름]` 등으로 가림. 실제 그래프에도 가린 질문만 넘기므로 체크포인트에도 원문이 없음. 가린 표시가 든 질문은 개인 지목 규칙에 걸려 `OUT_OF_SCOPE_INDIVIDUAL`로 보류(예: 도로명 주소가 든 질문도 보류됨). 조회 응답(`/runs`, `/history`, `/followups`)에도 가린 글이 나감. 보존 기한(`LINKRADAR_TEXT_RETENTION_DAYS`, 기본 365일)이 지난 질문·메모 글은 `uv run python -m api.retention --db <상태 DB>`(`--dry-run` 가능)로 비움(행은 남기고 글 칸만 NULL, 감사 `text_purged`). 이어 묻기 질문은 저장하지 않음 |
| 입력 한도 | 본문 64KB(넘으면 413 `PAYLOAD_TOO_LARGE`), 질문 500자, 메모 2000자, 이름 50자, 멱등 키 영문·숫자·`:._-` 100자, 목록 50개 |
| 추적 | 응답 헤더 `X-Request-ID`, 요청마다 JSON 로그 한 줄(질문 원문·메모·토큰은 남기지 않음. 칸: 시각·경로·상태·소요 시간·`run_id`·오류 코드·actor). 감사 로그에도 질문·메모는 넣지 않음 |
| 자료 출처 | 응답 헤더 `X-Data-Source`: `fake`(가짜 API) / `real`(실제 그래프) / `config`(설정 파일). 서버 환경 변수 `LINKRADAR_BACKEND=real`이면 실행·질의응답·이어 묻기·스냅샷과 현황·지도·후보 목록·자원 조회·후속 확인·현장 결과(실행별·모아 보기 `/outcomes/summary`)가 모두 `real`, `/data/capabilities`만 `config` (§5). 모든 경로의 표지는 `tests/test_source_header.py`가 확인 |

### 토큰 설정

| 방식 | 설정 | 쓰는 곳 |
|---|---|---|
| 사람별 토큰 (권장) | `LINKRADAR_API_TOKENS_FILE=<yaml>` (`tokens: {<토큰>: {name: worker-01, role: 검토}}`, 레포에 올리지 않음) | 담당자마다 하나. 검토자·승인자·감사 actor가 그 이름 |
| 공용 토큰 | `LINKRADAR_API_TOKEN`, 이름 `LINKRADAR_API_USER`(기본 `demo`), 역할 `LINKRADAR_API_ROLE`(기본 **조회**) | 시연·읽기 전용 화면. 기록 이름·actor는 `공용:<이름>` (한 사람 이름처럼 보이지 않게). `LINKRADAR_ENV=prod`에서 검토·승인을 주면 서버가 시작하지 않음 |

> ⚠ 화면 빌드의 `VITE_API_TOKEN`은 JS 번들에 그대로 들어가 화면을 여는 **누구나 개발자 도구로 볼 수 있습니다.** 여기에는 조회 역할 공용 토큰만 넣습니다. 운영(파일럿)에서는 번들에 토큰을 넣지 말고, 앞단 프록시(nginx 등)에서 기관 로그인(SSO·LDAP)이나 최소 로그인 화면을 거친 뒤 프록시가 사람별 토큰(`Authorization`)을 붙이게 합니다. 토큰 파일 방식은 그 프록시·서버 간 호출용입니다. 개발 모드(토큰 설정 없음)는 모두 허용하되 감사 actor가 `dev-unauthenticated`로 남습니다.

## 2. 엔드포인트 목록

| 메서드 | 경로 | 내용 | 응답 모델 |
|---|---|---|---|
| GET | `/health` | 서버·활성 스냅샷 확인 | `HealthResponse` |
| GET | `/me` | 지금 토큰의 이름·역할·공용 여부 (화면이 버튼 잠금에 씀) | `MeResponse` |
| GET | `/snapshots/active` | 활성 스냅샷, 데이터 기간, 규칙·원장 버전 | `SnapshotInfo` |
| GET | `/overview?as_of=&city=` | 현황: 트리거, 후보 수(범위), 새로 생긴/해제된 후보 | `OverviewResponse` |
| GET | `/map?as_of=&city=` | 행정동별 구조 취약성 × 행동 신호, 자원 위치 | `MapResponse` |
| GET | `/candidates?as_of=&city=&type=&level=&analysis_mode=` | 후보 목록 (배정·부분 판정 포함) | `CandidateListResponse` |
| POST | `/runs` | 조사 실행 시작 → 202 | `RunAccepted` |
| GET | `/runs/{id}` | 실행 상태, 경로, Guard 판정, 후보 | `RunStatusResponse` |
| GET | `/runs/{id}/evidence` | 근거 카드 | `EvidenceResponse` |
| POST | `/runs/{id}/review` | 근거 확인, 자원별 판정·메모, 후속 확인일 | `ReviewResponse` |
| POST | `/runs/{id}/approve` | 승인 / 보류 / 추가 조사 (`report_revision` 필수) | `ApproveResponse` |
| POST | `/qa` | 질의응답 (검사가 끝난 뒤 한 번에 응답) | `QAResponse` |
| GET | `/resources?adm_cd=&need_type=&group=&city=&limit=&cause_type=` | 자원 조회 | `ResourceListResponse` |
| GET | `/runs/{id}/resources` | 이 실행에 묶인 추천 자원 + 담당자 판정 | `RunResourcesResponse` |
| POST | `/runs/{id}/followup` | 이어 묻기 (이 실행 근거 카드 안에서만) | `FollowupResponse` |
| GET | `/data/capabilities` | 데이터별 지원 차원·기간 | `CapabilitiesResponse` |
| GET | `/followups?city=&week=&from=&to=&as_of=` | 후속 확인 모아 보기 | `FollowUpListResponse` |
| POST | `/runs/{id}/outcomes` | 현장 확인 결과 적기 (승인된 실행만, 건수만) | `OutcomeResponse` |
| GET | `/runs/{id}/outcomes` | 이 실행의 현장 확인 결과 | `OutcomeListResponse` |
| GET | `/outcomes/summary?city=&by=` | 현장 확인 결과 모아 보기 (10 미만 숨김) | `OutcomeSummaryResponse` |

## 3. 엔드포인트 상세

### 3.1 `GET /health`

서버가 떠 있는지, 읽을 스냅샷이 있는지 봅니다. 활성 스냅샷이 없으면 `status=degraded`.

<!-- schema: HealthResponse -->
```json
{
  "status": "ok",
  "active_snapshot_id": "snap-20261013-101500-a1b2",
  "time": "2026-10-13T10:20:00+09:00"
}
```

### 3.2 `GET /snapshots/active`

지금 화면이 보는 분석 스냅샷입니다. 새 스냅샷이 발행돼도 진행 중인 실행은 자기 `snapshot_id`를 계속 씁니다.

<!-- schema: SnapshotInfo -->
```json
{
  "snapshot_id": "snap-20261013-101500-a1b2",
  "created_at": "2026-10-13T10:15:00+09:00",
  "data_from": "2025-07-01",
  "data_to": "2025-12-31",
  "cleaning_rule_version": "clean-v1",
  "rule_version": "rules-v0.1",
  "ledger_version": "ledger@sha256:3f9a1c0b7d22",
  "previous_snapshot_id": "snap-20261012-180000-9c0d",
  "row_counts": {"candidates.parquet": 42, "analysis.duckdb:card_cohort_daily": 165600}
}
```

없으면 `503 NO_ACTIVE_SNAPSHOT`.

### 3.3 `GET /overview`

| 파라미터 | 필수 | 예 |
|---|---|---|
| `as_of` | 예 | `2025-09-26` |
| `city` | 예 | `춘천` |
| `analysis_mode` | 아니오 | `simulated_replay` (기본) |

후보 수는 임계값에 따라 바뀌므로 `low`~`high` 범위와 함께 줍니다. 화면은 범위를 같이 보여 줍니다.

- 새로 생긴 / 해제된 후보는 7일 전 기준일 목록과 비교합니다(`compared_as_of`). 7일 전이 자료 기간 밖이면 비교하지 않습니다(실제 그래프)
- 실제 그래프 추가 칸: `released_candidates`(해제된 후보 + `release_reason`, 후보 정보는 지난 기준일 것), `compared_as_of`, `monitoring_run_id`(실제 그래프가 읽은 정기 감시 실행), `rule_version`. `released_candidate_ids`는 `released_candidates`의 ID와 같음
- 해제 사유: 트리거 기간이 끝남 / 트리거는 이어지지만 이 동이 진입 조건(구조 상위·접근성)을 넘지 않음 / 그 코호트 이탈 경보가 없음
- 실제 그래프에서 후보 수 범위는 같은 근거에 임계값만 바꿔(`rules.sensitivity.THRESHOLD_GRID`) 다시 낸 후보 수의 최소·최대입니다(기각 제외)

<!-- schema: OverviewResponse -->
```json
{
  "as_of": "2025-09-26",
  "city": "춘천",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "analysis_mode": "simulated_replay",
  "triggers": [
    {"trigger_id": "TR-20250926-R1", "rule": "R1", "name": "추석 연휴 D-7", "city": "춘천", "starts_on": "2025-10-03", "note": "3일 이상 연휴"}
  ],
  "candidate_counts": {
    "scheduled_access_review": {"count": 3, "low": 2, "high": 5, "note": "임계값에 따라 달라지는 수"},
    "unexpected_activity_deviation": {"count": 1, "low": 0, "high": 2, "note": "임계값에 따라 달라지는 수"}
  },
  "new_candidate_ids": ["C-20250926-001", "C-20250926-002", "C-20250926-003"],
  "released_candidate_ids": ["C-20250919-002"],
  "time_label": "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과",
  "disclaimer": "개인 판정 아님 · 최종 판단은 담당자",
  "released_candidates": [
    {"candidate_id": "C-20250919-002", "candidate_type": "unexpected_activity_deviation", "city": "춘천", "adm_cd": null,
     "check_first_adm": ["32010630"], "analysis_cohort": "70대", "service_eligibility_group": "65세 이상",
     "candidate_level": "Level 1", "trigger": null, "last_seen_as_of": "2025-09-19",
     "release_reason": "이번 기준일에는 70대 결제 이탈 경보가 없음"}
  ],
  "compared_as_of": "2025-09-19",
  "monitoring_run_id": "run-20261013-100000-7d1e2f3a",
  "rule_version": "rules-v1.0"
}
```

### 3.4 `GET /map`

파라미터는 `/overview`와 같습니다. 위험지도가 아니라 "구조 취약성 × 행동 신호" 2차원 표시입니다. 카드 신호는 도시 단위라 동에 붙일 때 `is_shared_context=true`입니다.

- 실제 그래프: `structure_value`·`structure_rank`는 정기 감시 근거의 1인세대 비율과 도시 안 순위(도시 × 기준월 행정동 전체 모집단), `structure_top`은 등급 규칙의 "구조 상위"(상위 비율 + 최소값, `rules.yaml`). 행동 신호는 같은 실행의 후보(예정된 점검의 동, 기각되지 않은 이탈의 먼저 확인할 동), `nearest_hub_km`는 관측 거점까지 직선거리 근거
- 자원 점(`resources`)은 원장 v2에서 좌표가 있는 거점과 전화형만. 출처·확인일이 없는 것, 운영 중단, 관할 밖 물리 거점은 뺍니다. `resource_kind`는 시설 / 전화 상담 / 제도·사업 / 사람

<!-- schema: MapResponse -->
```json
{
  "as_of": "2025-09-26",
  "city": "춘천",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "structure_metric": "주민등록 1인세대 비율",
  "areas": [
    {"adm_cd": "32010350", "adm_nm": "남면", "structure_value": 0.47, "structure_rank": 3, "behavior_signal": "예정된 점검", "is_shared_context": false, "nearest_hub_km": 12.4, "value_state": "observed"},
    {"adm_cd": "32010630", "adm_nm": "효자1동", "structure_value": 0.52, "structure_rank": 1, "behavior_signal": "이탈 경보(공유 맥락)", "is_shared_context": true, "nearest_hub_km": 0.8, "value_state": "observed"}
  ],
  "resources": [
    {"resource_id": "R0412", "name": "춘천시 노인복지관", "resource_type": "노인복지", "lat": 37.88, "lon": 127.73, "phone_based": false},
    {"resource_id": "R0007", "name": "외로움안녕120", "resource_type": "상담", "lat": null, "lon": null, "phone_based": true, "resource_kind": "전화 상담"}
  ],
  "note": "구조 취약성 × 행동 신호 2차원 표시. 위험지도 아님",
  "monitoring_run_id": "run-20261013-100000-7d1e2f3a",
  "rule_version": "rules-v1.0"
}
```

### 3.5 `GET /candidates`

| 파라미터 | 필수 | 예 |
|---|---|---|
| `as_of`, `city` | 예 | `2025-09-26`, `춘천` |
| `type` | 아니오 | `scheduled_access_review` / `unexpected_activity_deviation` |
| `level` | 아니오 | `Level 2` |

후보 필드
- `input_status`: 필수 근거 `충족` / `부분 판정` / `보류`. 부분·보류면 `missing_inputs`에 빠진 근거
- `counter_result`: 반증 결과 `기각` / `보류` / `통과` / `해당 없음`(예정된 점검)
- `candidate_level`: `Level 0`~`Level 3` / `판단 보류`. 기각 후보도 `Level 1`로 남음
- `queue_rank`, `selected_for_review`, `selection_budget`: 검토 배정(K). 등급과 따로
- 실제 그래프: 도시 하나의 정기 감시 실행에서 읽고 `monitoring_run_id`에 그 실행을 적습니다. 후보 ID는 그 실행의 ID라 `POST /runs`(`candidate_id`)가 그대로 받습니다. 같은 기준일에 두 도시 ID가 겹치지 않게 번호대를 나눕니다(춘천 `-001`~, 강남 `-501`~). 후보 ID는 실행마다 새로 매기므로 다른 기준일과는 ID로 비교하지 않습니다(§3.3)
- `analysis_mode`(선택, 기본 `simulated_replay`): 실제 그래프가 읽을 정기 감시의 분석 모드
- `cause_type`, `need_types`: 후보의 원인 유형(예: `거점 원거리형+계절 공동화형+한파·대설형`)과 그 필요 유형. 조사 실행의 자원 찾기와 같은 함수(`nodes.candidate_cause`: 예정된 점검은 트리거, 관측 거점이 멀면 거점 원거리형을 앞에)로 정합니다. 화면은 조사 전 자원 미리 보기에 `cause_type`을 그대로 보냅니다(§3.12). 가짜는 거리를 보지 않고 트리거만

<!-- schema: CandidateListResponse -->
```json
{
  "as_of": "2025-09-26",
  "city": "춘천",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "selection_budget": 2,
  "items": [
    {
      "candidate_id": "C-20250926-001",
      "candidate_type": "scheduled_access_review",
      "city": "춘천",
      "adm_cd": "32010350",
      "check_first_adm": [],
      "analysis_cohort": "60대·70대·80대+",
      "service_eligibility_group": "65세 이상",
      "trigger": "R1 추석 연휴 D-7",
      "input_status": "충족",
      "missing_inputs": [],
      "counter_result": "해당 없음",
      "counter_reason": null,
      "candidate_level": "Level 2",
      "level_reason": "접근성 근거(관측 거점까지 12.4km) + 예정된 트리거",
      "queue_rank": 1,
      "selected_for_review": true,
      "selection_budget": 2,
      "evidence_ids": ["EV-20250926-0001", "EV-20250926-0002"],
      "is_shared_context": false
    },
    {
      "candidate_id": "C-20250926-004",
      "candidate_type": "unexpected_activity_deviation",
      "city": "춘천",
      "adm_cd": null,
      "check_first_adm": ["32010630", "32010350"],
      "analysis_cohort": "80대+",
      "service_eligibility_group": "65세 이상",
      "trigger": null,
      "input_status": "부분 판정",
      "missing_inputs": ["weather_asos"],
      "counter_result": "보류",
      "counter_reason": "강수 반증 자료 없음",
      "candidate_level": "판단 보류",
      "level_reason": "반증 보류",
      "queue_rank": 3,
      "selected_for_review": false,
      "selection_budget": 2,
      "evidence_ids": ["EV-20250926-0007"],
      "is_shared_context": true
    }
  ]
}
```

### 3.6 `POST /runs`

조사 실행을 시작합니다. 바로 `202 Accepted`. 검토 역할 이상, 사용자별 횟수 한도(§1). 질문은 개인정보를 가린 뒤 저장·실행합니다.

요청 (`RunCreateRequest`). 질의응답 실행은 `question`이나 `candidate_id` 중 하나가 필요합니다.

<!-- schema: RunCreateRequest -->
```json
{
  "mode": "qa",
  "question": "이번 추석 전에 춘천에서 어디부터 확인해야 해?",
  "candidate_id": null,
  "as_of": "2025-09-26",
  "analysis_mode": "simulated_replay"
}
```

응답 `202`

<!-- schema: RunAccepted -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "status": "queued",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "status_url": "/runs/run-20261013-100000-3fa9c2d1"
}
```

같은 요청을 다시 보내면 새 실행이 생깁니다(재분석 = 새 실행).

후보로 열 때
- `candidate_id`와 함께 `city`(그 도시 후보만)와 `monitoring_run_id`(현황·후보 목록 응답의 `monitoring_run_id`)를 보냅니다. `monitoring_run_id`를 주면 그 정기 감시 실행에서만 후보를 찾습니다(화면이 본 바로 그 후보). 그 기준일에 발행된 정기 감시 실행이 아니거나 그 실행·도시에 없는 후보면 `400 INVALID_REQUEST`
- 둘 다 선택 칸입니다. 없으면 같은 기준일의 발행된 정기 감시 실행에서 찾습니다(§5). `monitoring_run_id`는 `candidate_id` 없이 쓰면 400
- 가짜 백엔드는 `monitoring_run_id`를 쓰지 않고, `city`가 후보 도시와 다르면 400
- **그 후보만 조사합니다.** 조사 실행은 도시 전체 근거를 모아 등급·순서를 정한 뒤(정기 감시와 같은 등급, `tests/test_scope.py`) 그 후보만 남깁니다. 근거 카드 제목은 `후보 C-… · 예정된 점검 · 춘천 남면 · 기준일 …`처럼 후보 하나이고, 카드에는 그 후보 근거 + 그 동 근거 + 도시 공통 근거(트리거, 같은 코호트의 결제·경보·반증)만 담습니다. 자원도 그 후보 것만
- `candidate_ids`(선택, 10개까지): 함께 볼 후보. 같은 정기 감시 실행·같은 도시의 후보만 받고, 아니면 `400 CANDIDATE_NOT_IN_RUN`. `candidate_id` 없이 쓰면 400. 가짜 백엔드는 쓰지 않음
- 정기 감시와 근거가 달라 그 후보가 조사 실행에 없으면 거르지 않고 전체 후보로 두고 `errors`에 `SCOPE_CANDIDATE_MISSING`을 남깁니다

```json
{"mode": "qa", "candidate_id": "C-20250926-001", "city": "춘천", "monitoring_run_id": "run-20261013-100000-7d1e2f3a",
 "as_of": "2025-09-26", "analysis_mode": "simulated_replay"}
```

### 3.7 `GET /runs/{id}`

실행 상태와 지나온 노드(`path`), Guard 판정, 후보를 줍니다.

| `status` | 뜻 |
|---|---|
| `queued`, `running` | 대기, 실행 중 |
| `awaiting_approval` | 담당자 승인 대기 (`await_approval`에서 멈춤) |
| `published` | 정기 감시: 후보 목록 발행 |
| `answered` | 단순 조회: 답변 |
| `abstained` | 보류 답변 (계약 위반·근거 부족) |
| `approved`, `held` | 확정 기록, 보류 기록 |
| `failed` | 처리 실패 (오류 코드만) |

`report_stale=true`면 검토 내용이 바뀌어 보고서를 다시 만들어야 하고, 그동안 승인은 거절됩니다.

보고서 판 두 가지 (선택 칸)
- `report_revision`: 요청한 판. 다시 만드는 중이면 다음 판(이상)
- `published_report_revision`: 실제로 발행된(화면에 보여 줄) 판. 실제 그래프에서는 Guard가 고친 초안을 발행하지 않으므로 판 번호가 건너뛸 수 있음
- 승인은 `report_stale=false`이고 두 값이 같을 때, 그 판으로만 됨. `extra_rounds`는 추가 조사 횟수

등급 규칙이 더한 선택 칸 (없어도 예전 응답과 호환):

- 후보: `rule_id`, `rule_version`, `rule_checks`(조건, 충족 여부, 값, 기준, 비교, 단위, 입력, 충족 시 가능한 등급)
- 보고서: `actions`(Level 2·3 후보별 확인할 영역, 대응 5칸, 체크리스트, 위급 시 안내), `level_explanations`, `decision_trace`, `distribution`(`내부용(대외비)` 고정), `external_notice`
- 실행 상태: `decision_trace` (가짜 응답도 지나온 노드마다 한 칸씩 채움)
- 보고서 `round_changes`("N회차 대비 달라진 점"), `review_ids`(이 판에 반영한 검토)

<!-- schema: RunStatusResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "status": "awaiting_approval",
  "mode": "qa",
  "route": "investigate",
  "as_of": "2025-09-26",
  "analysis_mode": "simulated_replay",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "path": ["intake", "classify", "check_availability", "collect_signals", "classify_candidate", "counter_evidence", "guard_signal", "assign_level", "assign_queue", "match_resources", "build_card", "write_report", "guard_report", "await_approval"],
  "guard": [
    {"guard": "guard_signal", "check": "신호", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 1},
    {"guard": "guard_report", "check": "사실", "verdict": "REVISE", "rule_ids": ["G-03"], "reasons": ["'춘천 거주'는 카드로 말할 수 없음"], "attempt": 1},
    {"guard": "guard_report", "check": "사실", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 2},
    {"guard": "guard_report", "check": "표현", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 2}
  ],
  "contract_check": {
    "status": "OK",
    "source": "card2",
    "issues": [],
    "analysis_cohort": "60대·70대·80대+",
    "service_eligibility_group": "65세 이상",
    "measured_grain": "가맹점 시군구 × 일 × 업종 × 고객 거주 시도 × 성 × 연령",
    "warnings": ["요청 집단(65세 이상)과 분석 코호트(card2 60대·70대·80대+)가 정확히 같지 않음: 60~64세가 섞임"]
  },
  "candidates": [],
  "evidence_revision": 1,
  "report_revision": 2,
  "report_stale": false,
  "abstain_reason": null,
  "errors": []
}
```

### 3.8 `GET /runs/{id}/evidence`

결정론적 근거 카드입니다. `facts`는 Guard 사실 검사가 대조하는 (지표, 지역, 기간, 값, 단위, 방향, 근거 ID) 묶음이고, 모든 `evidence_id`는 `evidence` 안에 있어야 합니다.

<!-- schema: EvidenceResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "evidence_card": {
    "card_id": "CARD-run-20261013-100000-3fa9c2d1-1",
    "run_id": "run-20261013-100000-3fa9c2d1",
    "candidate_id": "C-20250926-004",
    "title": "판단 보류 · 예상 밖 이탈 · 춘천 가맹점 · 강원 거주 80대+ 고객",
    "snapshot_id": "snap-20261013-101500-a1b2",
    "analysis_mode": "simulated_replay",
    "as_of": "2025-09-26",
    "evidence_revision": 1,
    "facts": [
      {"metric": "생필품 결제 평소 대비", "region": "춘천시 가맹점 · 강원 거주", "period": "2025-09-13~2025-09-19", "value": 0.84, "unit": "평소 대비 비율", "direction": "감소", "evidence_id": "EV-20250926-0007"}
    ],
    "evidence": [
      {
        "evidence_id": "EV-20250926-0007",
        "metric": "생필품 결제 평소 대비",
        "value": 0.84,
        "unit": "평소 대비 비율",
        "denominator": "같은 코호트 최근 같은 요일 4주 평균 (카드 데이터2)",
        "n_obs": 4,
        "direction": "감소",
        "measured_grain": "도시×성×연령×일",
        "applied_scope": "춘천 (동 목록은 공유 맥락)",
        "merchant_region": "춘천시",
        "customer_residence": "강원",
        "is_shared_context": true,
        "analysis_cohort": "80대+",
        "service_eligibility_group": "65세 이상",
        "observed_from": "2025-09-13",
        "observed_to": "2025-09-19",
        "analysis_mode": "simulated_replay",
        "as_of": "2025-09-26",
        "actual_available_at": null,
        "simulated_available_at": "2025-09-22",
        "availability_basis": "assumed",
        "trained_until": "2025-09-12",
        "rule_derived_from": "2025-07~08",
        "snapshot_id": "snap-20261013-101500-a1b2",
        "data_version": "card2@sha256:7c1e…/clean-v1",
        "rule_version": "rules-v0.1",
        "interpretation_status": "계산 재현",
        "valid_claims": ["강원 거주 80대+ 고객의 춘천 가맹점 생필품 결제가 비교 기준보다 낮음"],
        "forbidden_claims": ["춘천 거주 80대의 결제 감소", "이 동의 결제가 줄었다 (도시 신호를 동에 공유한 근거)", "고립 발생"],
        "missing_inputs": ["weather_asos"],
        "warnings": ["가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과"],
        "status": "OK",
        "value_state": "observed",
        "error_code": null,
        "calibrated_until": null
      }
    ],
    "limitations": ["개인 판단 아님", "동 단위 카드 소비는 알 수 없음", "강수 반증 자료 없음"],
    "time_label": "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과",
    "disclaimer": "개인 판정 아님 · 최종 판단은 담당자"
  }
}
```

### 3.9 `POST /runs/{id}/review`

담당자가 본 근거(**이 실행 카드의 근거 ID만**), 자원별 판정·메모(**이 실행의 추천 범위 안 자원만**), 후속 확인일을 저장합니다. 자원 판정이 바뀌면 보고서 버전이 올라가고 `report_stale=true`가 됩니다(다시 만든 뒤 승인).

- 다른 ID는 400 `EVIDENCE_NOT_IN_RUN`·`RESOURCE_NOT_IN_RUN`
- 근거 ID는 **지금 근거 카드**(`GET /runs/{id}/evidence`)에 있는 것만 보냅니다. 추가 조사(`more`)로 다음 회차 카드에서 빠진 근거 ID를 다음 검토에 다시 보내면 `400 EVIDENCE_NOT_IN_RUN`입니다. 화면은 이전 검토에서 체크한 ID 중 지금 카드에 없는 것을 빼고 보냅니다
- 승인 대기가 아닌 실행(이미 승인·보류, 조사 중 등)은 `409 RUN_NOT_AWAITING_APPROVAL`. 메시지에 지금 상태를 적습니다 (예: "승인 대기 상태가 아닙니다 (지금: 이미 승인됨). 검토를 저장할 수 없습니다.")
- 화면에서 본 판을 `report_revision`으로 보내면, 지금 판과 다를 때 409 `REVIEW_REVISION_STALE`
- 응답의 `report_stale`은 저장한 뒤의 실제 상태, `changed`는 이번 저장으로 판정이 바뀌었는지
- 실제 그래프: 판정이 바뀌면 승인 대기를 검토로 재개 → `record_review` → `write_report`·`guard_report` → 새 판 발행. 새 판에는 "담당자 검토: 자원 … 판정" 문장(메모 원문은 넣지 않음)과 `review_ids`가 들어가고, 부적합 자원은 대응안에서 빠짐. 발행 판이 저장된 검토를 모두 담았는지 확인한 뒤에 `report_stale=false`

<!-- schema: ReviewRequest -->
```json
{
  "reviewer": "worker-01",
  "checked_evidence_ids": ["EV-20250926-0001", "EV-20250926-0002"],
  "resource_judgements": [
    {"resource_id": "R0007", "judgement": "적합", "memo": "전화형이라 거리와 무관"},
    {"resource_id": "R0412", "judgement": "보류", "memo": "연휴 운영 여부 확인 필요"}
  ],
  "memo": "남면 이장님께 먼저 연락",
  "follow_up_on": "2025-10-01"
}
```

<!-- schema: ReviewResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "review_id": "rev-20261013-100500-8c1d2e3f",
  "report_revision": 3,
  "report_stale": true
}
```

### 3.10 `POST /runs/{id}/approve`

승인(`approved`) / 보류(`held`) / 추가 조사(`more`, 최대 2회). 지금 보고서 버전(`report_revision`)과 멱등 키(`idempotency_key`)가 필요합니다.

- 버전이 다르거나 `report_stale=true` → `409 REPORT_REVISION_STALE`
- 같은 키로 다시 보내면 첫 결과를 그대로 주고 `duplicate=true` (확정 기록은 1건)
- Guard ABSTAIN(계약 위반)으로 끝난 실행은 승인으로 풀리지 않음 → `409 APPROVAL_BLOCKED_BY_ABSTAIN`
- 거절된 승인 요청도 감사 로그 `approval_rejected`에 남음. 승인·확정 기록은 생기지 않음
- 화면은 **실제로 띄운 보고서 판**으로만 승인 요청을 보냄. 보고서 응답이 늦거나 순서가 바뀌면 승인 버튼을 잠금
- 확정·보류가 끝난 뒤 다른 키(반대 결정·다른 판 포함) → `409 RUN_NOT_AWAITING_APPROVAL`. 에이전트(`Agent.approve`)도 같은 뜻으로 답함. 메시지에 지금 상태를 적음 (예: "승인 대기 상태가 아닙니다 (지금: 이미 보류됨). 승인·보류·추가 조사를 할 수 없습니다.")
- 계약 위반으로 보류된 실행은 추가 조사도 `409 APPROVAL_BLOCKED_BY_ABSTAIN` (보류 `held`는 됨)
- 추가 조사(`more`)는 `focus`(선택)를 받음: `candidate_ids`(이 실행 후보만, 아니면 `400 CANDIDATE_NOT_IN_RUN`), `tools`(추가 도구 `get_mobility_layer`·`get_validation_reference`·`calc_holiday_gap`·`calc_winter_contraction`·`calc_heat_response`), `counter_checks`(`공휴일 기대값`·`도시 공통요인`·`강수`·`학사일정`), `memo`(500자). 메모는 LLM(없으면 규칙)이 화이트리스트 도구 계획으로 바꾸고, 원문은 보고서·기록에 넣지 않음. 다음 회차 보고서에 "N회차 대비 달라진 점". 등급은 규칙이 정하므로 계획 때문에 바뀌지 않음. `more`가 아닌 결정에 `focus`를 주면 400
- 실제 그래프: 추가 조사 계획은 **받을 때** 정해 범위(후보·도구·반증)와 함께 저장합니다(`approval_intents.focus`, 메모 원문 없음). 작업 전·재개 키를 차지한 직후·도구 실행 중에 API가 꺼져도 다시 켜면 같은 계획이 한 번만 돕니다
- 실제 그래프: 승인·보류는 그래프 재개 전에 승인 의도를 먼저 적습니다. 처리 중 오류로 500을 받았더라도 의도가 적혔으면 다음 요청이나 재시작 때 같은 결정·판·승인자·키로 끝나고, 같은 키로 다시 보내면 `duplicate=true`, 다른 키의 반대 결정은 `409 RUN_NOT_AWAITING_APPROVAL`입니다(§5)

추가 조사 요청 예

<!-- schema: ApproveRequest -->
```json
{
  "decision": "more",
  "report_revision": 2,
  "approver": "lead-01",
  "idempotency_key": "run-20261013-100000-3fa9c2d1:more:2",
  "memo": null,
  "focus": {
    "candidate_ids": ["C-20250926-004"],
    "tools": ["calc_holiday_gap"],
    "counter_checks": ["강수"],
    "memo": "연휴 뒤 회복이 늦은지 더 봐 주세요"
  }
}
```

<!-- schema: ApproveRequest -->
```json
{
  "decision": "approved",
  "report_revision": 3,
  "approver": "lead-01",
  "idempotency_key": "run-20261013-100000-3fa9c2d1:approve:3",
  "memo": null
}
```

<!-- schema: ApproveResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "decision": "approved",
  "report_revision": 3,
  "approval_id": "apr-20261013-101000-5b6a7c8d",
  "duplicate": false
}
```

오래된 버전으로 승인한 경우 `409`

<!-- schema: ErrorResponse -->
```json
{
  "error_code": "REPORT_REVISION_STALE",
  "message": "보고서가 3판으로 바뀌었습니다. 다시 확인한 뒤 승인하세요.",
  "details": []
}
```

### 3.11 `POST /qa`

질의응답입니다(검토 역할 이상, 사용자별 횟수 한도). 근거 카드를 먼저 만들고, 사실 검사가 끝난 문장만 `answer`에 담아 한 번에 응답합니다. 계약 위반이면 오류가 아니라 `status=abstained`(보류 답변) + 필요한 자료 안내입니다.

<!-- schema: QARequest -->
```json
{
  "question": "강남 60대의 밤 활동은 어때?",
  "as_of": "2025-10-20",
  "analysis_mode": "simulated_replay",
  "city": "강남"
}
```

보류 답변 (단위 계약 위반 G-02)

<!-- schema: QAResponse -->
```json
{
  "run_id": "run-20261013-0002",
  "status": "abstained",
  "evidence_card": null,
  "answer": null,
  "guard": [],
  "abstain_reason": "GRAIN_FORBIDDEN_COMBO: 체류인구: 연령 × 시간대 조합은 지원하지 않음. 통신 성연령 표와 시간대 표는 따로라서 \"특정 연령의 시간대 체류\"는 알 수 없음",
  "needed_data": ["연령 × 시간대가 함께 있는 통신 자료", "또는: 카드 데이터1의 60대 18~23시 결제(도시 단위, 결제 기준)"]
}
```

답변 (단순 조회)

<!-- schema: QAResponse -->
```json
{
  "run_id": "run-20261013-0003",
  "status": "answered",
  "evidence_card": {
    "card_id": "CARD-run-20261013-0003-1",
    "run_id": "run-20261013-0003",
    "candidate_id": null,
    "title": "강남 1인세대 비율 상위 동",
    "snapshot_id": "snap-20261013-101500-a1b2",
    "analysis_mode": "retrospective",
    "as_of": "2025-10-20",
    "evidence_revision": 1,
    "facts": [
      {"metric": "1인세대 비율", "region": "강남 역삼1동", "period": "2025-09", "value": 0.68, "unit": "비율", "direction": null, "evidence_id": "EV-20251020-0001"}
    ],
    "evidence": [
      {
        "evidence_id": "EV-20251020-0001",
        "metric": "1인세대 비율",
        "value": 0.68,
        "unit": "비율",
        "denominator": "전체 세대수 (주민등록)",
        "n_obs": 1,
        "measured_grain": "행정동 × 월",
        "applied_scope": "강남 역삼1동",
        "analysis_mode": "retrospective",
        "as_of": "2025-10-20",
        "observed_from": "2025-09-30",
        "observed_to": "2025-09-30",
        "simulated_available_at": "2025-10-10",
        "availability_basis": "assumed",
        "interpretation_status": "직접 확인",
        "valid_claims": ["1인세대 비율"],
        "forbidden_claims": ["1인가구 (개념이 다름)"],
        "status": "OK",
        "value_state": "observed"
      }
    ],
    "limitations": ["주민등록 1인세대는 1인가구와 다름"],
    "time_label": null,
    "disclaimer": "개인 판정 아님 · 최종 판단은 담당자"
  },
  "answer": "2025년 9월 주민등록 기준 강남 역삼1동의 1인세대 비율은 0.68입니다 [EV-20251020-0001].",
  "guard": [
    {"guard": "guard_signal", "check": "신호", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 1},
    {"guard": "guard_report", "check": "사실", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 1},
    {"guard": "guard_report", "check": "표현", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 1}
  ],
  "abstain_reason": null,
  "needed_data": []
}
```

### 3.12 `GET /resources`

| 파라미터 | 필수 | 예 |
|---|---|---|
| `adm_cd` | 아니오 | `32010350` |
| `need_type` | 아니오 | `안부 확인` |
| `group` | 아니오 | `고령층` |

자격·운영 상태를 모르면 `확인 필요`로 표시합니다. 출처·확인일이 없는 자원은 목록에 넣지 않습니다(G-07).

실제 그래프
- 원장 v2(`external/resources/복지자원_원장_v2.csv`, 시설·전화 상담·제도·사업·사람)를 조사 실행의 자원 찾기(`find_resources`)와 같은 매칭·제외 규칙으로 거릅니다: 관할 → 대상·자격 → 필요 유형 → 운영 상태 → 거리/전화형, 그다음 확인일·출처 없음과 관할 밖 물리 거점은 뺌. 위기 전화는 맨 아래 "위급 시 안내" 묶음
- `adm_cd`를 주면 그 동 중심점에서 직선거리, `city`만 주면 도시 전체(거리 없음), 둘 다 없으면 두 도시
- `need_type`은 `configs/need_types.yaml`의 필요 유형(의료 접근, 생필품, 안부·관계, 정신건강 상담, 이동 지원, 난방, 주거 안전, 더위 쉼터)
- `limit`(기본 50, 최대 200)까지 돌려주고 `total`에 거른 뒤 전체 수, `ledger_version`에 원장 버전
- `cause_type`(선택): 원인 유형(`configs/need_types.yaml` `cause_types`, 여럿이면 `+`로 이음). 주면 그 원인의 필요 유형 자원만 거르고(제외 규칙은 같음), 응답에 `need_types`(쓴 필요 유형)와 `scope_total`(필요 유형으로 거르기 전, 관할·대상·제외 규칙만 거친 수)을 줍니다. 화면은 "필요한 것(…)에 맞는 자원 `total`건 (이 동·집단에서 볼 수 있는 `scope_total`건 중)"으로 보입니다. 모르는 원인 유형은 400. 예: 춘천 남면·고령층 390건 → 계절 공동화형 160건, 강남 역삼1동·중장년 남성 315건 → 1인가구 밀집형 17건(원장 v2)

<!-- schema: ResourceListResponse -->
```json
{
  "adm_cd": "32010350",
  "need_type": "안부 확인",
  "group": "고령층",
  "items": [
    {
      "resource_id": "R0007",
      "name": "외로움안녕120",
      "resource_type": "상담",
      "city": "춘천",
      "adm_cd": null,
      "target_group": "전 연령",
      "eligibility": "적합",
      "operating_status": "운영",
      "distance_km": null,
      "phone_based": true,
      "source_url": "https://www.data.go.kr/",
      "verified_date": "2026-03-18",
      "warnings": []
    },
    {
      "resource_id": "R0412",
      "name": "춘천시 노인복지관",
      "resource_type": "노인복지",
      "city": "춘천",
      "adm_cd": "32010530",
      "target_group": "노년",
      "eligibility": "확인 필요",
      "operating_status": "확인 필요",
      "distance_km": 12.4,
      "phone_based": false,
      "source_url": "https://www.data.go.kr/",
      "verified_date": "2026-03-18",
      "warnings": ["연휴 운영 여부 미확인"]
    }
  ]
}
```

### 3.13 `GET /runs/{id}/resources`

이 실행이 시작할 때 고정한 스냅샷·원장으로 고른 추천 자원입니다(Guard G-07을 지난 것). 후보의 `adm_cd`·집단으로 새로 찾지 않으므로, 원장이 바뀌어도 이 실행의 목록은 그대로입니다. `judgement`는 가장 최근 검토의 판정. 검토의 `resource_judgements`는 이 목록 안의 ID만 받습니다. 실행 중이면 `409 RUN_NOT_READY`.

- **자원 하나에 한 줄**입니다. 판정은 자원 ID 단위라, 여러 후보에 함께 추천된 자원도 한 줄로 오고 `candidate_ids`에 그 후보를 모두 적습니다(실행의 후보 순서). `candidate_id`는 `candidate_ids`의 첫 후보(옛 칸)
- 합칠 때 거리(`distance_km`)는 그 후보들 중 가장 가까운 값, 자격(`eligibility`)은 가장 조심스러운 값(부적합 > 확인 필요 > 적합), `warnings`는 합친 것. 순서는 처음 나온 순서(후보 검토 배정 순)이고 위기 전화("위급 시 안내")는 맨 아래
- 그래프 상태(`resources`)는 그대로 후보마다 한 줄입니다(대응안·Guard G-07이 후보별로 씀). 응답에서만 합칩니다

<!-- schema: RunResourcesResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "snapshot_id": "snap-20261013-101500-a1b2",
  "evidence_revision": 1,
  "report_revision": 2,
  "items": [
    {
      "resource_id": "R0007",
      "name": "외로움안녕120",
      "resource_type": "상담",
      "city": "춘천",
      "adm_cd": null,
      "target_group": "전 연령",
      "eligibility": "적합",
      "operating_status": "운영",
      "distance_km": null,
      "phone_based": true,
      "source_url": "https://www.data.go.kr/",
      "verified_date": "2026-03-18",
      "warnings": [],
      "candidate_id": "C-20250926-001",
      "candidate_ids": ["C-20250926-001", "C-20250926-002"],
      "judgement": "적합"
    }
  ]
}
```

### 3.14 `POST /runs/{id}/followup`

이어 묻기입니다(검토 역할 이상, 사용자별 횟수 한도, 그 실행의 LLM 예산을 같이 씀). 보고서가 나온 실행의 근거 카드·후보 안에서만 답하고 새 도구를 부르지 않습니다(실행 상태도 그대로). 개인 지목 질문은 `abstained` + `OUT_OF_SCOPE_INDIVIDUAL`, 카드에 없는 도시는 `abstained` + `OUT_OF_SCOPE`, 판단 보류된 보고서면 `abstained`(보류 사유·필요한 자료). 답은 템플릿 문장(후보 문장, 그 후보 근거의 사실 문장)과 LLM 설명 문장(카드 안 근거 ID만 인용)이고, 보고서와 같은 Guard(사실·표현)를 지난 뒤에만 줍니다. 걸리면 LLM 문장을 빼고 템플릿만(`source=template`). 감사 로그에는 질문 원문 없이 `followup_answered`만 남습니다. 실행 중이면 `409 RUN_NOT_READY`.

답할 후보 고르기: 질문에 적힌 대상 조건은 **모두 엄격하게** 씁니다. 후보 ID, 동 이름(행정동 코드표 `ref/adm_cd_names.csv`, 후보의 `adm_cd`와 먼저 확인할 동 모두), 성별("여성"·"남성"·"할머니" 등, 성별이 없는 집단은 남녀 모두), 연령("60대", 분석 집단 기준), 서비스 대상 집단("고령층"·"어르신"·"중장년"·"청년", 집단 이름이 없는 후보는 나이 범위·성별로), 등급("Level 2", "보류")을 모두 맞는 후보만 답합니다. 맞는 후보가 없으면 다른 후보로 대신 답하지 않고 `abstained` + `NO_TARGET: 이 카드에 맞는 대상이 없습니다 (질문의 조건)` + 필요한 자료 "그 대상을 묻는 새 조사"입니다. 대상 조건이 하나도 없는 일반 질문만 검토 배정 순으로 3건까지. 예: "신사동은 왜 Level 2야?" → 신사동 후보만, 고령층 카드에 "동산면은 왜 Level 3야?"·"청년 여성은?"·없는 후보 ID → `NO_TARGET`. 후보가 없는 조회 카드는 후보 ID·등급 조건만 `NO_TARGET`으로 막고 나머지는 카드 사실로 답합니다.

<!-- schema: FollowupRequest -->
```json
{"question": "남면은 왜 Level 3야?"}
```

<!-- schema: FollowupResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "followup_id": "fup-20261013-101200-1a2b3c4d",
  "status": "answered",
  "answer": "1인세대 비율 0.47(비율) — 춘천 남면 (32010350), 2025-08, 방향 없음 [EV-20250926-STR-32010350].\n개인 판정 아님 · 최종 판단은 담당자",
  "cited_evidence_ids": ["EV-20250926-STR-32010350"],
  "source": "template",
  "report_revision": 2,
  "guard": [
    {"guard": "guard_report", "check": "사실", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 1},
    {"guard": "guard_report", "check": "표현", "verdict": "PASS", "rule_ids": [], "reasons": [], "attempt": 1}
  ],
  "abstain_reason": null,
  "needed_data": []
}
```

### 3.15 `GET /data/capabilities`

데이터별 지원 차원·도시·기간과 교차 금지 조합입니다. 값은 `configs/grain_contract.yaml`에서 만듭니다(`contracts.grain.get_data_capabilities`). 아래는 일부만 보인 예입니다.

<!-- schema: CapabilitiesResponse -->
```json
{
  "grain_version": "grain-v1",
  "sources": [
    {
      "source": "card2",
      "name": "신한카드 데이터2 (주력)",
      "dimensions": ["city", "date", "weekday", "industry", "customer_residence", "sex", "age"],
      "cities": ["강남", "춘천"],
      "measured_grain": "가맹점 시군구 × 일 × 업종 × 고객 거주 시도 × 성 × 연령",
      "period_from": "2025-07-01",
      "period_to": "2025-12-31",
      "age_bands": ["10대", "20대", "30대", "40대", "50대", "60대", "70대", "80대+"],
      "observed_window_status": null
    },
    {
      "source": "seoul_living",
      "name": "서울 시민생활 데이터 (강남만)",
      "dimensions": ["city", "adm_cd", "month", "sex", "age"],
      "cities": ["강남"],
      "measured_grain": "행정동 × 성 × 5세 연령 × 파일 기준월",
      "period_from": "2025-07-01",
      "period_to": "2025-12-31",
      "age_bands": ["20~24세", "25~29세", "30~34세", "35~39세", "40~44세", "45~49세", "50~54세", "55~59세", "60~64세", "65~69세", "70~74세", "75세 이상"],
      "observed_window_status": "미확인"
    }
  ],
  "forbidden_combos": [
    {"dims": ["age", "time_of_day"], "metrics": ["체류인구"], "reason": "통신 성연령 표와 시간대 표는 따로라서 \"특정 연령의 시간대 체류\"는 알 수 없음"}
  ]
}
```

### 3.16 `GET /followups`

후속 확인 모아 보기입니다. 후보마다 **가장 최근 조사 실행**의 마지막 후속 확인일(검토의 `follow_up_on`) 하나를 줍니다. 화면이 실행마다 `/runs/{id}/history`를 부르지 않아도 됩니다. 가짜·실제 모두 저장소(검토 기록)에서 만듭니다.

| 파라미터 | 필수 | 뜻 |
|---|---|---|
| `city` | 아니오 | 그 도시 실행만 |
| `week` | 아니오 | 이 날부터 7일 (`from`·`to`와 함께 쓰면 400) |
| `from`, `to` | 아니오 | 후속 확인일 범위 |
| `as_of` | 아니오 | 주면 `state`: `지남` / `이번 주`(7일 안) / `나중` |

후속 확인일 순으로 줍니다. 같은 후보를 다시 열어 새 실행을 만들면 새 실행이 기준이라, 새 실행에 후속 확인일이 없으면 목록에서 빠집니다.

<!-- schema: FollowUpListResponse -->
```json
{
  "as_of": "2025-09-26",
  "city": "춘천",
  "date_from": null,
  "date_to": null,
  "items": [
    {"candidate_id": "C-20250926-001", "run_id": "run-20261013-100000-3fa9c2d1", "city": "춘천", "follow_up_on": "2025-10-01",
     "memo": "남면 이장님께 먼저 연락", "review_id": "rev-20261013-100500-8c1d2e3f", "reviewer": "worker-01",
     "run_status": "awaiting_approval", "approved": false, "state": "이번 주"}
  ]
}
```

### 3.17 현장 확인 결과

승인한 조사 뒤 담당자가 현장에서 확인한 결과를 **동·집단(후보) 단위 건수로만** 적습니다. 이름·주소·전화번호·메모 칸은 없고, 모르는 칸을 보내면 400입니다.

`POST /runs/{id}/outcomes` (검토 역할 이상)
- 실행이 `approved`일 때만 받습니다. 아니면 `409 RUN_NOT_APPROVED`
- `candidate_id`는 이 실행의 후보만 (`400 CANDIDATE_NOT_IN_RUN`). 도시·동·집단·트리거 코드·사건 ID는 서버가 그 후보에서 채웁니다
- 건수는 0 이상 정수. 연락됨 + 부재 ≤ 확인 가구, 신규 연계·위기 발견 ≤ 확인 가구. 어기면 `400 INVALID_REQUEST`
- `idempotency_key`: 같은 키 + 같은 내용이면 첫 기록(`duplicate=true`), 같은 키 + 다른 내용이면 `409 IDEMPOTENCY_KEY_REUSED`
- 같은 후보를 다시 적으면 새 기록이 남고, 모아 보기와 목록은 **가장 나중에 저장된** 기록을 씁니다 (고쳐 적기). 순서는 저장소가 붙이는 저장 순서(`seq`)로만 정하므로 같은 시각에 고쳐 적어도, 다시 켜도 고친 값이 기준입니다. `created_at`은 표시용
- 감사 로그: `outcome_recorded`(기록 ID, 후보, 건수), 거절은 `outcome_rejected`

<!-- schema: OutcomeRequest -->
```json
{"candidate_id": "C-20251220-003", "checked_households": 12, "contacted": 7, "absent": 4, "new_links": 3,
 "crises_found": 1, "idempotency_key": "run-20261013-100000-3fa9c2d1:C-20251220-003:1"}
```

<!-- schema: OutcomeResponse -->
```json
{
  "run_id": "run-20261013-100000-3fa9c2d1",
  "duplicate": false,
  "outcome": {"outcome_id": "out-20261014-090000-1a2b3c4d", "run_id": "run-20261013-100000-3fa9c2d1",
              "candidate_id": "C-20251220-003", "city": "춘천", "adm_cd": "32010350", "group": "65세 이상",
              "trigger_code": "W", "event_id": "춘천:W:2025", "checked_households": 12, "contacted": 7, "absent": 4,
              "new_links": 3, "crises_found": 1, "recorded_by": "worker-01", "created_at": "2026-10-14T09:00:00"}
}
```

`GET /runs/{id}/outcomes`: 이 실행 상세 화면용입니다. 후보마다 가장 최근 기록을 **10 미만도 그대로** 줍니다(`history_count`는 고쳐 적은 것까지 전체 수).

<!-- schema: OutcomeListResponse -->
```json
{"run_id": "run-20261013-100000-3fa9c2d1", "history_count": 2, "items": []}
```

`GET /outcomes/summary?city=&by=trigger|event`: 모아 보기입니다. 도시 × 트리거(`by=trigger`, 기본) 또는 도시 × 트리거 × 사건(`by=event`)별 합계를 줍니다.
- **작은 칸 규칙**: 합계가 10 미만인 칸은 숫자 대신 `null`이고 `small_cells`에 칸 이름이 들어갑니다. 화면은 "10 미만"으로 보입니다(G-08과 같은 10). 숨긴 칸이 들어간 비율(`contact_rate`·`link_rate`·`crisis_rate`)도 `null`
- 실행 하나의 건수가 10 미만이어도 저장은 적은 그대로 합니다. 그 숫자는 그 실행 상세(`GET /runs/{id}/outcomes`)에서만 보이고, 모아 보기·내보내기에서는 보이지 않습니다
- 다음 규칙 평가(트리거·사건별로 현장에서 실제로 확인된 비율 점검)에 입력으로 쓸 수 있는 집계입니다(`bigcontest.rules.outcomes.summarize`). **등급 계산에는 쓰지 않습니다**

<!-- schema: OutcomeSummaryResponse -->
```json
{
  "city": "춘천",
  "by": "trigger",
  "small_cell": 10,
  "rows": [
    {"city": "춘천", "trigger_code": "W", "event_id": null, "runs": 2, "places": 3, "checked_households": 24,
     "contacted": 16, "absent": null, "new_links": null, "crises_found": null,
     "small_cells": ["absent", "new_links", "crises_found"], "contact_rate": 0.667, "link_rate": null, "crisis_rate": null}
  ],
  "note": "건수만 모음 (개인 정보 없음). 10 미만 칸은 숫자를 보이지 않음. 등급 계산에는 쓰지 않음"
}
```

## 4. 오류

모든 오류는 같은 모양(`ErrorResponse`)입니다. `message`는 화면에 그대로 보여도 되는 쉬운 문장이고, 내부 예외 문장은 넣지 않습니다.

| HTTP | `error_code` | 언제 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 화이트리스트 밖 값(도시·기간·지표·차원), 형식 오류, 입력 한도 위반 |
| 400 | `EVIDENCE_NOT_IN_RUN` | 검토에 이 실행 카드에 없는 근거 ID |
| 400 | `RESOURCE_NOT_IN_RUN` | 검토에 이 실행의 추천 범위 밖 자원 ID |
| 400 | `CANDIDATE_NOT_IN_RUN` | 추가 조사 `focus`에 이 실행에 없는 후보 ID. 후보로 열 때 `candidate_ids`가 같은 정기 감시 실행·도시의 후보가 아님. 현장 확인 결과의 후보가 이 실행에 없음 |
| 409 | `RUN_NOT_APPROVED` | 승인되지 않은 실행에 현장 확인 결과를 적음 |
| 409 | `RUN_NOT_READY` | 아직 실행 중인데 근거·보고서·자원·이어 묻기를 요청. 현황·지도·후보 목록이 읽을 정기 감시가 아직 도는 중 (실제 그래프) |
| 401 | `UNAUTHENTICATED` | 토큰 없음·틀림 (인증이 켜졌을 때) |
| 403 | `FORBIDDEN` | 역할 부족 (예: 조회 역할로 실행 만들기·질의응답·이어 묻기·승인) |
| 429 | `RATE_LIMITED` | 사용자별 실행 만들기·질의응답·이어 묻기 횟수 한도를 넘음. `Retry-After` 초 뒤 다시 |
| 413 | `PAYLOAD_TOO_LARGE` | 본문 64KB 초과 |
| 409 | `REVIEW_REVISION_STALE` | 검토 요청의 `report_revision`이 지금 판과 다름 |
| 404 | `RUN_NOT_FOUND` | 없는 `run_id` |
| 404 | `SNAPSHOT_NOT_FOUND` | 없는 `snapshot_id` |
| 409 | `REPORT_REVISION_STALE` | 승인 요청의 `report_revision`이 지금 버전과 다름, 또는 보고서를 다시 만들어야 함 |
| 409 | `RUN_NOT_AWAITING_APPROVAL` | 승인 대기가 아닌 실행에 승인·검토 요청. 메시지에 지금 상태(이미 승인됨·이미 보류됨·조사 중 등) |
| 409 | `APPROVAL_BLOCKED_BY_ABSTAIN` | 계약 위반(Guard ABSTAIN)으로 끝난 실행 |
| 409 | `EXTRA_ROUNDS_EXCEEDED` | 추가 조사 2회를 넘음 |
| 409 | `IDEMPOTENCY_KEY_REUSED` | 같은 멱등 키로 다른 내용(결정·버전)을 보냄 |
| 503 | `NO_ACTIVE_SNAPSHOT` | 발행된 스냅샷이 없음 |
| 500 | `INTERNAL_ERROR` | 그 밖의 오류 (사유는 로그에만) |

400 예 (화이트리스트 위반)

<!-- schema: ErrorResponse -->
```json
{
  "error_code": "INVALID_REQUEST",
  "message": "도시는 강남, 춘천만 고를 수 있습니다.",
  "details": []
}
```

**계약 사유 코드** — 질의응답·실행에서 계약 위반은 HTTP 오류가 아니라 보류 답변(`abstained`)으로 끝나고, 사유는 `contract_check.issues[].code`와 `abstain_reason`에 들어갑니다.

| 코드 | Guard | 뜻 |
|---|---|---|
| `DIMENSION_NOT_AVAILABLE` | G-01 | 원천에 그 차원이 없음 (예: 카드의 행정동) |
| `CITY_NOT_COVERED` | G-01 | 그 도시 자료가 없음 (예: 춘천 시민생활) |
| `COHORT_NOT_SUPPORTED` | G-01 | 요청 연령을 원천 연령 구간으로 나타낼 수 없음 (예: 통신 60+로 "70대 이상") |
| `GRAIN_FORBIDDEN_COMBO` | G-02 | 서로 다른 표를 결합해야 답할 수 있음 (예: 연령 × 시간대) |
| `TIME_DATA_AFTER_AS_OF` | G-13 | 기준일 뒤에 관측·공개된 자료 |
| `TIME_AVAILABILITY_UNKNOWN` | G-13 | 공개일 미상 (`strict_as_of`에서는 쓰지 않고 배경 자료로만) |
| `TIME_MODEL_AFTER_AS_OF` | G-13 | 기준일 뒤 자료로 만든 모델·규칙 (`strict_as_of`) |
| `TIME_MODEL_PROVENANCE_UNKNOWN` | G-13 | 모델·규칙을 언제까지의 자료로 만들었는지 기록 없음 (`strict_as_of`) |
| `TIME_NO_USABLE_DATA` | G-13 | 시간 계약을 지키면 필요한 원천이 하나도 안 남음 |

도구 반환 봉투의 `status=ERROR`에는 `error_code`가 함께 옵니다. `EMPTY`(빈 결과)는 오류가 아닙니다. 실제 그래프 실행이 예외로 멈추면 `status=failed`, `errors=[{"error_code": "AGENT_ERROR"}]`(사유는 서버 로그에만). 고정한 스냅샷 파일이 바뀌었으면 도구가 `SNAPSHOT_MISMATCH`로 멈춥니다(새 실행으로 다시 분석).

## 5. 실제 그래프 연결

`LINKRADAR_BACKEND=real`이면 실행 API가 LangGraph 에이전트(`agent/graph.py`)를 씁니다(`api/services/agent_runs.py`). 가짜와 같은 계약을 그래프로 지킵니다.

| API | 그래프 |
|---|---|
| `POST /runs` | 활성 스냅샷과 입력(질문·도시·기준일·모드·LLM·도구)을 `run_metadata.body.inputs`에 고정 → `Agent.start`. 후보 ID로 열면 같은 기준일에 발행된 정기 감시 실행에서 그 후보를 찾아 그 도시의 조사 실행으로 시작 (없으면 400) |
| 상태 | 승인 대기 `interrupt` = `awaiting_approval`, `publish_candidates` = `published`, `answer` = `answered`/`abstained`, `finalize` = `approved`/`held`, 예외 = `failed`. `path`·`guard`·`contract_check`·`candidates`·`decision_trace`·`errors`는 그래프 상태 그대로 |
| `abstain_reason` | 계약 위반(`check_availability`)·Guard ABSTAIN. 승인 → 409 `APPROVAL_BLOCKED_BY_ABSTAIN`, 계약 위반에 추가 조사도 409 (그래프가 보류로 바꾸기 전에 API가 막음), 보류는 됨 |
| 보고서 판 | 그래프가 승인 대기·끝점에 닿았을 때의 보고서를 `report_revisions`에 발행. Guard REVISE 초안은 발행하지 않음 |
| 근거·보고서·자원·검토 | 모두 이 실행의 고정 스냅샷과 발행 판. 자원은 그래프 상태의 `resources`(고정 스냅샷·원장으로 고른 것) |
| 검토 | 판정이 바뀌면 `Command(resume={"kind": "review"})` → `record_review` → 새 판. 발행 판의 `review_ids`가 모든 검토를 담을 때까지 다시 만듦 |
| 승인·보류 | ① API `approval_intents`에 의도 기록 → ② `Command(resume)` → `finalize` 기록 1건 (그래프 `run_records`) → ③ API `approvals`·`finalizations`·감사 (한 트랜잭션) → ④ 상태 맞춤. 두 DB를 한 트랜잭션으로 보지 않고, 끊긴 단계는 다음 승인·검토 요청과 재시작 때 `_reconcile`이 그래프 확정 기록과 의도를 대조해 원래 결정·판·승인자·멱등 키로 채움. 승인·확정은 각각 1건 (데이터 모델 §2.6.1) |
| 추가 조사 | 받을 때 계획을 정해(`make_plan`) 승인 행·의도(범위+계획)·`running`을 한 트랜잭션으로 저장 → `Command(resume={"decision": "more", "focus": {..., "plan": ...}})` → `plan_more`(받은 계획 그대로) → `collect_signals` ... → 다시 승인 대기. 2회 넘으면 409 `EXTRA_ROUNDS_EXCEEDED` |
| 재시작 | API를 켤 때 `queued`·`running` 실행은 고정 입력으로 이어 가고(체크포인트가 있으면 이어서, 없으면 처음부터), 다시 만들던 보고서도 이어 감. 추가 조사는 저장한 범위·계획으로 이어 감(재개 전이면 재개, 재개 키만 차지했으면 그 키로, 회차 도중이면 남은 노드). 승인 대기·승인·보류 실행 중 그래프 확정 기록이나 대기 의도가 API 기록과 다르면 `_reconcile`로 맞춤 |
| 스냅샷 | 새 스냅샷(파일 해시가 바뀐 dev 스냅샷)이 활성이 돼도 진행 중 실행은 처음 id로 읽음. 그 사이 파일이 바뀌었으면 도구가 `SNAPSHOT_MISMATCH` (조용히 새 자료를 읽지 않음). 한계: 그 id의 파일을 따로 보존하지 않으므로 파일이 바뀐 뒤 오래 멈춰 있던 실행의 추가 조사·검토 반영은 새 실행이 필요 (발행 스냅샷 폴더를 읽는 경로는 다음 단계, 데이터 모델 운영 메모) |
| `/qa` | 요청 안에서 그래프를 끝까지 돌려 Guard를 지난 문장만 한 번에 응답 |
| 현황·지도·후보 목록 | 활성 스냅샷 · 기준일 · 도시 · 분석 모드가 같은 **발행된 정기 감시 실행**(도시 하나, 검토 예산 K)에서 읽음. 없으면 요청 안에서 한 번 돌려 발행(LLM 없이 규칙·템플릿만이라 비용 없음, 등급은 규칙이 정함, 감사 `run_created`의 `origin=dashboard`). 한계: 이 GET 은 조회 역할로도 실행 행을 만들 수 있음(LLM 비용은 없지만 상태 DB 에 씀). 배치가 미리 만들어 두고 GET 은 읽기만 하는 방식은 다음 단계. 실제 도구로 처음 한 번은 몇 초(dev 스냅샷 약 8초) 걸리고 그다음은 바로. 같은 열쇠의 정기 감시가 도는 중이면 409 `RUN_NOT_READY`(잠시 뒤 다시). 현황은 7일 전 기준일 실행도 같은 방식으로 읽어 비교 |
| 후보 ID | 응답의 `monitoring_run_id` 실행의 ID = `POST /runs`가 받는 ID. 요청에 `monitoring_run_id`를 주면 그 실행에서만(화면은 늘 줌), 없으면 활성 스냅샷의 실행부터, 최근 실행부터 찾음. 요청에 `city`를 주면 그 도시 후보만 |
| 자원 조회 | 원장 v2 + `find_resources`와 같은 매칭·제외 규칙 (§3.12) |
| 후속 확인 | `GET /followups`는 저장소의 검토 기록에서 (§3.16) |

설정: `LINKRADAR_TOOLS`(real / stub), `LINKRADAR_LLM`(none / claude / 가짜 변형), 체크포인트는 `<상태 DB 이름>.agent.sqlite`. 그래프 실행은 작업자 스레드 1개에서 하고(`--workers 1`), `LINKRADAR_FAKE_DELAY=0`이면 요청 안에서 바로 돌립니다.
