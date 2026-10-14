// 실제 API 계약에 맞춘 화면: 추가 조사 범위(focus), 보고서 다시 만드는 중 승인 잠금, 이어 묻기, 실행에 묶인 자원, 새 오류 코드.
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, APPROVER_NAME, type Candidate, type FollowupResponse, type Report } from '../api'
import { ApprovalPanel, approvalBlock, regenerating, remainingRounds } from '../components/ApprovalPanel'
import { FollowupPanel } from '../components/FollowupPanel'
import { evidenceLine } from '../components/EvidenceTable'
import { buildFocus } from '../components/MoreForm'
import { ERROR_TEXT, errorText, roundChangesView, splitReason } from '../lib/labels'
import CandidateDetail, { uniqueResources } from '../pages/CandidateDetail'
import { FOLLOWUP_ANSWER, MORE_REQUEST, REPORT_501, RUN_RESOURCES } from './fixtures'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '강남', as_of: '2025-09-26', query: '' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

afterEach(() => {
  vi.restoreAllMocks()
})

const ready = { status: 'awaiting_approval' as const, report_revision: 2, report_stale: false, abstain_reason: null, published_report_revision: 2, extra_rounds: 0 }
const CANDS = [
  { candidate_id: 'C-1', label: 'Level 2 · 강남 논현1동' },
  { candidate_id: 'C-2', label: 'Level 1 · 강남 역삼1동' },
]

describe('추가 조사 범위 (focus)', () => {
  it('고른 후보·도구·다른 원인·메모로 focus 를 만들어 보낸다', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined)
    render(<ApprovalPanel run={{ ...ready, extra_rounds: 1 }} onDecide={onDecide} moreCandidates={CANDS} moreDefaultIds={['C-1']} />)
    await userEvent.click(screen.getByRole('button', { name: '추가 조사 (남은 1회)' }))
    const form = screen.getByTestId('more-form')
    expect(within(form).getByTestId('more-remaining')).toHaveTextContent('남은 추가 조사 1회 (최대 2회)')
    expect(within(form).getByTestId('more-memo-warning')).toHaveTextContent('개인 정보·이름은 쓰지 마세요')
    expect(within(form).getByRole('checkbox', { name: /C-1/ })).toBeChecked() // 지금 후보는 미리 골라 둠
    await userEvent.click(within(form).getByRole('checkbox', { name: /연휴 전후 차이 계산/ }))
    await userEvent.click(within(form).getByRole('checkbox', { name: '강수' }))
    await userEvent.type(within(form).getByRole('textbox'), '연휴 뒤 회복이 늦은지')
    await userEvent.click(within(form).getByRole('button', { name: '추가 조사 보내기' }))
    expect(onDecide).toHaveBeenCalledWith('more', null, {
      candidate_ids: ['C-1'],
      tools: ['calc_holiday_gap'],
      counter_checks: ['강수'],
      memo: '연휴 뒤 회복이 늦은지',
    })
    await waitFor(() => expect(screen.queryByTestId('more-form')).toBeNull())
  })

  it('아무것도 안 고르면 focus 없이 보낸다. 메모는 500자에서 자른다', () => {
    expect(buildFocus({ ids: [], tools: [], checks: [], memo: '  ' })).toBeNull()
    expect(buildFocus({ ids: [], tools: [], checks: [], memo: 'x'.repeat(600) })?.memo).toHaveLength(500)
    expect(MORE_REQUEST.focus?.tools).toEqual(['calc_holiday_gap'])
  })

  it('2번을 다 쓰면 추가 조사 버튼이 잠긴다', () => {
    render(<ApprovalPanel run={{ ...ready, extra_rounds: 2 }} onDecide={vi.fn()} moreCandidates={CANDS} />)
    expect(remainingRounds({ ...ready, extra_rounds: 2 })).toBe(0)
    expect(screen.getByRole('button', { name: '추가 조사 (남은 0회)' })).toBeDisabled()
    expect(screen.getByTestId('more-exhausted')).toHaveTextContent('추가 조사 2번을 모두 썼습니다')
  })

  it('서버가 EXTRA_ROUNDS_EXCEEDED 로 답하면 쉬운 문장을 보이고 버튼을 잠근다', async () => {
    const onDecide = vi.fn().mockRejectedValue(new ApiError(409, 'EXTRA_ROUNDS_EXCEEDED', 'server text'))
    render(<ApprovalPanel run={ready} onDecide={onDecide} moreCandidates={CANDS} />)
    await userEvent.click(screen.getByRole('button', { name: '추가 조사 (남은 2회)' }))
    await userEvent.click(screen.getByRole('button', { name: '추가 조사 보내기' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('추가 조사는 2번까지입니다')
    expect(alert).toHaveTextContent('오류 코드 EXTRA_ROUNDS_EXCEEDED')
    expect(screen.getByRole('button', { name: '추가 조사 (남은 0회)' })).toBeDisabled()
  })

  it('CANDIDATE_NOT_IN_RUN 은 쉬운 문장으로', async () => {
    const onDecide = vi.fn().mockRejectedValue(new ApiError(400, 'CANDIDATE_NOT_IN_RUN', '이 실행에 없는 후보 ID입니다: C-9'))
    render(<ApprovalPanel run={ready} onDecide={onDecide} moreCandidates={CANDS} />)
    await userEvent.click(screen.getByRole('button', { name: /^추가 조사/ }))
    await userEvent.click(screen.getByRole('button', { name: '추가 조사 보내기' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('이 조사의 후보만 고르세요')
    expect(alert).toHaveTextContent('C-9') // 서버 문장은 보조로
  })
})

describe('새 오류 코드 문장', () => {
  it('서버 문장보다 쉬운 문장을 먼저', () => {
    for (const code of ['CANDIDATE_NOT_IN_RUN', 'RUN_NOT_READY', 'AGENT_ERROR', 'IDEMPOTENCY_KEY_REUSED', 'RUN_NOT_AWAITING_APPROVAL', 'EXTRA_ROUNDS_EXCEEDED']) {
      const t = errorText(code, 'internal-ish text')
      expect(t.title).not.toBe('internal-ish text')
      expect(t.detail).toBe('internal-ish text')
    }
    // 예전 코드는 서버 문장 그대로 (판 번호가 들어 있음)
    expect(errorText('REPORT_REVISION_STALE', '보고서가 3판으로 바뀌었습니다.').title).toBe('보고서가 3판으로 바뀌었습니다.')
    expect(splitReason('OUT_OF_SCOPE_INDIVIDUAL: 개인 지목')).toEqual({ code: 'OUT_OF_SCOPE_INDIVIDUAL', text: '개인 지목' })
    expect(splitReason('G-05: 통과 못함').code).toBe('G-05')
    // 이어 묻기에서 질문한 대상이 카드에 없을 때 (서버: "NO_TARGET: 이 카드에 맞는 대상이 없습니다 (...)")
    expect(ERROR_TEXT[splitReason('NO_TARGET: 이 카드에 맞는 대상이 없습니다 (남면)').code ?? '']).toBe('이 보고서에 맞는 대상이 없습니다. 새 조사를 시작하세요.')
    expect(splitReason('판단 보류된 보고서라 답하지 않음').code).toBeNull()
  })
})

describe('보고서 다시 만드는 중 승인 잠금', () => {
  it('요청한 판이 발행된 판보다 앞서면 승인할 수 없다 (report_stale 이 false 여도)', () => {
    const run = { ...ready, report_revision: 3, published_report_revision: 2 }
    expect(regenerating(run)).toBe(true)
    expect(approvalBlock(run, { run_id: 'r', snapshot_id: 's', report_revision: 2 })).toMatch('3판으로 다시 만드는 중')
    expect(regenerating({ ...ready, published_report_revision: undefined })).toBe(false) // 옛 응답
  })
})

// ---- 후보 상세 전체
const cand: Candidate = {
  candidate_id: 'C-20250926-501', candidate_type: 'scheduled_access_review', city: '강남', adm_cd: '11230510', check_first_adm: [],
  analysis_cohort: '40대·50대 남성', service_eligibility_group: '40~64세 남성', trigger: 'R1 추석 연휴 D-7', input_status: '충족',
  missing_inputs: [], counter_result: '해당 없음', counter_reason: null, candidate_level: 'Level 2', level_reason: '구조 취약성 상위',
  queue_rank: 1, selected_for_review: true, selection_budget: 2, evidence_ids: [], is_shared_context: false,
}

function runOf(over: Record<string, unknown>) {
  return {
    run_id: 'run-1', snapshot_id: 'snap-1', status: 'awaiting_approval', report_revision: 1, published_report_revision: 1, report_stale: false,
    extra_rounds: 0, candidates: [cand], path: [], guard: [], abstain_reason: null, analysis_mode: 'simulated_replay', contract_check: null,
    decision_trace: [], errors: [], ...over,
  } as never
}
function reportOf(rev: number, extra: Partial<Report> = {}) {
  return { run_id: 'run-1', snapshot_id: 'snap-1', report_stale: false, report: { ...REPORT_501, report_revision: rev, ...extra } }
}

function mountDetail(opts: { runs?: unknown[]; checked?: string[] } = {}) {
  vi.spyOn(api, 'candidates').mockResolvedValue({ as_of: '2025-09-26', city: '강남', snapshot_id: 's', selection_budget: 2, items: [cand] })
  vi.spyOn(api, 'runs').mockResolvedValue({ items: opts.runs ?? [{ run_id: 'run-1' }] } as never)
  vi.spyOn(api, 'evidence').mockResolvedValue({ run_id: 'run-1', snapshot_id: 'snap-1', evidence_card: REPORT_501.evidence_card })
  const reviews = opts.checked
    ? [{ review_id: 'rev-1', reviewer: 'w', checked_evidence_ids: opts.checked, resource_judgements: [], memo: null, follow_up_on: null, report_revision_before: 1, report_revision_after: 1, created_at: '2026-10-13T10:00:00+09:00' }]
    : []
  vi.spyOn(api, 'history').mockResolvedValue({ run_id: 'run-1', reviews, approvals: [], report_revisions: [], finalized: [] })
  const resources = vi.spyOn(api, 'resources').mockResolvedValue({ adm_cd: null, need_type: null, group: null, items: [] })
  const runResources = vi.spyOn(api, 'runResources').mockResolvedValue({ ...RUN_RESOURCES, run_id: 'run-1' })
  render(
    <MemoryRouter initialEntries={['/candidates/C-20250926-501']}>
      <Routes>
        <Route path="/candidates/:id" element={<CandidateDetail />} />
      </Routes>
    </MemoryRouter>,
  )
  return { resources, runResources }
}

describe('후보 상세: 실제 API 계약', () => {
  it('검토 뒤 새 판이 발행될 때까지 "보고서 다시 만드는 중"으로 승인을 잠그고, 따라잡으면 그 판으로 승인한다', async () => {
    const state = { requested: 2, published: 1 }
    vi.spyOn(api, 'run').mockImplementation(async () => runOf({ report_revision: state.requested, published_report_revision: state.published }))
    vi.spyOn(api, 'report').mockImplementation(async () => reportOf(state.published) as never)
    const approve = vi.spyOn(api, 'approve').mockResolvedValue({} as never)
    mountDetail()
    expect(await screen.findByText('보고서 1판')).toBeInTheDocument()
    expect(screen.getByTestId('report-regenerating')).toHaveTextContent('보고서 다시 만드는 중 (1판 표시)')
    expect(screen.getByTestId('regenerating')).toHaveTextContent('2판')
    expect(screen.getByRole('button', { name: '승인' })).toBeDisabled()
    expect(screen.getByTestId('approval-block')).toHaveTextContent('2판으로 다시 만드는 중')
    // 발행 판이 따라잡음 → 1초 폴링으로 새 판을 불러옴
    state.published = 2
    expect(await screen.findByText('보고서 2판', {}, { timeout: 4000 })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '승인' })).toBeEnabled())
    expect(screen.queryByTestId('report-regenerating')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    await userEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '그래도 승인' }))
    expect(approve).toHaveBeenCalledWith('run-1', expect.objectContaining({ decision: 'approved', report_revision: 2 }))
  })

  it('추가 조사는 focus 를 담아 화면의 판과 멱등 키로 보낸다', async () => {
    vi.spyOn(api, 'run').mockResolvedValue(runOf({}))
    vi.spyOn(api, 'report').mockResolvedValue(reportOf(1) as never)
    const approve = vi.spyOn(api, 'approve').mockResolvedValue({} as never)
    mountDetail()
    await screen.findByText('보고서 1판')
    await userEvent.click(screen.getByRole('button', { name: '추가 조사 (남은 2회)' }))
    await userEvent.click(screen.getByRole('checkbox', { name: /겨울철 활동 위축/ }))
    await userEvent.click(screen.getByRole('button', { name: '추가 조사 보내기' }))
    expect(approve).toHaveBeenCalledWith('run-1', {
      decision: 'more',
      report_revision: 1,
      approver: APPROVER_NAME,
      idempotency_key: 'run-1:more:1',
      memo: null,
      focus: { candidate_ids: ['C-20250926-501'], tools: ['calc_winter_contraction'] },
    })
  })

  it('실행이 있으면 실행에 묶인 자원(GET /runs/{id}/resources)을 쓰고 전체 자원 조회는 부르지 않는다', async () => {
    vi.spyOn(api, 'run').mockResolvedValue(runOf({}))
    vi.spyOn(api, 'report').mockResolvedValue(reportOf(1) as never)
    const { resources, runResources } = mountDetail()
    expect(await screen.findByText('외로움안녕120')).toBeInTheDocument()
    expect(runResources).toHaveBeenCalledWith('run-1', expect.anything())
    expect(resources).not.toHaveBeenCalled()
    expect(screen.getByTestId('resource-scope')).toHaveTextContent('이 조사에 묶인 목록')
  })

  it('실행 자원이 후보마다 겹쳐 오면 자원 ID 하나로 보이고 이 후보 줄을 앞에 둔다', () => {
    const base = RUN_RESOURCES.items[0]
    const items = [
      { ...base, candidate_id: 'C-2' },
      { ...base, candidate_id: 'C-1', name: 'mine' },
      { ...base, resource_id: 'R2', candidate_id: 'C-2' },
    ]
    expect(uniqueResources(items, 'C-1').map((r) => [r.resource_id, r.name])).toEqual([
      ['R0007', 'mine'],
      ['R2', base.name],
    ])
  })

  it('추가 조사로 빠진 근거 ID 는 검토에 보내지 않는다 (EVIDENCE_NOT_IN_RUN 방지)', async () => {
    vi.spyOn(api, 'run').mockResolvedValue(runOf({}))
    vi.spyOn(api, 'report').mockResolvedValue(reportOf(1) as never)
    const review = vi.spyOn(api, 'review').mockResolvedValue({ run_id: 'run-1', review_id: 'rev-2', report_revision: 1, report_stale: false, changed: false })
    const keep = REPORT_501.evidence_card.evidence[0].evidence_id
    mountDetail({ checked: [keep, 'EV-GONE'] })
    await screen.findByText('보고서 1판')
    await waitFor(() => expect(screen.getByRole('checkbox', { name: `${evidenceLine(REPORT_501.evidence_card.evidence[0]).head} 확인함` })).toBeChecked())
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    expect(review).toHaveBeenCalledWith('run-1', expect.objectContaining({ checked_evidence_ids: [keep] }))
  })

  it('실행이 없으면 전체 자원 조회를 쓴다', async () => {
    const { resources, runResources } = mountDetail({ runs: [] })
    await screen.findByRole('button', { name: '조사 시작' })
    await waitFor(() => expect(resources).toHaveBeenCalled())
    expect(runResources).not.toHaveBeenCalled()
  })

  it('추가 조사 뒤 보고서에 "1회차 대비 달라진 점"을 보이고 사실 목록에서는 뺀다', async () => {
    const lines = ['1회차 대비 달라진 점: 추가 도구 calc_holiday_gap 실행 (llm)', '1회차 대비 새 근거 1건: 연휴 전후 결제']
    vi.spyOn(api, 'run').mockResolvedValue(runOf({ report_revision: 2, published_report_revision: 2, extra_rounds: 1 }))
    vi.spyOn(api, 'report').mockResolvedValue(reportOf(2, { round_changes: lines, fact_sentences: [...REPORT_501.fact_sentences, ...lines] }) as never)
    mountDetail()
    const box = await screen.findByTestId('round-changes')
    expect(box).toHaveTextContent('1회차 대비 달라진 점')
    expect(box).toHaveTextContent('추가 도구 연휴 전후 차이 계산 실행')
    expect(screen.getAllByText(/새 근거 1건/).filter((el) => !el.closest('[data-testid="print-report"]'))).toHaveLength(1)
    expect(screen.getByRole('button', { name: '추가 조사 (남은 1회)' })).toBeEnabled()
    expect(roundChangesView([]).title).toBe('지난 회차 대비 달라진 점')
  })

  it('그래프 예외로 멈춘 실행은 AGENT_ERROR 를 쉬운 문장으로', async () => {
    vi.spyOn(api, 'run').mockResolvedValue(runOf({ status: 'failed', errors: [{ error_code: 'AGENT_ERROR' }] }))
    vi.spyOn(api, 'report').mockRejectedValue(new ApiError(409, 'RUN_NOT_READY', 'x'))
    vi.spyOn(api, 'runResources').mockRejectedValue(new ApiError(409, 'RUN_NOT_READY', 'x'))
    mountDetail()
    const box = await screen.findByTestId('run-failed')
    expect(box).toHaveTextContent('조사 중 문제가 생겨 멈췄습니다')
    expect(box).toHaveTextContent('오류 코드 AGENT_ERROR')
  })

  it('이어 묻기 답의 근거(번호·이름)를 누르면 근거 표 그 행을 표시한다', async () => {
    vi.spyOn(api, 'run').mockResolvedValue(runOf({}))
    vi.spyOn(api, 'report').mockResolvedValue(reportOf(1) as never)
    const eid = REPORT_501.evidence_card.evidence[0].evidence_id
    vi.spyOn(api, 'followup').mockResolvedValue({ ...FOLLOWUP_ANSWER, run_id: 'run-1', answer: `사실 [${eid}].`, cited_evidence_ids: [eid] })
    mountDetail()
    await screen.findByText('보고서 1판')
    await userEvent.type(screen.getByRole('textbox', { name: '이어 묻기 질문' }), '왜 Level 2야?')
    await userEvent.click(screen.getByRole('button', { name: '묻기' }))
    const cited = await screen.findByTestId('followup-cited')
    const btn = within(cited).getByRole('button', { name: /^근거 1 · / }) // 근거 ID 대신 근거 표 번호·이름, ID 는 title
    expect(btn).toHaveAttribute('title', eid)
    await userEvent.click(btn)
    expect(document.getElementById(eid)).toHaveClass('bg-brand-50')
  })
})

describe('이어 묻기 패널', () => {
  it('"새 조사를 하지 않고" 안내, 답과 인용 근거 ID', async () => {
    const followup = vi.spyOn(api, 'followup').mockResolvedValue(FOLLOWUP_ANSWER)
    const onCite = vi.fn()
    render(<FollowupPanel runId="run-20261013-100000-3fa9c2d1" onCite={onCite} />)
    expect(screen.getByTestId('followup-panel')).toHaveTextContent('새 조사를 하지 않고 이 보고서의 근거만으로 답합니다')
    await userEvent.type(screen.getByRole('textbox', { name: '이어 묻기 질문' }), '남면은 왜 Level 3야?')
    await userEvent.click(screen.getByRole('button', { name: '묻기' }))
    expect(followup).toHaveBeenCalledWith('run-20261013-100000-3fa9c2d1', { question: '남면은 왜 Level 3야?' })
    const ans = await screen.findByTestId('followup-answer')
    expect(ans).toHaveTextContent('1인세대 비율 0.47')
    expect(ans).toHaveTextContent('개인 판정 아님') // 줄마다 나눠 보임
    await userEvent.click(within(screen.getByTestId('followup-cited')).getByRole('button', { name: 'EV-20250926-STR-32010350' }))
    expect(onCite).toHaveBeenCalledWith('EV-20250926-STR-32010350')
    expect(screen.getByRole('textbox', { name: '이어 묻기 질문' })).toHaveValue('')
  })

  it('개인 지목·범위 밖 보류는 쉬운 문장, 코드는 작게', async () => {
    const res: FollowupResponse = {
      ...FOLLOWUP_ANSWER,
      run_id: 'run-x',
      followup_id: 'fup-2',
      status: 'abstained',
      answer: null,
      cited_evidence_ids: [],
      abstain_reason: 'OUT_OF_SCOPE_INDIVIDUAL: 개인을 지목하는 질문',
      needed_data: ['동·집단 단위로 다시 묻기'],
    }
    vi.spyOn(api, 'followup').mockResolvedValue(res)
    render(<FollowupPanel runId="run-x" onCite={vi.fn()} />)
    await userEvent.type(screen.getByRole('textbox', { name: '이어 묻기 질문' }), '김OO 할머니 괜찮아?')
    await userEvent.click(screen.getByRole('button', { name: '묻기' }))
    const item = await screen.findByTestId('followup-item')
    expect(item).toHaveAttribute('data-status', 'abstained')
    expect(within(item).getByTestId('followup-abstain')).toHaveTextContent('특정 개인을 가리키는 질문에는 답하지 않습니다.')
    expect(item).toHaveTextContent('필요한 자료: 동·집단 단위로 다시 묻기')
    expect(within(item).getByText('OUT_OF_SCOPE_INDIVIDUAL')).toHaveClass('text-xs')
  })

  it('실행 중이면 RUN_NOT_READY 를 쉬운 문장으로', async () => {
    vi.spyOn(api, 'followup').mockRejectedValue(new ApiError(409, 'RUN_NOT_READY', '보고서가 나온 실행에만 이어 물을 수 있습니다.'))
    render(<FollowupPanel runId="run-x" onCite={vi.fn()} />)
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: '이 후보는 왜 이 등급이야?' }))
    })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('조사가 아직 끝나지 않았습니다')
    expect(alert).toHaveTextContent('오류 코드 RUN_NOT_READY')
  })
})
