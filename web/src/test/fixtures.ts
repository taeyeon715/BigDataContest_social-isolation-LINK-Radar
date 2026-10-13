// 가짜 API(api/services/fake_data.py build_report)가 만든 보고서 하나를 그대로 옮긴 시험 자료 (C-20250926-501, 판단 기록은 일부만).
import type { ApproveRequest, FollowupResponse, Report, RunResourcesResponse } from '../api'

export const REPORT_501: Report = {
  "report_revision": 1,
  "evidence_card": {
    "card_id": "CARD-run-x-1",
    "run_id": "run-x",
    "candidate_id": "C-20250926-501",
    "title": "Level 2 · 예정된 점검 · 강남 논현1동 · 중장년 남성 1인가구 · R1 추석 연휴 D-7",
    "snapshot_id": "snap-fake-1",
    "analysis_mode": "simulated_replay",
    "as_of": "2025-09-26",
    "evidence_revision": 1,
    "facts": [
      {
        "metric": "1인세대 비율",
        "region": "강남 논현1동",
        "period": "2025-08",
        "value": 0.61,
        "unit": "비율",
        "direction": null,
        "evidence_id": "EV-20250926-501-1"
      }
    ],
    "evidence": [
      {
        "evidence_id": "EV-20250926-501-1",
        "metric": "1인세대 비율",
        "value": 0.61,
        "unit": "비율",
        "denominator": "전체 세대수 (주민등록)",
        "n_obs": 1,
        "direction": null,
        "measured_grain": "행정동 × 월",
        "applied_scope": "강남 논현1동",
        "merchant_region": null,
        "customer_residence": null,
        "is_shared_context": false,
        "analysis_cohort": null,
        "service_eligibility_group": null,
        "observed_from": "2025-08-31",
        "observed_to": "2025-08-31",
        "analysis_mode": "simulated_replay",
        "as_of": "2025-09-26",
        "actual_available_at": null,
        "simulated_available_at": "2025-09-03",
        "availability_basis": "assumed",
        "trained_until": null,
        "rule_derived_from": null,
        "snapshot_id": "snap-fake-1",
        "data_version": "resident@sha256:fake71d0/clean-v1",
        "rule_version": "rules-v0.1",
        "interpretation_status": "직접 확인",
        "valid_claims": [
          "논현1동 주민등록 1인세대 비율 (강남 내 2위)"
        ],
        "forbidden_claims": [
          "1인가구 비율 (개념이 다름)",
          "고립 위험이 높은 동"
        ],
        "missing_inputs": [],
        "warnings": [
          "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과"
        ],
        "status": "OK",
        "value_state": "observed",
        "error_code": null,
        "calibrated_until": null
      },
      {
        "evidence_id": "EV-20250926-501-2",
        "metric": "달력·계절 트리거",
        "value": "R1 추석 연휴 D-7",
        "unit": null,
        "denominator": null,
        "n_obs": null,
        "direction": null,
        "measured_grain": "도시 × 일",
        "applied_scope": "강남",
        "merchant_region": null,
        "customer_residence": null,
        "is_shared_context": false,
        "analysis_cohort": null,
        "service_eligibility_group": null,
        "observed_from": "2025-09-26",
        "observed_to": "2025-09-26",
        "analysis_mode": "simulated_replay",
        "as_of": "2025-09-26",
        "actual_available_at": null,
        "simulated_available_at": "2025-09-26",
        "availability_basis": "assumed",
        "trained_until": null,
        "rule_derived_from": null,
        "snapshot_id": "snap-fake-1",
        "data_version": "calendar@fake-v1",
        "rule_version": "rules-v0.1",
        "interpretation_status": "직접 확인",
        "valid_claims": [
          "추석 연휴 D-7 기간"
        ],
        "forbidden_claims": [
          "활동 위축이 확인된 후보"
        ],
        "missing_inputs": [],
        "warnings": [
          "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과"
        ],
        "status": "OK",
        "value_state": "observed",
        "error_code": null,
        "calibrated_until": null
      },
      {
        "evidence_id": "EV-20250926-501-3",
        "metric": "중장년 남성 1인가구 '소통 적음' 남녀 비 (여성 대비)",
        "value": 1.8,
        "unit": "배",
        "denominator": "같은 연령 여성의 '소통 적음' 비율",
        "n_obs": null,
        "direction": null,
        "measured_grain": "행정동 × 성 × 5세 연령 × 월 (강남 전체로 합침)",
        "applied_scope": "강남 전체 · 탐색적",
        "merchant_region": null,
        "customer_residence": null,
        "is_shared_context": true,
        "analysis_cohort": null,
        "service_eligibility_group": null,
        "observed_from": "2025-07-01",
        "observed_to": "2025-08-31",
        "analysis_mode": "simulated_replay",
        "as_of": "2025-09-26",
        "actual_available_at": null,
        "simulated_available_at": "2025-09-03",
        "availability_basis": "assumed",
        "trained_until": null,
        "rule_derived_from": null,
        "snapshot_id": "snap-fake-1",
        "data_version": "seoul_living@fake",
        "rule_version": "rules-v0.1",
        "interpretation_status": "가설",
        "valid_claims": [
          "강남 전체에서 중장년 남성 1인가구의 '소통 적음'이 여성보다 많음 (탐색적 참고)"
        ],
        "forbidden_claims": [
          "논현1동 중장년 남성이 고립됨",
          "등급 입력으로 사용"
        ],
        "missing_inputs": [],
        "warnings": [
          "검증 참고 자료이며 등급 입력이 아님",
          "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과"
        ],
        "status": "OK",
        "value_state": "observed",
        "error_code": null,
        "calibrated_until": null
      }
    ],
    "limitations": [
      "개인 판단 아님",
      "동 단위 카드 소비는 알 수 없음"
    ],
    "time_label": "가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과",
    "disclaimer": "개인 판정 아님 · 최종 판단은 담당자"
  },
  "fact_sentences": [
    "2025-08 강남 논현1동의 1인세대 비율은(는) 0.61입니다 [EV-20250926-501-1].",
    "대응안 C-20250926-501: 주체 동 복지팀(맞춤형복지팀) / 방법 전화 안부 → 방문 대상 추리기 / 기한 연휴 전날까지 (2025-10-02까지) / 확인할 영역 건강·복약, 사회적 관계·연락 / 할 일 휴일 운영 약국·의료기관 안내, 상시 복용약 확인 / 연계 자원: 외로움안녕120(전화형, 자격 적합, 운영 운영), 강남구 중장년 1인가구 자조모임(1인가구지원(청년·중장년), 자격 확인 필요, 운영 운영), 강남구 정신건강복지센터(상담/정신건강, 자격 적합, 운영 운영)"
  ],
  "narrative": "이 보고서는 집단·지역 단위 근거로 먼저 확인할 곳을 제안합니다. 개인을 판정하지 않으며 최종 판단은 담당자가 합니다.",
  "is_abstain": false,
  "needs_regeneration": false,
  "actions": [
    {
      "candidate_id": "C-20250926-501",
      "candidate_level": "Level 2",
      "group": "중장년 남성 1인가구",
      "where": "강남 논현1동",
      "check_areas": [
        {
          "area": "건강·복약",
          "reasons": [
            "연휴 진료 단절(R1)"
          ],
          "ask": "상시 복용약 남은 양, 연휴·특보 중 진료 일정"
        },
        {
          "area": "사회적 관계·연락",
          "reasons": [
            "1인세대 비율 상위·연락 공백",
            "중장년 남성 1인가구 소통 공백"
          ],
          "ask": "최근 연락한 사람, 연휴·주말에 만날 사람"
        }
      ],
      "check_area_note": "판단도구 문항 채점 아님, 현장에서 먼저 물어볼 영역",
      "action_plan": {
        "owner": [
          "동 복지팀(맞춤형복지팀)"
        ],
        "method": "전화 안부 → 방문 대상 추리기",
        "due": "연휴 전날까지 (2025-10-02까지)",
        "due_date": "2025-10-02",
        "check_areas": [
          "건강·복약",
          "사회적 관계·연락"
        ],
        "linked_resources": [
          "외로움안녕120(전화형, 자격 적합, 운영 운영)",
          "강남구 중장년 1인가구 자조모임(1인가구지원(청년·중장년), 자격 확인 필요, 운영 운영)",
          "강남구 정신건강복지센터(상담/정신건강, 자격 적합, 운영 운영)"
        ],
        "tasks": [
          "휴일 운영 약국·의료기관 안내, 상시 복용약 확인"
        ],
        "resource_note": null,
        "emergency_contacts": [],
        "text": "대응안 C-20250926-501: 주체 동 복지팀(맞춤형복지팀) / 방법 전화 안부 → 방문 대상 추리기 / 기한 연휴 전날까지 (2025-10-02까지) / 확인할 영역 건강·복약, 사회적 관계·연락 / 할 일 휴일 운영 약국·의료기관 안내, 상시 복용약 확인 / 연계 자원: 외로움안녕120(전화형, 자격 적합, 운영 운영), 강남구 중장년 1인가구 자조모임(1인가구지원(청년·중장년), 자격 확인 필요, 운영 운영), 강남구 정신건강복지센터(상담/정신건강, 자격 적합, 운영 운영)"
      },
      "checklist": {
        "lists": [
          "행복이음 위기정보 대상자: 강남 논현1동 · 40~64세 남성 대상만 걸러 보기",
          "고독사위기대응시스템 배분 명단: 강남 논현1동 · 40~64세 남성 대상만 걸러 보기",
          "기존 사례관리 가구: 강남 논현1동 · 40~64세 남성 대상만 걸러 보기"
        ],
        "contacts": [
          "동 복지팀(맞춤형복지팀)",
          "우리동네돌봄단·통리장",
          "연계 자원 외로움안녕120(전화형, 자격 적합, 운영 운영)",
          "연계 자원 강남구 중장년 1인가구 자조모임(1인가구지원(청년·중장년), 자격 확인 필요, 운영 운영)",
          "연계 자원 강남구 정신건강복지센터(상담/정신건강, 자격 적합, 운영 운영)"
        ],
        "method": "전화 안부 → 방문 대상 추리기",
        "due": "연휴 전날까지 (2025-10-02까지)",
        "check_areas": [
          "건강·복약",
          "사회적 관계·연락"
        ],
        "note": "시스템은 명단을 만들지 않음. 기존 명단 중 어느 것을 먼저 꺼낼지만 안내"
      }
    }
  ],
  "level_explanations": [
    {
      "candidate_id": "C-20250926-501",
      "candidate_level": "Level 2",
      "rule_id": "SCHED-L2",
      "rule_version": "rules-v0.1 (가짜)",
      "checks": [
        {
          "condition": "구조 상위 30%",
          "met": true,
          "value": "0.61 (2/21위)",
          "threshold": "상위 30%",
          "distance": null,
          "text": "구조 상위 30% 조건: 충족(0.61 (2/21위), 기준 상위 30%)"
        },
        {
          "condition": "1인세대 비율 최소값",
          "met": true,
          "value": 0.61,
          "threshold": 0.35,
          "distance": 0.26,
          "text": "1인세대 비율 최소값 조건: 충족(0.61, 기준 0.35 이상, 여유 0.26)"
        },
        {
          "condition": "관측 거점까지 직선거리",
          "met": false,
          "value": 0.3,
          "threshold": 5.0,
          "distance": 4.7,
          "text": "관측 거점까지 직선거리 조건: 미충족(0.3km, 기준 5km 이상, 부족 4.7km)"
        },
        {
          "condition": "예정된 트리거",
          "met": true,
          "value": "R1 추석 연휴 D-7",
          "threshold": null,
          "distance": null,
          "text": "예정된 트리거 조건: 충족(R1 추석 연휴 D-7)"
        }
      ],
      "what_if": [
        "관측 거점까지 직선거리 조건을 충족하면 Level 3 가능 (기준까지 4.7km 부족)"
      ],
      "lines": [
        "적용 규칙 SCHED-L2 (rules-v0.1 (가짜)) → Level 2",
        "구조 상위 30% 조건: 충족(0.61 (2/21위), 기준 상위 30%)",
        "1인세대 비율 최소값 조건: 충족(0.61, 기준 0.35 이상, 여유 0.26)",
        "관측 거점까지 직선거리 조건: 미충족(0.3km, 기준 5km 이상, 부족 4.7km)",
        "예정된 트리거 조건: 충족(R1 추석 연휴 D-7)",
        "관측 거점까지 직선거리 조건을 충족하면 Level 3 가능 (기준까지 4.7km 부족)"
      ]
    }
  ],
  "decision_trace": [
    {
      "node": "intake",
      "choice": "요청 정규화 됨",
      "next": "classify",
      "source": "rule",
      "rule_ids": [],
      "reason": null,
      "inputs": {},
      "changed": [],
      "started_at": null,
      "ms": null
    },
    {
      "node": "classify",
      "choice": "investigate",
      "next": "check_availability",
      "source": "llm",
      "rule_ids": [],
      "reason": "가짜: 규칙과 같은 경로",
      "inputs": {},
      "changed": [],
      "started_at": null,
      "ms": null
    },
    {
      "node": "check_availability",
      "choice": "OK",
      "next": "collect_signals",
      "source": "rule",
      "rule_ids": [],
      "reason": null,
      "inputs": {},
      "changed": [],
      "started_at": null,
      "ms": null
    },
    {
      "node": "assign_level",
      "choice": "Level 2 1",
      "next": null,
      "source": "rule",
      "rule_ids": [
        "SCHED-L2"
      ],
      "reason": null,
      "inputs": {},
      "changed": [],
      "started_at": null,
      "ms": null
    }
  ],
  "round_changes": [],
  "review_ids": [],
  "distribution": "내부용(대외비)",
  "external_notice": "특정 동네가 위험하다는 뜻이 아니라, 명절·계절에 돌봄이 끊기기 쉬운 이웃을 먼저 살피는 기간입니다"
}


// ---- docs/API_명세.md 예시를 그대로 옮긴 것 (타입이 명세와 맞는지 tsc 가 본다)

/** §3.10 추가 조사 요청 (focus) */
export const MORE_REQUEST: ApproveRequest = {
  decision: 'more',
  report_revision: 2,
  approver: 'lead-01',
  idempotency_key: 'run-20261013-100000-3fa9c2d1:more:2',
  memo: null,
  focus: {
    candidate_ids: ['C-20250926-004'],
    tools: ['calc_holiday_gap'],
    counter_checks: ['강수'],
    memo: '연휴 뒤 회복이 늦은지 더 봐 주세요',
  },
}

/** §3.13 실행에 묶인 자원 */
export const RUN_RESOURCES: RunResourcesResponse = {
  run_id: 'run-20261013-100000-3fa9c2d1',
  snapshot_id: 'snap-20261013-101500-a1b2',
  evidence_revision: 1,
  report_revision: 2,
  items: [
    {
      resource_id: 'R0007',
      name: '외로움안녕120',
      resource_type: '상담',
      city: '춘천',
      adm_cd: null,
      target_group: '전 연령',
      eligibility: '적합',
      operating_status: '운영',
      distance_km: null,
      phone_based: true,
      source_url: 'https://www.data.go.kr/',
      verified_date: '2026-03-18',
      warnings: [],
      candidate_id: 'C-20250926-001',
      judgement: '적합',
    },
  ],
}

/** §3.14 이어 묻기 답 */
export const FOLLOWUP_ANSWER: FollowupResponse = {
  run_id: 'run-20261013-100000-3fa9c2d1',
  followup_id: 'fup-20261013-101200-1a2b3c4d',
  status: 'answered',
  answer: '1인세대 비율 0.47(비율) — 춘천 남면 (32010350), 2025-08, 방향 없음 [EV-20250926-STR-32010350].\n개인 판정 아님 · 최종 판단은 담당자',
  cited_evidence_ids: ['EV-20250926-STR-32010350'],
  source: 'template',
  report_revision: 2,
  guard: [
    { guard: 'guard_report', check: '사실', verdict: 'PASS', rule_ids: [], reasons: [], attempt: 1 },
    { guard: 'guard_report', check: '표현', verdict: 'PASS', rule_ids: [], reasons: [], attempt: 1 },
  ],
  abstain_reason: null,
  needed_data: [],
}
