// 해제 사유 칩, 후속 확인 한 번에 읽기(GET /followups), 후보 상세에서 조사 시작할 때 정기 감시 실행·도시, 자원 total.
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { api, type Candidate, type FollowUpListResponse, type OverviewResponse } from '../api'
import CandidateDetail, { uniqueResources } from '../pages/CandidateDetail'
import Candidates from '../pages/Candidates'
import Overview, { releasedOf } from '../pages/Overview'
import { RUN_RESOURCES } from './fixtures'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '강남', as_of: '2025-09-26', query: '?as_of=2025-09-26&city=강남' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

beforeAll(() => {
  // recharts ResponsiveContainer 가 쓰는 ResizeObserver (jsdom 에 없음)
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
  candidate_id: 'C-20250926-501', candidate_type: 'scheduled_access_review', city: '강남', adm_cd: '11230510', check_first_adm: [],
  analysis_cohort: '40대·50대 남성', service_eligibility_group: '40~64세 남성', trigger: 'R1 추석 연휴 D-7', input_status: '충족',
  missing_inputs: [], counter_result: '해당 없음', counter_reason: null, candidate_level: 'Level 2', level_reason: '구조 취약성 상위',
  queue_rank: 1, selected_for_review: true, selection_budget: 2, evidence_ids: [], is_shared_context: false,
}

const OV: OverviewResponse = {
  as_of: '2025-09-26', city: '강남', snapshot_id: 's', analysis_mode: 'simulated_replay', triggers: [],
  candidate_counts: { scheduled_access_review: { count: 1, low: 1, high: 2, note: '' } },
  new_candidate_ids: [], released_candidate_ids: ['C-20250919-502'], time_label: null, disclaimer: '',
  released_candidates: [
    {
      candidate_id: 'C-20250919-502', candidate_type: 'scheduled_access_review', city: '강남', adm_cd: '11230640', check_first_adm: [],
      analysis_cohort: '20대 남성', service_eligibility_group: '20~34세 남성', candidate_level: 'Level 2', trigger: 'R4 폭염',
      last_seen_as_of: '2025-09-19', release_reason: '트리거 기간이 끝남 (R4 폭염)',
    },
  ],
  compared_as_of: '2025-09-19', monitoring_run_id: 'mon-1',
}

const FU: FollowUpListResponse = {
  as_of: '2025-09-26', city: '강남', date_from: null, date_to: null,
  items: [
    { candidate_id: 'C-20250926-501', run_id: 'run-1', city: '강남', follow_up_on: '2025-09-20', memo: '다시 전화', review_id: 'rev-1',
      reviewer: 'w', run_status: 'awaiting_approval', approved: false, state: '지남' },
  ],
}

function stubLists() {
  vi.spyOn(api, 'activeSnapshot').mockRejectedValue(new Error('x'))
  vi.spyOn(api, 'capabilities').mockRejectedValue(new Error('x'))
  vi.spyOn(api, 'candidates').mockResolvedValue({ as_of: '2025-09-26', city: '강남', snapshot_id: 's', selection_budget: 2, items: [cand], monitoring_run_id: 'mon-1' })
  const followups = vi.spyOn(api, 'followups').mockResolvedValue(FU)
  const runs = vi.spyOn(api, 'runs')
  const history = vi.spyOn(api, 'history')
  return { followups, runs, history }
}

describe('현황: 해제된 후보와 사유', () => {
  it('released_candidates 의 동·집단·지난 등급 칩 옆에 해제 사유를 보인다', async () => {
    stubLists()
    vi.spyOn(api, 'overview').mockResolvedValue(OV)
    render(
      <MemoryRouter>
        <Overview />
      </MemoryRouter>,
    )
    const list = await screen.findByTestId('released-list')
    expect(within(list).getByTestId('candidate-chip')).toHaveTextContent('강남 역삼1동 · 청년 남성 · Level 2')
    expect(within(list).getByTestId('release-reason')).toHaveTextContent('트리거 기간이 끝남 (R4 폭염)')
    expect(screen.getByText('2025-09-19 목록과 비교')).toBeInTheDocument()
    expect(screen.queryByText(/트리거 기간이 끝났거나 조건이 바뀜/)).not.toBeInTheDocument()
  })

  it('옛 응답(사유 없음)이면 ID 만', () => {
    const old = { ...OV, released_candidates: undefined }
    expect(releasedOf(old)).toEqual([{ id: 'C-20250919-502', c: null, reason: null }])
  })

  it('후속 확인은 GET /followups 한 번으로 읽고 실행마다 이력을 부르지 않는다', async () => {
    const { followups, runs, history } = stubLists()
    vi.spyOn(api, 'overview').mockResolvedValue(OV)
    render(
      <MemoryRouter>
        <Overview />
      </MemoryRouter>,
    )
    const card = await screen.findByTestId('follow-card')
    await waitFor(() => expect(within(card).getByTestId('follow-up-badge')).toHaveTextContent('후속 확인 기한 지남 09-20'))
    expect(followups).toHaveBeenCalledWith({ city: '강남', as_of: '2025-09-26' }, expect.anything())
    expect(runs).not.toHaveBeenCalled()
    expect(history).not.toHaveBeenCalled()
  })
})

describe('후보 목록: 후속 확인 탭', () => {
  it('서버 state 로 "지남" 배지를 세고, 탭에 후속 확인 후보를 보인다', async () => {
    const { runs, history } = stubLists()
    render(
      <MemoryRouter initialEntries={['/candidates?tab=follow']}>
        <Candidates />
      </MemoryRouter>,
    )
    expect(await screen.findByText('지남 1')).toBeInTheDocument()
    expect(await screen.findByTestId('follow-up-badge')).toHaveTextContent('후속 확인 기한 지남')
    expect(runs).not.toHaveBeenCalled()
    expect(history).not.toHaveBeenCalled()
  })
})

describe('후보 상세: 조사 시작·자원 미리 보기', () => {
  function mount() {
    vi.spyOn(api, 'candidates').mockResolvedValue({ as_of: '2025-09-26', city: '강남', snapshot_id: 's', selection_budget: 2, items: [cand], monitoring_run_id: 'mon-1' })
    vi.spyOn(api, 'runs').mockResolvedValue({ items: [] } as never)
    const r = RUN_RESOURCES.items[0]
    const resources = vi.spyOn(api, 'resources').mockResolvedValue({ adm_cd: '11230510', need_type: null, group: null, items: [r], total: 12 })
    const createRun = vi.spyOn(api, 'createRun').mockResolvedValue({ run_id: 'run-9', status: 'queued', snapshot_id: 's', status_url: '/runs/run-9' })
    vi.spyOn(api, 'run').mockReturnValue(new Promise(() => {}))
    render(
      <MemoryRouter initialEntries={['/candidates/C-20250926-501']}>
        <Routes>
          <Route path="/candidates/:id" element={<CandidateDetail />} />
        </Routes>
      </MemoryRouter>,
    )
    return { resources, createRun }
  }

  it('조사 전 자원은 "n건 중 m건"으로 미리 보이고 도시·limit 을 보낸다', async () => {
    const { resources } = mount()
    expect(await screen.findByTestId('resource-preview')).toHaveTextContent('12건 중 1건')
    expect(resources).toHaveBeenCalledWith(expect.objectContaining({ adm_cd: '11230510', city: '강남', limit: 5 }), expect.anything())
  })

  it('조사 시작은 화면이 읽은 정기 감시 실행(monitoring_run_id)과 도시를 같이 보낸다', async () => {
    const { createRun } = mount()
    await userEvent.click(await screen.findByRole('button', { name: '조사 시작' }))
    expect(createRun).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'qa', candidate_id: 'C-20250926-501', city: '강남', monitoring_run_id: 'mon-1' }),
    )
  })

  it('실행 자원은 candidate_ids 로 이 후보 것을 앞에 둔다', () => {
    const base = RUN_RESOURCES.items[0]
    const items = [
      { ...base, resource_id: 'R9', candidate_id: 'C-2', candidate_ids: ['C-2'] },
      { ...base, resource_id: 'R1', candidate_id: 'C-2', candidate_ids: ['C-2', 'C-1'] },
    ]
    expect(uniqueResources(items, 'C-1').map((r) => r.resource_id)).toEqual(['R1', 'R9'])
  })
})
