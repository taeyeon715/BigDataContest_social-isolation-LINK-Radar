import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HomeResponse } from '../api'
import { localAnswer, sameDayLastData, snapDate, upcomingEvents } from '../lib/home'

// 지도는 jsdom 에서 그리지 않음 (Leaflet). 고른 동만 확인
vi.mock('../components/DongMap', () => ({
  DongMap: ({ onSelect }: { onSelect: (a: string) => void }) => (
    <button data-testid="fake-map" onClick={() => onSelect('32010640')}>
      지도
    </button>
  ),
  LevelLegend: () => null,
  LEVEL_FILL: { 'Level 3': '#1F5FA8', 'Level 2': '#5E9BD9', 'Level 1': '#B9D3F0', 'Level 0': '#DCE3EE', '판단 보류': '#9AA1AE' },
}))

function home(city: '강남' | '춘천', l3: boolean): HomeResponse {
  return {
    as_of: '2025-09-26', city, snapshot_id: 'dev-test', rule_version: 'rules-v2.0', selection_budget: 2,
    summary: {
      counts: { 'Level 3': l3 ? 1 : 0, 'Level 2': 1, 'Level 1': 0, '판단 보류': 0 },
      triggers: [{ code: 'R1', name: '추석 연휴 D-7 (10/03~10/09)', starts_on: '2025-10-03' }],
      holiday_watch: l3 ? ['2025 추석 연휴 주의 지역: 춘천 효자2동의 60대 이상', '할 일: 연휴 전 기존 돌봄 대상자 안부 확인'] : [],
      actions: ['현장 확인'],
    },
    areas: [
      { adm_cd: '32010640', adm_nm: '효자2동', level: l3 ? 'Level 3' : null, candidate_ids: l3 ? ['C-1'] : [], deviation_first: false },
      { adm_cd: '32010650', adm_nm: '효자3동', level: 'Level 2', candidate_ids: ['C-2'], deviation_first: false },
    ],
    candidates: [
      {
        candidate_id: 'C-1', candidate_type: 'scheduled_access_review', adm_cd: '32010640', adm_nm: '효자2동', check_first_adm: [],
        group: '고령층', analysis_cohort: '60대 이상', candidate_level: 'Level 3',
        level_reason: '남녀 60대 이상 Level 3(행동 고립, 카드 결제 공백). [rules-v2.0]', trigger: 'R1 추석 연휴 D-7',
        response_type: '현장 확인', evidence_confidence: '보통', input_status: '충족', missing_inputs: [], counter_result: '해당 없음',
        counter_reason: null, queue_rank: 1, selected_for_review: true, cause_type: '달력 공백형',
        resources: [{ name: '가람약국', resource_type: '휴일 운영 약국', eligibility: '확인 필요', distance_km: 0.78, phone_based: false }],
      },
    ],
    monitoring_run_id: 'run-1',
  }
}

afterEach(() => vi.restoreAllMocks())

describe('home helpers', () => {
  it('maps today to the same day of 2025 data', () => {
    expect(sameDayLastData('2026-10-25')).toBe('2025-10-25')
    expect(sameDayLastData('2027-02-01')).toBeNull() // 1~6월은 자료 없음
    expect(snapDate('2025-10-25', ['2025-10-14', '2025-10-21', '2025-10-28'])).toBe('2025-10-21')
    expect(snapDate('2025-06-01', ['2025-07-01'])).toBe('2025-07-01')
  })

  it('lists upcoming holidays with last-year analog dates', () => {
    const ev = upcomingEvents('2026-10-25')
    expect(ev[0]).toMatchObject({ name: '성탄절 연휴', dDay: 61, analog: '2025-12-18' })
    expect(ev.find((e) => e.name === '설 연휴')?.analog).toBeNull()
  })

  it('answers only from the evidence on screen', () => {
    const h = home('춘천', true)
    expect(localAnswer('어떤 자원을 연결하면 돼?', h, null).join(' ')).toContain('가람약국')
    expect(localAnswer('왜 먼저 봐야 해?', h, null).join(' ')).not.toContain('[rules-v2.0]')
    expect(localAnswer('담당자는 무엇을 하면 돼?', h, null)[0]).toContain('현장 확인')
  })
})

describe('Home page', () => {
  it('shows the summary, the ranked dongs and evidence for the picked dong', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const city = decodeURIComponent(url).includes('city=강남') ? '강남' : '춘천'
      return new Response(JSON.stringify(home(city, city === '춘천')), { status: 200 })
    })
    const { default: Home } = await import('../pages/Home')
    render(
      <MemoryRouter initialEntries={['/?as_of=2025-09-26&city=춘천']}>
        <Routes>
          <Route path="/" element={<Home />} />
        </Routes>
      </MemoryRouter>,
    )
    const summary = await screen.findByTestId('home-summary')
    expect(within(summary).getByText('효자2동')).toBeInTheDocument()
    expect(within(summary).getByText('먼저 볼 동 없음')).toBeInTheDocument() // 강남
    expect(screen.getByTestId('basis-line')).toHaveTextContent('기준일 2025-09-26 결과')
    expect(screen.getByTestId('level-guide')).toHaveTextContent('위험 점수가 아니라 먼저 확인할 순서')
    expect(screen.getByTestId('home-watch')).toHaveTextContent('연휴 주의 지역')
    // 처음에는 가장 높은 등급 동의 근거, 등급 꼬리표는 화면에서 뺌
    const ev = await screen.findByTestId('home-evidence')
    expect(ev).toHaveTextContent('효자2동')
    expect(ev).toHaveTextContent('가람약국')
    expect(ev).not.toHaveTextContent('[rules-v2.0]')
    fireEvent.click(within(screen.getByTestId('home-rank')).getByText(/효자3동/))
    await waitFor(() => expect(screen.getByText(/지도나 목록에서 동을 고르면/)).toBeInTheDocument())
  })

  it('preset cards explain the scene and switch to its city as well as its date', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const city = decodeURIComponent(String(input)).includes('city=강남') ? '강남' : '춘천'
      return new Response(JSON.stringify(home(city, city === '춘천')), { status: 200 })
    })
    const { default: Home } = await import('../pages/Home')
    render(
      <MemoryRouter initialEntries={['/?city=강남']}>
        <Routes>
          <Route path="/" element={<Home />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByTestId('home-summary')
    expect(screen.getByRole('heading', { level: 2, name: /먼저 볼 곳 · 강남/ })).toBeInTheDocument()
    // 날짜를 바꿔도 페이지가 ④ 질문창으로 끌려가지 않음 (질문창은 안쪽만 스크롤)
    const pageScroll = vi.fn()
    Element.prototype.scrollIntoView = pageScroll
    const presets = screen.getByTestId('presets')
    expect(presets).toHaveTextContent('겨울 점검')
    fireEvent.click(within(presets).getByText('동절기 (12/20)'))
    await waitFor(() => expect(screen.getByRole('heading', { level: 2, name: /먼저 볼 곳 · 춘천/ })).toBeInTheDocument())
    expect(screen.getByTestId('basis-line')).toHaveTextContent('기준일 2025-12-20')
    expect(pageScroll).not.toHaveBeenCalled()
  })
})
