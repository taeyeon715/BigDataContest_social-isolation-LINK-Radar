// 화면 문장에 내부 표시(규칙 판 rules-v1.1, 설정 파일 rules.yaml, 설정 열쇠 context_only, 규칙 ID L2-…, 영어 값)가 보이지 않는다.
// 규칙 이름·판은 등급 조건 "자세히"와 인쇄 꼬리말에 남는다.
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { Candidate } from '../api'
import { CandidateCard } from '../components/CandidateCard'
import { PrintReport } from '../components/PrintReport'
import { LevelExplain } from '../components/ReportParts'
import { VERDICT_LABEL, counterView, hideInternal, limitText, plainText, reportText } from '../lib/labels'
import { REPORT_501 } from './fixtures'

const INTERNAL = /rules-v\d|\.yaml|context_only|target_population|city_guidance|holiday_path|\b(?:L[0-3]|HOLD|CAP)-[A-Z]|scheduled_access_review|unexpected_activity_deviation|strict_as_of/

describe('내부 표시 숨기기 (문장)', () => {
  it('등급 이유 끝의 규칙 판·규칙 ID를 뺀다', () => {
    const r = '구조 취약성 상위(1인세대 비율 0.61, 강남 22개 동 전체 중 2/22위) + 예정된 트리거(R1 추석 연휴 D-7) [rules-v1.1 L2-STRUCT-TRIGGER+CAP-PARTIAL]'
    expect(plainText(r)).toBe('구조 취약성 상위(1인세대 비율 0.61, 강남 22개 동 전체 중 2/22위) + 예정된 트리거(R1 추석 연휴 D-7)')
    expect(plainText('… 도시 중앙값 0.120 미만이라 이 집단의 구조 상위로 보지 않음 (rules-v1.1 target_population)')).toBe(
      '… 도시 중앙값 0.120 미만이라 이 집단의 구조 상위로 보지 않음',
    )
    expect(plainText('동을 고르지 않음 (1인세대 밀집이 이 집단의 고립과 반대 방향, rules.yaml city_guidance)')).toBe(
      '동을 고르지 않음 (1인세대 밀집이 이 집단의 고립과 반대 방향)',
    )
    expect(plainText("맥락만 (rules-v1.1, 등급 입력 아님): '생활 폭' 대리")).toBe("맥락만 (등급 입력 아님): '생활 폭' 대리")
  })

  it('설정 열쇠 context_only 는 쉬운 말로 바꾼다', () => {
    const s = '전입 맥락 표시만 (rules.yaml context_only). 이사 뒤 관계망 약화는 근거 방향이 섞여 위험 요인으로 쓰지 않음'
    expect(limitText(s)).toBe('전입 맥락 표시만 (참고용 맥락, 등급에 쓰지 않음). 이사 뒤 관계망 약화는 근거 방향이 섞여 위험 요인으로 쓰지 않음')
    expect(reportText(s)).not.toMatch(INTERNAL)
  })

  it('영어 값·앞머리 오류 코드는 한국어로', () => {
    expect(limitText('TIME_AVAILABILITY_UNKNOWN: 카드 실제 공개일을 몰라 strict_as_of 에서 쓰지 않음')).toBe(
      '카드 실제 공개일을 몰라 기준일 엄격 모드에서 쓰지 않음',
    )
    expect(hideInternal('scheduled_access_review 후보')).toBe('예정된 점검 후보')
    expect(hideInternal('unexpected_activity_deviation')).toBe('예상 밖 이탈')
    expect(VERDICT_LABEL.REVISE).toBe('다시 확인')
  })

  it('등급 설명 첫 줄은 규칙 이름·판 없이', () => {
    expect(hideInternal('적용 규칙 L2-STRUCT-TRIGGER+CAP-PARTIAL (rules-v1.1) → Level 2')).toBe('적용 규칙 → Level 2')
    expect(hideInternal('적용 규칙 기록 없음 → Level 2 (등급 이유: -)')).toBe('적용 규칙 기록 없음 → Level 2 (등급 이유: -)')
    // 이어 묻기 답 줄 (서버는 LLM 입력과 같게 두고 화면이 뺌)
    expect(reportText('등급 이유: 적용 규칙 L2-STRUCT-TRIGGER (rules-v1.1) → Level 2')).toBe('등급 이유: 적용 규칙 → Level 2')
  })

  it('내부 표시가 없는 문장은 그대로 둔다 (자원 ID·근거 ID·트리거 이름)', () => {
    for (const s of ['R1 추석 연휴 D-7 · 외로움안녕120', '근거 [EV-501-STR-A] 참고', 'Level 3 불가 (부분 판정: 거리 없음)', '1인세대 비율 상위 , 값']) {
      expect(hideInternal(s)).toBe(s)
    }
  })
})

const CAND: Candidate = {
  candidate_id: 'C-20250926-008',
  candidate_type: 'unexpected_activity_deviation',
  city: '춘천',
  adm_cd: null,
  check_first_adm: [],
  analysis_cohort: '60대·70대·80대+',
  service_eligibility_group: '65세 이상',
  trigger: null,
  input_status: '충족',
  missing_inputs: [],
  counter_result: '보류',
  counter_reason: '도시 공통 변화: 연휴 뒤 기대 경로 안 (rules.yaml holiday_path): 회복 중',
  candidate_level: '판단 보류',
  level_reason: '반증 보류: 도시 공통 변화 [rules-v1.1 HOLD-COUNTER]',
  queue_rank: null,
  selected_for_review: false,
  selection_budget: null,
  evidence_ids: [],
  is_shared_context: false,
}

describe('내부 표시 숨기기 (화면)', () => {
  it('후보 카드의 반증 사유에 설정 파일 이름이 없다', () => {
    expect(counterView(CAND).reason).toBe('연휴 뒤 기대 경로 안: 회복 중')
    render(
      <MemoryRouter>
        <CandidateCard c={CAND} query="" />
      </MemoryRouter>,
    )
    const card = screen.getByTestId('candidate-card')
    expect(card).toHaveTextContent('연휴 뒤 기대 경로 안')
    expect(card.textContent).not.toMatch(INTERNAL)
  })

  it('규칙 이름·판은 등급 조건 "자세히"에만 (보고서 설명이 없어도 후보 칸으로)', () => {
    render(
      <LevelExplain
        checks={[{ condition: '구조 상위 (도시 안 상위 30%)', met: true, value: 2, threshold: 7, comparator: '<=', unit: '위' }]}
        ruleId="L2-STRUCT-TRIGGER"
        ruleVersion="rules-v1.1"
      />,
    )
    const box = screen.getByTestId('level-explain')
    const details = screen.getByTestId('rule-id-details')
    expect(details).toHaveTextContent('규칙 L2-STRUCT-TRIGGER · rules-v1.1')
    expect(details.tagName).toBe('DETAILS')
    const outside = box.textContent!.replace(details.textContent!, '')
    expect(outside).not.toMatch(INTERNAL)
  })

  it('인쇄: "등급을 정한 이유"에는 규칙 이름이 없고 꼬리말에 규칙·판이 남는다', () => {
    const exp = {
      ...REPORT_501.level_explanations![0],
      rule_id: 'L2-STRUCT-TRIGGER',
      rule_version: 'rules-v1.1',
      lines: ['적용 규칙 L2-STRUCT-TRIGGER (rules-v1.1) → Level 2', '구조 상위 조건: 충족(2위, 기준 7위 이하)'],
    }
    render(
      <PrintReport
        candidate={null}
        card={REPORT_501.evidence_card}
        report={{ ...REPORT_501, level_explanations: [exp] }}
        runId="r"
        snapshotId="snap-1"
        judgements={[]}
        approval={null}
      />,
    )
    const sheet = screen.getByTestId('print-report')
    expect(within(sheet).getByText('적용 규칙 → Level 2')).toBeInTheDocument()
    expect(screen.getByTestId('rule-key')).toHaveTextContent('등급 규칙 기록: L2-STRUCT-TRIGGER · rules-v1.1')
  })
})
