// 보고서 판 경합 회귀 시험:
// 화면에 띄운 보고서 판과 승인 요청의 판이 언제나 같아야 하고, 늦게 온 이전 응답이 최신을 덮으면 안 된다.
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import CandidateDetail from '../pages/CandidateDetail'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '강남', as_of: '2025-09-26', query: '' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

const candidate: any = {
  candidate_id: 'C-audit', candidate_type: 'scheduled_access_review', city: '강남', adm_cd: '11230510',
  check_first_adm: [], analysis_cohort: '40대·50대', service_eligibility_group: null, trigger: 'R1',
  input_status: '충족', missing_inputs: [], counter_result: null, counter_reason: null,
  candidate_level: 'Level 1', level_reason: 'audit', evidence_ids: [], is_shared_context: false,
}
const card: any = {
  run_id: 'run-audit', title: 'audit', evidence_revision: 1, as_of: '2025-09-26',
  analysis_mode: 'simulated_replay', facts: [], evidence: [], limitations: [], time_label: null,
}

function reportOf(rev: number): any {
  return {
    run_id: 'run-audit', snapshot_id: 'snap-1', report_stale: false,
    report: { report_revision: rev, fact_sentences: [`report ${rev}`], narrative: '' },
  }
}

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

function setup() {
  const state = { revision: 1 }
  vi.spyOn(api, 'candidates').mockResolvedValue({ items: [candidate] } as any)
  vi.spyOn(api, 'runs').mockResolvedValue({ items: [{ run_id: 'run-audit' }] } as any)
  vi.spyOn(api, 'run').mockImplementation(async () => ({
    run_id: 'run-audit', snapshot_id: 'snap-1', status: 'awaiting_approval', report_revision: state.revision,
    report_stale: false, candidates: [candidate], path: [], guard: [], abstain_reason: null,
  } as any))
  vi.spyOn(api, 'resources').mockResolvedValue({ items: [] } as any)
  vi.spyOn(api, 'runResources').mockResolvedValue({ run_id: 'run-audit', snapshot_id: 'snap-1', items: [] } as any)
  vi.spyOn(api, 'evidence').mockResolvedValue({ evidence_card: card } as any)
  vi.spyOn(api, 'history').mockResolvedValue({ reviews: [], approvals: [], report_revisions: [] } as any)
  const review = vi.spyOn(api, 'review').mockImplementation(async () => {
    state.revision = 2
    return { report_revision: 2, report_stale: false, changed: true } as any
  })
  const approve = vi.spyOn(api, 'approve').mockResolvedValue({} as any)
  render(
    <MemoryRouter initialEntries={['/candidates/C-audit']}>
      <Routes>
        <Route path="/candidates/:id" element={<CandidateDetail />} />
      </Routes>
    </MemoryRouter>,
  )
  return { state, review, approve }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('화면에 보지 않은 판 승인 차단', () => {
  it('새 판 보고서 응답이 늦으면 1판을 보이는 동안 승인할 수 없다', async () => {
    let reportRequests = 0
    vi.spyOn(api, 'report').mockImplementation(() => {
      reportRequests += 1
      return reportRequests === 1 ? Promise.resolve(reportOf(1)) : new Promise(() => {})
    })
    const { approve, review } = setup()
    expect(await screen.findByText('보고서 1판')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '승인' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    expect(review).toHaveBeenCalledWith('run-audit', expect.objectContaining({ report_revision: 1 }))
    await waitFor(() => expect(reportRequests).toBe(2))
    expect(screen.getByText('보고서 1판')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '승인' })).toBeDisabled())
    expect(screen.getByRole('button', { name: '보류' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^추가 조사/ })).toBeDisabled()
    expect(screen.getByTestId('approval-block')).toHaveTextContent('화면의 보고서(1판)가 지금 판(2판)과 다릅니다')
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    expect(approve).not.toHaveBeenCalled()
  })

  it('새 판이 화면에 뜬 뒤에는 그 판으로 승인한다', async () => {
    const second = deferred<any>()
    let reportRequests = 0
    vi.spyOn(api, 'report').mockImplementation(() => {
      reportRequests += 1
      return reportRequests === 1 ? Promise.resolve(reportOf(1)) : second.promise
    })
    const { approve } = setup()
    expect(await screen.findByText('보고서 1판')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(reportRequests).toBe(2))
    await act(async () => second.resolve(reportOf(2)))
    expect(await screen.findByText('보고서 2판')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: '승인' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    expect(approve).toHaveBeenCalledWith('run-audit', expect.objectContaining({ report_revision: 2 }))
  })

  it('늦게 도착한 이전 판 응답이 새 판을 덮지 않는다', async () => {
    const first = deferred<any>()
    const second = deferred<any>()
    let reportRequests = 0
    vi.spyOn(api, 'report').mockImplementation(() => {
      reportRequests += 1
      return reportRequests === 1 ? first.promise : second.promise
    })
    const { state, approve } = setup()
    await waitFor(() => expect(reportRequests).toBe(1))
    // 1판 응답이 오기 전에 서버가 2판으로 바뀌고 다시 조회됨
    state.revision = 2
    await userEvent.click(await screen.findByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(reportRequests).toBe(2))
    await act(async () => second.resolve(reportOf(2)))
    expect(await screen.findByText('보고서 2판')).toBeInTheDocument()
    await act(async () => first.resolve(reportOf(1))) // 순서 역전
    expect(screen.getByText('보고서 2판')).toBeInTheDocument()
    expect(screen.queryByText('보고서 1판')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    expect(approve).toHaveBeenCalledWith('run-audit', expect.objectContaining({ report_revision: 2 }))
  })
})
