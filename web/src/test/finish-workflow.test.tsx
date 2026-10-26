import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { createMemoryRouter, Link, MemoryRouter, Route, RouterProvider, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Candidate, type QAResponse } from '../api'
import { UnsavedReviewGuard } from '../components/UnsavedReviewGuard'
import { ResourcePanel } from '../components/ResourcePanel'
import CandidateDetail from '../pages/CandidateDetail'
import QA from '../pages/QA'
import Runs from '../pages/Runs'
import { REPORT_501, RUN_RESOURCES } from './fixtures'

vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))
afterEach(() => vi.restoreAllMocks())

const cand: Candidate = {
  candidate_id: 'C-1', candidate_type: 'scheduled_access_review', city: '강남', adm_cd: '11230520',
  check_first_adm: [], analysis_cohort: '60대+', service_eligibility_group: '고령층', trigger: 'R1',
  input_status: '충족', missing_inputs: [], counter_result: '해당 없음', counter_reason: null,
  candidate_level: 'Level 2', level_reason: '지정 조사에 남은 선정 이유', queue_rank: 1,
  selected_for_review: true, selection_budget: 2, evidence_ids: [], is_shared_context: false,
}
function run(runId: string, changes = {}) {
  return { run_id: runId, mode: 'qa', route: 'investigate', as_of: '2025-09-26', analysis_mode: 'simulated_replay',
    snapshot_id: 's', status: 'awaiting_approval', candidates: [cand], path: [], guard: [], contract_check: null,
    report_revision: 1, published_report_revision: 1, report_stale: false, errors: [], abstain_reason: null,
    extra_rounds: 0, ...changes } as any
}
function candidateApi() {
  vi.spyOn(api, 'candidates').mockResolvedValue({ items: [{ ...cand, level_reason: '현재 감시의 다른 이유' }], monitoring_run_id: 'monitor' } as any)
  vi.spyOn(api, 'runs').mockResolvedValue({ items: [] })
  vi.spyOn(api, 'run').mockImplementation(async (id) => run(id))
  vi.spyOn(api, 'evidence').mockImplementation(async (id) => ({ run_id: id, snapshot_id: 's', evidence_card: REPORT_501.evidence_card }))
  vi.spyOn(api, 'report').mockImplementation(async (id) => ({ run_id: id, snapshot_id: 's', report_stale: false, report: REPORT_501 }))
  vi.spyOn(api, 'history').mockImplementation(async (id) => ({ run_id: id, reviews: [], approvals: [], report_revisions: [], finalized: [] }))
  vi.spyOn(api, 'runResources').mockImplementation(async (id) => ({ ...RUN_RESOURCES, run_id: id }))
  vi.spyOn(api, 'resources').mockResolvedValue({ items: [] } as any)
}
function Location() { return <output data-testid="url">{useLocation().search}</output> }
function mountCandidate(query = 'run_id=old') {
  return render(<MemoryRouter initialEntries={[`/candidates/C-1?city=강남&as_of=2025-09-26&${query}`]}>
    <Location /><Routes><Route path="/candidates/:id" element={<CandidateDetail />} /></Routes>
  </MemoryRouter>)
}

describe('조사 기록 이어가기', () => {
  it.each(['simulated_replay', 'strict_as_of'])('최신 실행 대신 지정한 %s 실행과 그 후보를 직접 연다', async (analysis_mode) => {
    candidateApi()
    vi.mocked(api.run).mockResolvedValue(run('old', { analysis_mode }))
    mountCandidate()
    expect((await screen.findAllByText(/지정 조사에 남은 선정 이유/)).length).toBeGreaterThan(0)
    expect(api.run).toHaveBeenCalledWith('old', expect.anything())
    expect(api.runs).not.toHaveBeenCalled()
    expect(api.candidates).not.toHaveBeenCalled()
    expect(screen.queryByText('현재 감시의 다른 이유')).not.toBeInTheDocument()
  })

  it('기준일이 다른 실행을 검토하지 못하게 막는다', async () => {
    candidateApi()
    vi.mocked(api.run).mockResolvedValue(run('wrong', { as_of: '2025-12-20' }))
    mountCandidate('run_id=wrong')
    expect(await screen.findByTestId('runs-error')).toHaveTextContent('선택한 조사와 후보·지역·기준일이 다릅니다')
    expect(api.report).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '조사 시작' })).not.toBeInTheDocument()
  })

  it('조사 생성 성공 후 URL도 새 실행으로 고정한다', async () => {
    candidateApi()
    vi.spyOn(api, 'createRun').mockResolvedValue({ run_id: 'new-run', status: 'queued' } as any)
    mountCandidate('')
    fireEvent.click(await screen.findByRole('button', { name: '조사 시작' }))
    await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('run_id=new-run'))
    expect(api.run).toHaveBeenCalledWith('new-run', expect.anything())
  })

  it('Q&A의 여러 후보를 정확한 실행 검토 링크로 연결한다', async () => {
    candidateApi()
    vi.mocked(api.runs).mockResolvedValue({ items: [{ run_id: 'qa-run', candidate_id: null, question: '어디부터 볼까?',
      mode: 'qa', status: 'awaiting_approval', as_of: '2025-09-26', city: '강남', analysis_mode: 'simulated_replay' }] } as any)
    vi.mocked(api.run).mockResolvedValue(run('qa-run', { candidates: [cand, { ...cand, candidate_id: 'C-2' }] }))
    render(<MemoryRouter initialEntries={['/runs?run_id=qa-run&city=춘천&as_of=2025-12-20']}><Runs /></MemoryRouter>)
    const links = await screen.findAllByRole('link', { name: /검토 열기/ })
    expect(links).toHaveLength(2)
    const target = new URL(links[0].getAttribute('href')!, 'http://test')
    expect(target.pathname).toBe('/candidates/C-1')
    expect(target.searchParams.get('run_id')).toBe('qa-run')
    expect(target.searchParams.get('city')).toBe('강남')
    expect(target.searchParams.get('as_of')).toBe('2025-09-26')
  })

  it('최근 100건 밖의 지정 실행도 직접 열 수 있다', async () => {
    candidateApi()
    render(<MemoryRouter initialEntries={['/runs?run_id=older']}><Runs /></MemoryRouter>)
    expect(await screen.findByRole('link', { name: /검토 열기/ })).toHaveAttribute('href', expect.stringContaining('run_id=older'))
  })
})

function QAContext() {
  const navigate = useNavigate()
  return <><button onClick={() => navigate('/qa?city=춘천&as_of=2025-12-20')}>다른 범위</button><QA /></>
}
const answer: QAResponse = { run_id: 'qa-old', status: 'abstained', evidence_card: null, answer: null,
  guard: [], abstain_reason: 'TEST: 이전 범위 응답', needed_data: [] }
describe('질의응답 범위', () => {
  it('필터가 바뀐 뒤 도착한 답을 버리고 새 질문을 허용한다', async () => {
    let resolve!: (r: QAResponse) => void
    const qa = vi.spyOn(api, 'qa').mockImplementation(() => new Promise((r) => { resolve = r }))
    render(<MemoryRouter initialEntries={['/qa?city=강남&as_of=2025-09-26']}><QAContext /></MemoryRouter>)
    fireEvent.change(screen.getByRole('textbox', { name: '질문' }), { target: { value: '자료를 보여줘' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    fireEvent.click(screen.getByRole('button', { name: '다른 범위' }))
    expect(qa.mock.calls[0][1]!.signal!.aborted).toBe(true)
    await act(async () => resolve(answer))
    expect(screen.queryByText('이전 범위 응답')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '묻기' })).toBeEnabled()
  })

  it('표시된 답을 범위 변경 시 지우고 질문 당시 도시로 기록을 연결한다', async () => {
    vi.spyOn(api, 'qa').mockResolvedValue(answer)
    render(<MemoryRouter initialEntries={['/qa?city=춘천&as_of=2025-09-26']}><QAContext /></MemoryRouter>)
    fireEvent.change(screen.getByRole('textbox', { name: '질문' }), { target: { value: '강남 자료' } })
    fireEvent.click(screen.getByRole('button', { name: '묻기' }))
    const link = await screen.findByRole('link', { name: /이 질문의 조사/ })
    const url = new URL(link.getAttribute('href')!, 'http://test')
    expect(url.searchParams.get('city')).toBe('강남')
    expect(url.searchParams.get('run_id')).toBe('qa-old')
    fireEvent.click(screen.getByRole('button', { name: '다른 범위' }))
    expect(screen.queryByRole('link', { name: /이 질문의 조사/ })).not.toBeInTheDocument()
  })
})

it('미저장 검토에서 이동을 취소하면 입력 화면에 남고 확인하면 이동한다', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
  const router = createMemoryRouter([{ path: '/', element: <><UnsavedReviewGuard dirty /><Link to="/next">이동</Link></> },
    { path: '/next', element: <p>다음 화면</p> }])
  render(<StrictMode><RouterProvider router={router} /></StrictMode>)
  fireEvent.click(screen.getByRole('link', { name: '이동' }))
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
  expect(screen.queryByText('다음 화면')).not.toBeInTheDocument()
  const unload = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(unload)
  expect(unload.defaultPrevented).toBe(true)
  fireEvent.click(screen.getByRole('link', { name: '이동' }))
  expect(await screen.findByText('다음 화면')).toBeInTheDocument()
})

it('자원의 공식 안내 링크를 제공하고 스크립트 URL은 링크로 만들지 않는다', () => {
  const resource = RUN_RESOURCES.items[0]
  render(<ResourcePanel items={[{ ...resource, source_url: 'https://example.org/official' },
    { ...resource, resource_id: 'bad', source_url: 'javascript:alert(1)' }]} draft={{}} onChange={() => {}} />)
  expect(screen.getAllByRole('link', { name: /공식 안내 확인/ })).toHaveLength(1)
  expect(screen.getByRole('link', { name: /공식 안내 확인/ })).toHaveAttribute('rel', 'noopener noreferrer')
})
