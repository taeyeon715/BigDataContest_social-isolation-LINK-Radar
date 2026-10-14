// rules-v1.1 후보 칸 (맥락·명절 묶음·폭염 단계)과 보고서 "추가로 확인한 것" 근거 링크
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Candidate, type ContextSignal, type Investigation, type TimelineStep } from '../api'
import { CandidateContext, DecisionTrace, EventTimeline } from '../components/ReportParts'
import CandidateDetail from '../pages/CandidateDetail'
import { REPORT_501, RUN_RESOURCES } from './fixtures'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '춘천', as_of: '2025-09-26', query: '' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

afterEach(() => {
  vi.restoreAllMocks()
})

const EID = REPORT_501.evidence_card.evidence[0].evidence_id

const CONTEXT: ContextSignal[] = [
  { name: '연휴 외지 손님 비중', text: '외지 손님 비중 28.6% vs 직전 8주 주말 18.0% → +10.6%p: 이동과 일관', role: '반증 맥락', scope: '춘천 (도시)', evidence_id: EID, affects_level: false },
  { name: '1인세대 월 순증률', value: 0.004, unit: '비율', text: '전월 대비 1인세대 +0.40%. 전입 맥락 표시만', role: '맥락', scope: '춘천 남면', affects_level: false },
]

const TIMELINE: TimelineStep[] = [
  { phase: '전', code: 'R1', label: '연휴 D-7 준비 (09/26~10/02)', start: '2025-09-26', end: '2025-10-02', status: '지금', applies: true },
  { phase: '중', code: 'R2', label: '명절 당일·다음 날 (10/06~10/07)', start: '2025-10-06', end: '2025-10-07', status: '예정', applies: true },
  { phase: '후', code: 'R3', label: '연휴 뒤 회복 확인 (10/10~10/19) · 춘천 고령층만 후보', start: '2025-10-10', end: '2025-10-19', status: '예정', applies: false },
]

const INVESTIGATION: Investigation = {
  rounds: 1,
  items: [
    {
      key: 'Q-HOL|춘천|32010350', qid: 'Q-HOL', label: '연휴 회복 미확인', question: '지난 연휴 뒤 결제가 평소로 돌아왔나?', city: '춘천', adm_cd: '32010350',
      candidate_ids: ['C-1'], tool: 'calc_holiday_gap', round: 1, source: 'llm', why: '연휴 직전 후보라서', status: 'answered',
      finding: '광복절 연휴 뒤 결제는 평소 수준', evidence_ids: [EID],
    },
  ],
  not_picked: [],
  stopped: '남은 질문 없음',
  note: '추가로 확인한 것은 등급·순서·반증 판정에 쓰지 않음 (참고용)',
}

const cand: Candidate = {
  candidate_id: 'C-1', candidate_type: 'scheduled_access_review', city: '춘천', adm_cd: '32010350', check_first_adm: [],
  analysis_cohort: '60대·70대·80대+', service_eligibility_group: '65세 이상', trigger: 'R1 추석 연휴 D-7', input_status: '충족',
  missing_inputs: [], counter_result: '해당 없음', counter_reason: null, candidate_level: 'Level 3', level_reason: '구조 취약성 상위',
  queue_rank: 1, selected_for_review: true, selection_budget: 2, evidence_ids: [], is_shared_context: false,
  context: CONTEXT, event_bundle_id: '춘천:명절:20251003', event_timeline: TIMELINE, heat_stage: '폭염경보',
}

function mount() {
  vi.spyOn(api, 'candidates').mockResolvedValue({ as_of: '2025-09-26', city: '춘천', snapshot_id: 's', selection_budget: 2, items: [cand] })
  vi.spyOn(api, 'runs').mockResolvedValue({ items: [{ run_id: 'run-1' }] } as never)
  vi.spyOn(api, 'run').mockResolvedValue({
    run_id: 'run-1', snapshot_id: 'snap-1', status: 'awaiting_approval', report_revision: 1, published_report_revision: 1, report_stale: false,
    extra_rounds: 0, candidates: [cand], path: [], guard: [], abstain_reason: null, analysis_mode: 'simulated_replay', contract_check: null,
    decision_trace: [], errors: [],
  } as never)
  vi.spyOn(api, 'evidence').mockResolvedValue({ run_id: 'run-1', snapshot_id: 'snap-1', evidence_card: REPORT_501.evidence_card })
  vi.spyOn(api, 'report').mockResolvedValue({
    run_id: 'run-1', snapshot_id: 'snap-1', report_stale: false,
    report: { ...REPORT_501, report_revision: 1, actions: [], investigation: INVESTIGATION },
  } as never)
  vi.spyOn(api, 'history').mockResolvedValue({ run_id: 'run-1', reviews: [], approvals: [], report_revisions: [], finalized: [] })
  vi.spyOn(api, 'resources').mockResolvedValue({ adm_cd: null, need_type: null, group: null, items: [] })
  vi.spyOn(api, 'runResources').mockResolvedValue({ ...RUN_RESOURCES, run_id: 'run-1' })
  render(
    <MemoryRouter initialEntries={['/candidates/C-1']}>
      <Routes>
        <Route path="/candidates/:id" element={<CandidateDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('후보 맥락 칸', () => {
  it('쉬운 이름과 "참고(등급에 안 씀)"를 보이고 근거 ID 를 누르면 onCite', async () => {
    const onCite = vi.fn()
    render(<CandidateContext items={CONTEXT} onCite={onCite} />)
    const box = screen.getByTestId('candidate-context')
    expect(box).toHaveTextContent('참고(등급에 안 씀)')
    expect(box).toHaveTextContent('다른 원인 참고')
    expect(box).not.toHaveTextContent('반증 맥락')
    expect(screen.getAllByTestId('context-signal')).toHaveLength(2)
    // 근거 표에 없는 근거: ID 대신 "참고 근거 · 이름", ID 는 title 로만
    const ref = within(box).getByTestId('context-ref')
    expect(ref).toHaveTextContent('참고 근거 · 연휴 외지 손님 비중')
    expect(ref).toHaveAttribute('title', `근거 ID ${EID}`)
    expect(box).not.toHaveTextContent(EID)
  })

  it('근거 표에 있는 근거는 "근거 n" 버튼으로 누르면 onCite', async () => {
    const onCite = vi.fn()
    render(<CandidateContext items={CONTEXT} onCite={onCite} refName={(id) => (id === EID ? '근거 3' : undefined)} />)
    await userEvent.click(within(screen.getByTestId('candidate-context')).getByRole('button', { name: '근거 3' }))
    expect(onCite).toHaveBeenCalledWith(EID)
  })

  it('맥락이 없으면 아무것도 그리지 않는다', () => {
    const { container } = render(<CandidateContext items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('명절 묶음: 지금 단계 표시, 적용 안 되는 단계는 표시만', () => {
    render(<EventTimeline steps={TIMELINE} bundleId="춘천:명절:20251003" />)
    const tl = screen.getByTestId('event-timeline')
    expect(tl).toHaveAttribute('data-bundle', '춘천:명절:20251003')
    expect(tl.querySelectorAll('li')).toHaveLength(3)
    expect(tl.querySelector('[aria-current="step"]')).toHaveTextContent('R1')
    expect(tl).toHaveTextContent('이 집단은 이 단계 후보가 아님')
  })

  it('DecisionTrace hideExtra 면 조사 루프 칸을 겹쳐 그리지 않는다', () => {
    const steps = [
      { node: 'check_findings', choice: null, source: 'rule', rule_ids: [], reason: null, inputs: { rounds: 1, items: INVESTIGATION.items }, changed: [] },
    ] as never
    const { rerender } = render(<DecisionTrace steps={steps} />)
    expect(screen.getByTestId('extra-checks')).toBeInTheDocument()
    rerender(<DecisionTrace steps={steps} hideExtra />)
    expect(screen.queryByTestId('extra-checks')).toBeNull()
  })
})

describe('후보 상세: v1.1 칸과 추가로 확인한 것', () => {
  it('폭염 단계·명절 묶음·맥락을 보이고, 추가로 확인한 것의 근거(번호)를 누르면 근거 표 그 행으로', async () => {
    mount()
    const extra = await screen.findByTestId('extra-checks')
    expect(extra).toHaveTextContent('지난 연휴 뒤 결제가 평소로 돌아왔나?')
    expect(extra).toHaveTextContent('AI가 고름')
    expect(within(screen.getByTestId('summary')).getByTestId('heat-stage')).toHaveTextContent('폭염경보')
    expect(screen.getByTestId('event-timeline')).toBeInTheDocument()
    expect(screen.getByTestId('candidate-context')).toHaveTextContent('연휴 외지 손님 비중')
    // 근거 ID 대신 근거 표 번호로 보이고, ID 는 title 로 남김
    const btn = within(extra).getByRole('button', { name: '근거 1' })
    expect(btn).toHaveAttribute('title', EID)
    await userEvent.click(btn)
    expect(document.getElementById(EID)).toHaveClass('bg-brand-50')
  })
})
