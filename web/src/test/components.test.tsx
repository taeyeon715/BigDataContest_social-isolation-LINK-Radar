import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { DISCLAIMER, type Candidate, type EvidenceCard, type QAResponse } from '../api'
import { ApprovalPanel, approvalBlock } from '../components/ApprovalPanel'
import { CandidateCard } from '../components/CandidateCard'
import { Footer } from '../components/Layout'
import { resourceFlags } from '../components/ResourcePanel'
import { CounterChip, HoldNotice, InputStatusBadge, LevelBadge, TimeLabelBar } from '../components/ui'
import { counterView, tabOf } from '../lib/labels'
import { QAResult } from '../pages/QA'

const base: Candidate = {
  candidate_id: 'C-20250926-004',
  candidate_type: 'unexpected_activity_deviation',
  city: '춘천',
  adm_cd: null,
  check_first_adm: ['32010630', '32010350'],
  analysis_cohort: '80대+',
  service_eligibility_group: '65세 이상',
  trigger: null,
  input_status: '부분 판정',
  missing_inputs: ['weather_asos'],
  counter_result: '보류',
  counter_reason: '강수 반증 자료 없음',
  candidate_level: '판단 보류',
  level_reason: '반증 보류',
  queue_rank: 4,
  selected_for_review: false,
  selection_budget: 2,
  evidence_ids: ['EV-20250926-0007'],
  is_shared_context: true,
}

describe('보류·부분 판정 표시', () => {
  it('판단 보류 배지와 부분 판정 배지를 보인다', () => {
    render(
      <MemoryRouter>
        <CandidateCard c={base} query="?as_of=2025-09-26&city=춘천" />
      </MemoryRouter>,
    )
    const card = screen.getByTestId('candidate-card')
    expect(within(card).getByTestId('level-badge')).toHaveTextContent('판단 보류')
    expect(within(card).getByTestId('input-status')).toHaveTextContent('근거 일부 빠짐')
    expect(within(card).getByTestId('input-status')).toHaveAttribute('title', expect.stringContaining('부분 판정'))
    // 내부 코드 대신 한국어 이름
    expect(card).toHaveTextContent('빠진 근거: 기상 관측 날씨 (ASOS)')
    expect(card).not.toHaveTextContent('weather_asos')
    expect(card).toHaveTextContent('먼저 확인할 동(공유 맥락): 효자1동, 남면')
    expect(card).toHaveAttribute('href', '/candidates/C-20250926-004?as_of=2025-09-26&city=춘천')
  })

  it('필수 근거가 다 있으면 부분 판정 배지가 없다', () => {
    const { container } = render(<InputStatusBadge status="충족" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('판단 보류 안내에 사유와 필요한 자료를 보인다', () => {
    render(<HoldNotice title="판단 보류" reasons={['강수 반증 자료 없음']} needed={['ASOS 강수']} />)
    expect(screen.getByTestId('hold-notice')).toHaveTextContent('강수 반증 자료 없음')
    expect(screen.getByTestId('hold-notice')).toHaveTextContent('ASOS 강수')
  })

  it('Level 배지', () => {
    render(<LevelBadge level="Level 3" withName />)
    expect(screen.getByTestId('level-badge')).toHaveTextContent('Level 3 · 우선 현장 검토')
  })
})

describe('반증 결과 나누기', () => {
  it('검토 중 / 도시 공통 변화 / 기각 / 보류를 구분한다', () => {
    expect(counterView({ counter_result: '보류', counter_reason: '검토 중: 연휴 뒤 10일 전' }).label).toBe('검토 중')
    expect(counterView({ counter_result: '보류', counter_reason: '도시 공통 변화 / 확인 필요: 도시 전체도 낮음' }).label).toBe('도시 공통 변화 / 확인 필요')
    expect(counterView({ counter_result: '보류', counter_reason: '강수 반증 자료 없음' }).label).toBe('다른 원인 확인 보류')
    expect(counterView({ counter_result: '기각', counter_reason: '공휴일 기대값' }).tone).toBe('reject')
    render(<CounterChip c={{ counter_result: '보류', counter_reason: '검토 중: x' }} />)
    expect(screen.getByTestId('counter-chip')).toHaveTextContent('검토 중')
  })

  it('기각·판단 보류는 기각·보류 탭으로 간다', () => {
    expect(tabOf(base)).toBe('rejected')
    expect(tabOf({ ...base, candidate_level: 'Level 2', counter_result: '통과', input_status: '충족', missing_inputs: [] })).toBe('deviation')
    expect(tabOf({ ...base, candidate_level: 'Level 1', counter_result: '기각' })).toBe('rejected')
  })
})

describe('승인 잠금', () => {
  it('보고서를 다시 만드는 중이면 승인 버튼이 잠기고 이유를 보인다', () => {
    const onDecide = vi.fn()
    render(<ApprovalPanel run={{ status: 'awaiting_approval', report_revision: 3, report_stale: true, abstain_reason: null }} onDecide={onDecide} />)
    expect(screen.getByRole('button', { name: '승인' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '보류' })).toBeDisabled()
    expect(screen.getByTestId('approval-block')).toHaveTextContent('3판으로 다시 만드는 중')
  })

  it('최신 판이면 승인할 수 있다', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined)
    render(<ApprovalPanel run={{ status: 'awaiting_approval', report_revision: 3, report_stale: false, abstain_reason: null }} onDecide={onDecide} />)
    const btn = screen.getByRole('button', { name: '승인' })
    expect(btn).toBeEnabled()
    await userEvent.click(btn)
    expect(onDecide).toHaveBeenCalledWith('approved', null)
  })

  it('계약 위반 보류는 승인으로 풀 수 없다', () => {
    expect(approvalBlock({ status: 'abstained', report_revision: null, report_stale: false, abstain_reason: 'GRAIN_FORBIDDEN_COMBO' })).toMatch('승인으로 풀 수 없습니다')
    expect(approvalBlock({ status: 'approved', report_revision: 1, report_stale: false, abstain_reason: null })).toMatch('확정')
  })

  it('승인 거절(409) 사유를 그대로 보인다', async () => {
    const { ApiError } = await import('../api')
    const onDecide = vi.fn().mockRejectedValue(new ApiError(409, 'REPORT_REVISION_STALE', '보고서가 3판으로 바뀌었습니다. 다시 확인한 뒤 승인하세요.'))
    render(<ApprovalPanel run={{ status: 'awaiting_approval', report_revision: 2, report_stale: false, abstain_reason: null }} onDecide={onDecide} />)
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    // 쉬운 문장이 먼저, 코드는 작게 따로
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('보고서가 3판으로 바뀌었습니다')
    expect(alert).toHaveTextContent('오류 코드 REPORT_REVISION_STALE')
  })
})

describe('고정 고지', () => {
  it('하단 영역에 "개인 판정 아님 · 최종 판단은 담당자"가 늘 있다', () => {
    render(<Footer runId={null} snapshot={null} />)
    expect(screen.getByTestId('disclaimer')).toHaveTextContent(DISCLAIMER)
    expect(DISCLAIMER).toBe('개인 판정 아님 · 최종 판단은 담당자')
  })

  it('simulated_replay 결과에 가정한 공개 시점을 표시한다', () => {
    render(<TimeLabelBar label="가정한 공개 시점: 실제 공개일이 아니라 관측일 + 가정한 공급 지연으로 재생한 결과" />)
    expect(screen.getByTestId('time-label')).toHaveTextContent('가정한 공개 시점')
    expect(screen.getByTestId('time-label')).toHaveTextContent('관측일 + 가정한 공급 지연')
  })

  it('time_label 이 없으면 띠도 없다', () => {
    const { container } = render(<TimeLabelBar label={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('자원 상태', () => {
  it('확인 필요 / 위치 확인 필요 / 권역 외 / 전화형', () => {
    const r = {
      resource_id: 'R0104', name: '서울잇다플레이스', resource_type: '1인가구지원', city: '강남' as const, adm_cd: null,
      target_group: '청년·중장년', eligibility: '확인 필요' as const, operating_status: '확인 필요' as const, distance_km: null,
      phone_based: false, source_url: 'https://www.data.go.kr/', verified_date: '2026-03-18',
      warnings: ['권역 외: 강남구 밖 성동구 거점', '위치 확인 필요: 위치·연락처 미확인'],
    }
    expect(resourceFlags(r).map((f) => f.label)).toEqual(['자격 확인 필요', '운영 확인 필요', '위치 확인 필요', '권역 외'])
    expect(resourceFlags({ ...r, phone_based: true, eligibility: '적합', operating_status: '운영', warnings: [] }).map((f) => f.label)).toEqual(['전화형'])
  })
})

describe('질의응답', () => {
  it('보류 답변은 사유 코드와 필요한 자료를 보이고 답 문장은 없다', () => {
    const res: QAResponse = {
      run_id: 'run-x', status: 'abstained', evidence_card: null, answer: null, guard: [],
      abstain_reason: 'GRAIN_FORBIDDEN_COMBO: 체류인구: 연령 × 시간대 조합은 지원하지 않음',
      needed_data: ['연령 × 시간대가 함께 있는 통신 자료'],
    }
    render(<QAResult res={res} />)
    expect(screen.getByText('답할 수 없는 질문입니다')).toBeInTheDocument()
    expect(screen.getByText('GRAIN_FORBIDDEN_COMBO')).toBeInTheDocument()
    expect(screen.getByTestId('hold-notice')).toHaveTextContent('연령 × 시간대가 함께 있는 통신 자료')
    expect(screen.queryByTestId('qa-answer')).toBeNull()
  })

  it('근거 표가 먼저 보이고 설명 문장은 그 뒤에 붙는다', async () => {
    const card: EvidenceCard = {
      card_id: 'CARD-1', run_id: 'run-y', candidate_id: null, title: '강남 1인세대 비율 상위 동', snapshot_id: 's',
      analysis_mode: 'retrospective', as_of: '2025-10-20', evidence_revision: 1, time_label: null, disclaimer: DISCLAIMER,
      facts: [{ metric: '1인세대 비율', region: '강남 역삼1동', period: '2025-09', value: 0.68, unit: '비율', direction: null, evidence_id: 'EV-20251020-0001' }],
      evidence: [{
        evidence_id: 'EV-20251020-0001', metric: '1인세대 비율', value: 0.68, unit: '비율', denominator: '전체 세대수', n_obs: 1, direction: null,
        measured_grain: '행정동 × 월', applied_scope: '강남 역삼1동', merchant_region: null, customer_residence: null, is_shared_context: false,
        analysis_cohort: null, service_eligibility_group: null, observed_from: '2025-09-30', observed_to: '2025-09-30', analysis_mode: 'retrospective',
        as_of: '2025-10-20', actual_available_at: null, simulated_available_at: null, availability_basis: 'assumed', trained_until: null,
        rule_derived_from: null, snapshot_id: 's', data_version: null, rule_version: null, interpretation_status: '직접 확인',
        valid_claims: ['1인세대 비율'], forbidden_claims: ['1인가구'], missing_inputs: [], warnings: [], status: 'OK', value_state: 'observed',
        error_code: null, calibrated_until: null,
      }],
      limitations: ['주민등록 1인세대는 1인가구와 다름'],
    }
    const res: QAResponse = {
      run_id: 'run-y', status: 'answered', evidence_card: card, guard: [{ guard: 'guard_report', check: '사실', verdict: 'PASS', rule_ids: [], reasons: [], attempt: 1 }],
      answer: '강남 역삼1동의 1인세대 비율은 0.68입니다 [EV-20251020-0001].', abstain_reason: null, needed_data: [],
    }
    render(<QAResult res={res} />)
    expect(screen.getByTestId('evidence-table')).toBeInTheDocument()
    expect(screen.queryByTestId('qa-answer')).toBeNull()
    const ans = await screen.findByTestId('qa-answer')
    expect(ans).toHaveTextContent('0.68')
    expect(within(ans).getByRole('button', { name: 'EV-20251020-0001' })).toBeInTheDocument()
  })
})
