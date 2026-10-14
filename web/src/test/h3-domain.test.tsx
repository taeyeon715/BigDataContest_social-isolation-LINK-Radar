// H3 도메인 반영 화면: 보호 자원 칸, 명절 전·중·후, 폭염 특보 단계, 연결 3단계, 이의·정정 기록
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, roleNeeded, setRole, type CandidateAction, type ObjectionRecord, type ProtectiveResponse } from '../api'
import { ObjectionForm } from '../components/ObjectionForm'
import { ProtectivePanel } from '../components/ProtectivePanel'
import { ActionPlanView } from '../components/ReportParts'

afterEach(() => {
  vi.restoreAllMocks()
  setRole('승인')
})

const action: CandidateAction = {
  candidate_id: 'C-1',
  candidate_level: 'Level 2',
  group: '중장년 남성 1인가구',
  where: '강남 논현1동',
  check_areas: [],
  check_area_note: '',
  checklist: null,
  action_plan: {
    owner: ['동 복지팀(맞춤형복지팀)'],
    method: '전화 안부 → 방문 대상 추리기',
    due: '연휴 전날까지',
    due_date: null,
    check_areas: ['건강·복약'],
    linked_resources: [],
    tasks: ['휴일 운영 약국·의료기관 안내'],
    resource_note: null,
    emergency_contacts: [],
    text: '대응안',
    heat_stage: '폭염중대경보',
    contact_frequency: '하루 2회 안부 확인(오전·오후) (전화 → 받지 않으면 당일 방문)',
    holiday_timeline: [
      { phase: '전', rule: 'R1', label: '연휴 전', window: '연휴 D-7 ~ 전날', tasks: ['상시 복용약 남은 양 확인'], contact: '안내 문자 1회', current: true },
      { phase: '중', rule: 'R2', label: '연휴 중', window: '명절 당일 ~ 연휴 끝', tasks: ['명절 당일 안부 확인'], contact: '동의한 가구만 안부 문자·전화 1회', current: false },
      { phase: '후', rule: 'R3', label: '연휴 뒤', window: '연휴 끝 다음 날 ~ 10일', tasks: ['연결한 자원 이용 확인'], contact: '연결 확인 1회', current: false },
    ],
    partners: ['임대인·관리사무소·경비원', '고시원·원룸 운영자'],
    partner_note: '이상 신호만 알려 달라고 요청 (개인정보는 넘겨받지 않음)',
    link_steps: [
      { step: '안내', text: '필요 확인' },
      { step: '연결', text: '첫 상담 날 잡기' },
      { step: '확인', text: '1~2주 뒤 확인' },
    ],
    bundle: [{ need: '일자리·생계', resource_id: 'JB-GN01', name: '서울강남고용센터(고용복지+센터)', phone: '02-3468-4794' }],
  },
}

describe('대응안 action-v2 칸', () => {
  it('폭염 특보 단계·명절 전중후·협력자·연결 3단계를 보인다', () => {
    render(<ActionPlanView a={action} />)
    expect(screen.getByTestId('heat-stage')).toHaveTextContent('폭염중대경보')
    expect(screen.getByTestId('heat-frequency')).toHaveTextContent('하루 2회')
    const tl = screen.getByTestId('holiday-timeline')
    expect(tl.querySelectorAll(':scope > ol > li')).toHaveLength(3)
    expect(within(tl).getByText('지금').closest('li')).toHaveAttribute('aria-current', 'step')
    expect(screen.getByTestId('housing-partners')).toHaveTextContent('고시원·원룸 운영자')
    expect(screen.getByTestId('link-steps')).toHaveTextContent('1. 안내')
    expect(screen.getByTestId('link-steps')).toHaveTextContent('서울강남고용센터')
  })

  it('옛 응답(칸 없음)이면 추가 칸을 그리지 않는다', () => {
    const old = { ...action, action_plan: { ...action.action_plan!, heat_stage: undefined, holiday_timeline: undefined, partners: undefined, link_steps: undefined, bundle: undefined } }
    render(<ActionPlanView a={old} />)
    expect(screen.queryByTestId('holiday-timeline')).toBeNull()
    expect(screen.queryByTestId('heat-stage')).toBeNull()
    expect(screen.queryByTestId('link-steps')).toBeNull()
  })
})

const protective: ProtectiveResponse = {
  city: '춘천',
  adm_cd: '32010310',
  adm_nm: '동면',
  note: '보호 자원은 위험 표시가 아님',
  groups: [
    { group: '경로당', label: '경로당', in_dong: null, city_total: 371, items: [], status: '도시 집계만', note: '춘천시 경로당 371곳, 그중 면 지역 157곳' },
    { group: '더위·한파 쉼터', label: '무더위·한파 쉼터', in_dong: null, city_total: null, items: [], status: '미수집', note: '춘천 쉼터 목록 미수집' },
    {
      group: '돌봄 이웃', label: '돌봄단·통장·이장', in_dong: null, city_total: null, status: '수집', note: null,
      items: [{ resource_id: 'PP-CC02', name: '춘천시 이·통장', subtype: '통·이장', address: null, phone: null, schedule: null, holiday_open: '미상', source_url: 'https://example.org', last_verified: null, scope: '도시' }],
    },
  ],
}

describe('보호 자원 칸', () => {
  it('묶음별 수·상태를 보이고 위험 표시가 아님을 밝힌다', async () => {
    const spy = vi.spyOn(api, 'protective').mockResolvedValue(protective)
    render(<ProtectivePanel city="춘천" admCd="32010310" />)
    const panel = screen.getByTestId('protective-panel')
    expect(panel).toHaveTextContent('위험 표시가 아니며')
    await waitFor(() => expect(screen.getAllByTestId('protective-group')).toHaveLength(3))
    expect(spy).toHaveBeenCalledWith('춘천', '32010310', expect.anything())
    expect(panel).toHaveTextContent('도시 371곳 (동별 목록 없음)')
    expect(panel).toHaveTextContent('미수집')
    expect(screen.getByRole('region', { name: /보호 자원/ })).toBeInTheDocument()
  })
})

const rec: ObjectionRecord = {
  objection_id: 'obj-1', target_type: 'candidate', run_id: null, candidate_id: 'C-1', city: '강남', adm_cd: '11230520',
  reason_category: '후보가 아님(현장 사정)', text: '[이름] 이미 사례관리 중', status: '접수', resolution: null,
  submitted_by: 'worker-01', resolved_by: null, created_at: '2026-10-14T10:00:00+09:00', updated_at: '2026-10-14T10:00:00+09:00',
}

describe('이의·정정 기록', () => {
  it('사유와 내용을 보내고 목록에 붙인다', async () => {
    setRole('검토')
    vi.spyOn(api, 'objections').mockResolvedValue({ items: [], total: 0 })
    const create = vi.spyOn(api, 'createObjection').mockResolvedValue({ objection: rec, duplicate: false })
    render(<ObjectionForm city="강남" candidateId="C-1" admCd="11230520" />)
    await userEvent.selectOptions(screen.getByLabelText('사유'), '근거 자료 오류')
    await userEvent.type(screen.getByLabelText('내용 (선택)'), '이미 사례관리 중')
    await userEvent.click(screen.getByRole('button', { name: '이의·정정 접수' }))
    await waitFor(() => expect(create).toHaveBeenCalled())
    const body = create.mock.calls[0][0]
    expect(body).toMatchObject({ target_type: 'candidate', candidate_id: 'C-1', city: '강남', reason_category: '근거 자료 오류', text: '이미 사례관리 중' })
    expect(await screen.findByRole('status')).toHaveTextContent('접수했습니다')
    expect(screen.getByTestId('objection-list')).toHaveTextContent('[이름] 이미 사례관리 중')
    expect(screen.queryByRole('button', { name: '반영' })).toBeNull() // 검토 역할은 처리 버튼 없음
  })

  it('조회 역할은 접수 버튼이 잠기고, 처리는 승인 역할이 필요하다', async () => {
    setRole('조회')
    vi.spyOn(api, 'objections').mockResolvedValue({ items: [rec], total: 1 })
    render(<ObjectionForm city="강남" candidateId="C-1" admCd={null} />)
    expect(screen.getByRole('button', { name: '이의·정정 접수' })).toBeDisabled()
    expect(screen.getByTestId('role-note')).toHaveTextContent('검토 권한')
    expect(roleNeeded('POST', '/objections/obj-1/status')).toBe('승인')
    expect(roleNeeded('POST', '/objections')).toBe('검토')
  })
})
