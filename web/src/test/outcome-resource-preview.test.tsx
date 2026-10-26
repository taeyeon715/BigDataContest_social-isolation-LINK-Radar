// 조사 전 자원 미리 보기를 후보의 필요 유형으로 좁힘, 현장 확인 결과 입력·모아 보기
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { api, type Candidate } from '../api'
import CandidateDetail from '../pages/CandidateDetail'
import { RUN_RESOURCES } from './fixtures'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '춘천', as_of: '2025-12-20', query: '?as_of=2025-12-20&city=춘천' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never
})
afterEach(() => {
  vi.restoreAllMocks()
})

const cand: Candidate = {
  candidate_id: 'C-20251220-003', candidate_type: 'scheduled_access_review', city: '춘천', adm_cd: '51110330', check_first_adm: [],
  analysis_cohort: '60대+', service_eligibility_group: '65세 이상', trigger: 'W 동절기', input_status: '충족',
  missing_inputs: [], counter_result: '해당 없음', counter_reason: null, candidate_level: 'Level 3', level_reason: '면 지역',
  queue_rank: 1, selected_for_review: true, selection_budget: 2, evidence_ids: [], is_shared_context: false,
  cause_type: '계절 공동화형', need_types: ['안부·관계', '이동 지원', '생필품'],
}

describe('후보 상세: 필요 유형으로 좁힌 자원 미리 보기', () => {
  it('후보의 원인 유형을 보내고 "필요한 것에 맞는 n건"을 보인다', async () => {
    vi.spyOn(api, 'candidates').mockResolvedValue({ as_of: '2025-12-20', city: '춘천', snapshot_id: 's', selection_budget: 2, items: [cand], monitoring_run_id: 'mon-1' })
    vi.spyOn(api, 'runs').mockResolvedValue({ items: [] } as never)
    const resources = vi.spyOn(api, 'resources').mockResolvedValue({
      adm_cd: '51110330', need_type: null, group: null, items: [RUN_RESOURCES.items[0]], total: 14,
      cause_type: '계절 공동화형', need_types: ['안부·관계', '이동 지원', '생필품'], scope_total: 120,
    })
    render(
      <MemoryRouter initialEntries={['/candidates/C-20251220-003']}>
        <Routes>
          <Route path="/candidates/:id" element={<CandidateDetail />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('resource-preview')).toHaveTextContent('14건 중 1건')
    expect(screen.getByTestId('resource-preview-needs')).toHaveTextContent('안부·관계·이동 지원·생필품')
    expect(screen.getByTestId('resource-preview-needs')).toHaveTextContent('120건 중')
    expect(resources).toHaveBeenCalledWith(expect.objectContaining({ cause_type: '계절 공동화형', adm_cd: '51110330' }), expect.anything())
  })
})

describe('현장 확인 결과', () => {
  it('건수 관계가 틀리면 막고, 맞으면 멱등 키와 함께 저장한다', async () => {
    const { OutcomeForm, outcomeProblem } = await import('../components/OutcomeForm')
    expect(outcomeProblem({ checked_households: 5, contacted: 4, absent: 2, new_links: 0, crises_found: 0 })).toContain('연락됨 + 부재')
    expect(outcomeProblem({ checked_households: 5, contacted: 3, absent: 2, new_links: 1, crises_found: 0 })).toBeNull()
    vi.spyOn(api, 'runOutcomes').mockResolvedValue({ run_id: 'run-1', items: [], history_count: 0 })
    const rec = vi.spyOn(api, 'recordOutcome').mockResolvedValue({
      run_id: 'run-1', duplicate: false,
      outcome: { outcome_id: 'out-1', run_id: 'run-1', candidate_id: 'C-1', city: '춘천', adm_cd: null, group: null, trigger_code: 'W',
        event_id: null, recorded_by: 'w', created_at: '2026-10-14T10:00:00', checked_households: 3, contacted: 0, absent: 0, new_links: 0, crises_found: 0 },
    })
    const userEvent = (await import('@testing-library/user-event')).default
    render(<OutcomeForm runId="run-1" candidateId="C-1" />)
    const box = screen.getByLabelText('확인 가구')
    await waitFor(() => expect(box).toBeEnabled())
    await userEvent.clear(box)
    await userEvent.type(box, '3')
    await userEvent.click(screen.getByRole('button', { name: '결과 저장' }))
    expect(rec).toHaveBeenCalledWith('run-1', expect.objectContaining({ candidate_id: 'C-1', checked_households: 3, idempotency_key: expect.stringMatching(/^run-1:C-1:/) }))
    expect(await screen.findByTestId('outcome-saved')).toBeInTheDocument()
  })

  it('조회 실패 시 입력과 저장을 막고 다시 불러온 기존 값을 복원한다', async () => {
    const { OutcomeForm } = await import('../components/OutcomeForm')
    const load = vi.spyOn(api, 'runOutcomes').mockRejectedValueOnce(new Error('network')).mockResolvedValue({
      run_id: 'run-1', history_count: 1, items: [{ candidate_id: 'C-1', checked_households: 12, contacted: 10, absent: 2,
        new_links: 1, crises_found: 0, created_at: '', recorded_by: 'w' }],
    } as never)
    const save = vi.spyOn(api, 'recordOutcome')
    render(<OutcomeForm runId="run-1" candidateId="C-1" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('저장된 결과를 불러오지 못했습니다')
    expect(screen.getByLabelText('확인 가구')).toBeDisabled()
    expect(screen.getByRole('button', { name: '결과 저장' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }))
    await waitFor(() => expect(screen.getByLabelText('확인 가구')).toHaveValue(12))
    expect(screen.getByRole('button', { name: '고쳐 저장' })).toBeEnabled()
    expect(load).toHaveBeenCalledTimes(2)
    expect(save).not.toHaveBeenCalled()
  })

  it('다른 후보 조회 중에는 이전 건수를 저장하지 못하고 늦은 응답도 버린다', async () => {
    const { OutcomeForm } = await import('../components/OutcomeForm')
    let resolve!: (v: any) => void
    vi.spyOn(api, 'runOutcomes').mockImplementationOnce(() => new Promise((r) => { resolve = r }))
      .mockResolvedValue({ run_id: 'run-B', items: [], history_count: 0 })
    const { rerender } = render(<OutcomeForm runId="run-A" candidateId="C-A" />)
    expect(screen.getByRole('button', { name: '결과 저장' })).toBeDisabled()
    rerender(<OutcomeForm runId="run-B" candidateId="C-B" />)
    await waitFor(() => expect(screen.getByRole('button', { name: '결과 저장' })).toBeEnabled())
    await act(async () => resolve({ items: [{ candidate_id: 'C-A', checked_households: 99 }] }))
    expect(screen.getByLabelText('확인 가구')).toHaveValue(0)
  })

  it('모아 보기는 10 미만 칸을 숫자 없이 보인다', async () => {
    const { OutcomeSummaryCard, cellText } = await import('../components/OutcomeSummary')
    const row = { city: '춘천' as const, trigger_code: 'W', event_id: null, runs: 2, places: 3, checked_households: 24, contacted: 16,
      absent: null, new_links: null, crises_found: null, small_cells: ['absent', 'new_links', 'crises_found'], contact_rate: 0.667, link_rate: null, crisis_rate: null }
    expect(cellText(row, 'absent')).toBe('10 미만')
    vi.spyOn(api, 'outcomeSummary').mockResolvedValue({ city: '춘천', by: 'trigger', small_cell: 10, rows: [row], note: 'n' })
    render(<OutcomeSummaryCard city="춘천" />)
    const tr = await screen.findByTestId('outcome-row')
    expect(tr).toHaveTextContent('동절기(W)')
    expect(tr).toHaveTextContent('24')
    expect(tr).toHaveTextContent('10 미만')
    expect(tr).toHaveTextContent('67%')
  })
})
