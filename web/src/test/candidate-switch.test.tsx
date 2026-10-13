// 후보 전환 회귀 시험:
// 후보·실행을 바꾸면 이전 실행의 자원 판정·메모·후속 확인일·확인한 근거가 새 검토로 넘어가면 안 된다.
// 같은 실행에서 보고서 판만 바뀌면 고치던 값은 남고, 다시 들어오면 그 실행에 실제 저장된 검토가 그대로 보인다.
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type RunHistoryResponse } from '../api'
import CandidateDetail from '../pages/CandidateDetail'
import { REPORT_501, RUN_RESOURCES } from './fixtures'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '춘천', as_of: '2025-09-26', query: '' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

const EV = REPORT_501.evidence_card.evidence[0]
const base: any = {
  candidate_type: 'scheduled_access_review', city: '춘천', adm_cd: '32010350', check_first_adm: [],
  analysis_cohort: '60대·70대·80대+', service_eligibility_group: '65세 이상', trigger: 'R1', input_status: '충족',
  missing_inputs: [], candidate_level: 'Level 3', level_reason: 'audit', evidence_ids: [], is_shared_context: false,
  selected_for_review: true,
}
const cands = ['A', 'B'].map((x) => ({ ...base, candidate_id: 'C-' + x }))
const emptyHistory = (rid: string): RunHistoryResponse => ({ run_id: rid, reviews: [], approvals: [], report_revisions: [], finalized: [] })

type Opts = {
  kind?: 'different' | 'shared'
  histories?: Record<string, RunHistoryResponse>
  status?: Record<string, string>
  runRev?: (rid: string) => { report_revision: number; published_report_revision: number }
}

function Nav() {
  const go = useNavigate()
  return (
    <>
      <button onClick={() => go('/candidates/C-A')}>to A</button>
      <button onClick={() => go('/candidates/C-B')}>to B</button>
    </>
  )
}

function setup({ kind = 'different', histories = {}, status = {}, runRev }: Opts = {}) {
  const runOf: Record<string, string> = { 'C-A': 'run-A', 'C-B': 'run-B' }
  vi.spyOn(api, 'candidates').mockResolvedValue({ items: cands } as any)
  vi.spyOn(api, 'runs').mockImplementation(async (cid) => ({ items: [{ run_id: runOf[cid ?? ''] }] }) as any)
  vi.spyOn(api, 'run').mockImplementation(async (rid) => ({
    run_id: rid, snapshot_id: 's', status: status[rid] ?? 'awaiting_approval',
    report_revision: 1, published_report_revision: 1, report_stale: false,
    ...(runRev ? runRev(rid) : {}),
    candidates: [rid.startsWith('run-A') ? cands[0] : cands[1]], path: [], guard: [], abstain_reason: null, extra_rounds: 0, errors: [],
  }) as any)
  vi.spyOn(api, 'evidence').mockImplementation(async (rid) => ({
    evidence_card: { ...REPORT_501.evidence_card, run_id: rid, title: rid, evidence: [{ ...EV, evidence_id: 'EV-' + rid }], limitations: [], time_label: null },
  }) as any)
  vi.spyOn(api, 'report').mockImplementation(async (rid) => {
    const rev = runRev ? runRev(rid).published_report_revision : 1
    return { run_id: rid, snapshot_id: 's', report_stale: false, report: { report_revision: rev, fact_sentences: [], narrative: '' } } as any
  })
  vi.spyOn(api, 'runResources').mockImplementation(async (rid) => {
    const x = rid.startsWith('run-A') ? 'A' : 'B'
    const resource_id = kind === 'shared' ? 'R-A' : 'R-' + x
    return {
      run_id: rid, snapshot_id: 's',
      items: [{ ...RUN_RESOURCES.items[0], resource_id, name: `auditresource ${x} ${rid}`, candidate_id: 'C-' + x, candidate_ids: ['C-' + x] }],
    } as any
  })
  vi.spyOn(api, 'history').mockImplementation(async (rid) => histories[rid] ?? emptyHistory(rid))
  const review = vi.spyOn(api, 'review').mockResolvedValue({ changed: false, report_stale: false, report_revision: 1 } as any)
  const createRun = vi.spyOn(api, 'createRun').mockResolvedValue({ run_id: 'run-A2' } as any)
  render(
    <MemoryRouter initialEntries={['/candidates/C-A']}>
      <Nav />
      <Routes>
        <Route path="/candidates/:id" element={<CandidateDetail />} />
      </Routes>
    </MemoryRouter>,
  )
  return { review, createRun }
}

const radio = (name: string) => screen.getByRole('radio', { name })
const memoBox = () => screen.getByLabelText('담당자 메모') as HTMLTextAreaElement
const followBox = () => screen.getByLabelText('후속 확인일') as HTMLInputElement

/** A에서 판정·메모·후속 확인일·근거 확인을 고친 뒤 B로 이동 */
async function editAThenSwitch() {
  expect((await screen.findAllByText('auditresource A run-A'))[0]).toBeInTheDocument()
  await userEvent.click(radio('적합'))
  await userEvent.type(memoBox(), 'A 메모')
  await userEvent.type(followBox(), '2025-10-01')
  await userEvent.click(screen.getByLabelText('EV-run-A 확인함'))
  await userEvent.click(screen.getByRole('button', { name: 'to B' }))
  expect((await screen.findAllByText('auditresource B run-B'))[0]).toBeInTheDocument()
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('후보 전환 때 검토 편집값 분리', () => {
  it.each(['different', 'shared'] as const)('다른 실행으로 바꾸면 이전 후보의 판정·메모·날짜·근거 확인이 넘어가지 않는다 (%s 자원)', async (kind) => {
    const { review } = setup({ kind })
    await editAThenSwitch()
    // 공유 자원이어도 B에서 판정하지 않았으면 체크되어 있지 않음
    expect(radio('적합')).toHaveAttribute('aria-checked', 'false')
    expect(memoBox().value).toBe('')
    expect(followBox().value).toBe('')
    expect(screen.getByLabelText('EV-run-B 확인함')).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(review).toHaveBeenCalled())
    expect(review).toHaveBeenLastCalledWith(
      'run-B',
      expect.objectContaining({ resource_judgements: [], memo: null, follow_up_on: null, checked_evidence_ids: [] }),
    )
  })

  it('B에서 판정하면 B 실행의 자원과 B 근거만 보낸다', async () => {
    const { review } = setup({ kind: 'different' })
    await editAThenSwitch()
    await userEvent.click(radio('보류'))
    await userEvent.click(screen.getByLabelText('EV-run-B 확인함'))
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(review).toHaveBeenCalled())
    expect(review).toHaveBeenLastCalledWith(
      'run-B',
      expect.objectContaining({
        resource_judgements: [expect.objectContaining({ resource_id: 'R-B', judgement: '보류' })],
        checked_evidence_ids: ['EV-run-B'],
      }),
    )
  })

  it('다시 들어오면 각 실행에 실제 저장된 검토가 그대로 보인다', async () => {
    const saved = (rid: string, rsc: string, j: '적합' | '부적합', memo: string, day: string): RunHistoryResponse => ({
      ...emptyHistory(rid),
      reviews: [
        // 앞 검토의 판정은 마지막 검토에서 빠졌으므로 되살리면 안 됨
        { review_id: 'rv0', reviewer: 'w', checked_evidence_ids: [], resource_judgements: [{ resource_id: 'R-old', judgement: '보류' }], memo: 'old', follow_up_on: null, report_revision_before: 1, report_revision_after: 1, created_at: '' },
        { review_id: 'rv1', reviewer: 'w', checked_evidence_ids: ['EV-' + rid], resource_judgements: [{ resource_id: rsc, judgement: j }], memo, follow_up_on: day, report_revision_before: 1, report_revision_after: 1, created_at: '' },
      ],
    })
    const { review } = setup({
      kind: 'shared',
      histories: { 'run-A': saved('run-A', 'R-A', '적합', 'A 저장', '2025-10-01'), 'run-B': saved('run-B', 'R-A', '부적합', 'B 저장', '2025-10-09') },
    })
    expect((await screen.findAllByText('auditresource A run-A'))[0]).toBeInTheDocument()
    await waitFor(() => expect(memoBox().value).toBe('A 저장'))
    // A에서 저장하지 않고 고친 값은 B로도, 다시 들어온 A로도 넘어가지 않음
    await userEvent.click(radio('보류'))
    await userEvent.click(screen.getByRole('button', { name: 'to B' }))
    expect((await screen.findAllByText('auditresource B run-B'))[0]).toBeInTheDocument()
    await waitFor(() => expect(memoBox().value).toBe('B 저장'))
    expect(radio('부적합')).toHaveAttribute('aria-checked', 'true')
    expect(followBox().value).toBe('2025-10-09')
    expect(screen.getByLabelText('EV-run-B 확인함')).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'to A' }))
    expect((await screen.findAllByText('auditresource A run-A'))[0]).toBeInTheDocument()
    await waitFor(() => expect(memoBox().value).toBe('A 저장'))
    expect(radio('적합')).toHaveAttribute('aria-checked', 'true')
    expect(followBox().value).toBe('2025-10-01')
    expect(screen.getByLabelText('EV-run-A 확인함')).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(review).toHaveBeenCalled())
    expect(review).toHaveBeenLastCalledWith(
      'run-A',
      expect.objectContaining({
        resource_judgements: [expect.objectContaining({ resource_id: 'R-A', judgement: '적합' })],
        memo: 'A 저장',
        follow_up_on: '2025-10-01',
        checked_evidence_ids: ['EV-run-A'],
      }),
    )
  })

  it('같은 후보의 새 실행에는 이전 실행의 저장 검토가 넘어가지 않는다', async () => {
    const { review, createRun } = setup({
      kind: 'shared',
      status: { 'run-A': 'held' },
      histories: {
        'run-A': {
          ...emptyHistory('run-A'),
          reviews: [{ review_id: 'rv1', reviewer: 'w', checked_evidence_ids: ['EV-run-A'], resource_judgements: [{ resource_id: 'R-A', judgement: '적합' }], memo: 'A 저장', follow_up_on: '2025-10-01', report_revision_before: 1, report_revision_after: 1, created_at: '' }],
        },
      },
    })
    expect((await screen.findAllByText('auditresource A run-A'))[0]).toBeInTheDocument()
    await waitFor(() => expect(memoBox().value).toBe('A 저장'))
    await userEvent.click(screen.getByRole('button', { name: '새 실행으로 다시 조사' }))
    await waitFor(() => expect(createRun).toHaveBeenCalled())
    expect((await screen.findAllByText('auditresource A run-A2'))[0]).toBeInTheDocument()
    expect(radio('적합')).toHaveAttribute('aria-checked', 'false')
    expect(memoBox().value).toBe('')
    expect(followBox().value).toBe('')
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(review).toHaveBeenCalled())
    expect(review).toHaveBeenLastCalledWith('run-A2', expect.objectContaining({ resource_judgements: [], memo: null, follow_up_on: null, checked_evidence_ids: [] }))
  })

  it('같은 실행에서 보고서 판이 바뀌어도 고치던 값은 남는다', async () => {
    // 첫 조회는 2판을 만드는 중(발행 1판) → 1초 뒤 다시 조회하면 2판 발행
    let polls = 0
    const { review } = setup({
      kind: 'different',
      runRev: (rid) => {
        if (rid !== 'run-A') return { report_revision: 1, published_report_revision: 1 }
        return polls >= 2 ? { report_revision: 2, published_report_revision: 2 } : { report_revision: 2, published_report_revision: 1 }
      },
    })
    const run = api.run as unknown as ReturnType<typeof vi.fn>
    run.mockImplementation(async (rid: string) => {
      polls += 1
      const pub = polls >= 2 ? 2 : 1
      return {
        run_id: rid, snapshot_id: 's', status: 'awaiting_approval', report_revision: 2, published_report_revision: pub, report_stale: false,
        candidates: [cands[0]], path: [], guard: [], abstain_reason: null, extra_rounds: 0, errors: [],
      } as any
    })
    expect((await screen.findAllByText('auditresource A run-A'))[0]).toBeInTheDocument()
    await userEvent.click(radio('적합'))
    await userEvent.type(memoBox(), '고치는 중')
    const history = api.history as unknown as ReturnType<typeof vi.fn>
    const before = history.mock.calls.length
    // 판이 바뀌면 이력·보고서를 다시 부름
    await waitFor(() => expect(history.mock.calls.length).toBeGreaterThan(before), { timeout: 3000 })
    expect(await screen.findByText('보고서 2판')).toBeInTheDocument()
    expect(radio('적합')).toHaveAttribute('aria-checked', 'true')
    expect(memoBox().value).toBe('고치는 중')
    await userEvent.click(screen.getByRole('button', { name: '검토 저장' }))
    await waitFor(() => expect(review).toHaveBeenCalled())
    expect(review).toHaveBeenLastCalledWith(
      'run-A',
      expect.objectContaining({ resource_judgements: [expect.objectContaining({ resource_id: 'R-A', judgement: '적합' })], memo: '고치는 중' }),
    )
  })
})
