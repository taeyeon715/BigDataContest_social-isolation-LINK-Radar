// 화면: 승인 전 확인·완료 카드, 내일 아침 체크리스트·대응안, 등급 설명, 인쇄용 보기, 근거 표, 탭 키보드.
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, DISCLAIMER, type Candidate } from '../api'
import { ApprovalPanel, preChecks } from '../components/ApprovalPanel'
import { EvidenceTable } from '../components/EvidenceTable'
import { PrintReport } from '../components/PrintReport'
import { ActionPlanView, DecisionTrace, LevelExplain, MorningChecklistView } from '../components/ReportParts'
import { TabList } from '../components/Tabs'
import { followState } from '../lib/followups'
import { missingLabel, replaceInputCodes } from '../lib/labels'
import CandidateDetail, { nextAssigned, runLine } from '../pages/CandidateDetail'
import { REPORT_501 } from './fixtures'

const ACTION = REPORT_501.actions![0]
const ready = { status: 'awaiting_approval' as const, report_revision: 1, report_stale: false, abstain_reason: null }

describe('승인 전 확인', () => {
  it('근거·자원·검토 저장 세 항목을 만든다. 추천 자원이 없으면 자원 항목은 충족', () => {
    const c = preChecks({ evidenceIds: ['a', 'b', 'c'], checked: new Set(['a']), resourceCount: 3, judgedCount: 0, reviewSaved: false })
    expect(c.map((x) => [x.key, x.ok])).toEqual([
      ['evidence', false],
      ['resource', false],
      ['review', false],
    ])
    expect(c[0].detail).toBe('1 / 3')
    expect(preChecks({ evidenceIds: [], checked: new Set(), resourceCount: 0, judgedCount: 0, reviewSaved: true }).every((x) => x.ok)).toBe(true)
  })

  it('미충족 항목이 있으면 바로 승인하지 않고 "그래도 승인" 확인창을 띄운다', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined)
    const checks = preChecks({ evidenceIds: ['a', 'b', 'c'], checked: new Set(), resourceCount: 2, judgedCount: 0, reviewSaved: true })
    render(<ApprovalPanel run={ready} onDecide={onDecide} checks={checks} />)
    const list = screen.getByTestId('pre-checks')
    expect(list).toHaveTextContent('근거를 모두 확인함')
    expect(list).toHaveTextContent('0 / 3')
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    expect(onDecide).not.toHaveBeenCalled()
    const dialog = screen.getByRole('alertdialog')
    expect(dialog).toHaveTextContent('근거를 모두 확인함 (0 / 3)')
    expect(dialog).toHaveTextContent('자원을 1개 이상 판정함')
    expect(dialog).not.toHaveTextContent('검토를 저장함') // 충족한 항목은 확인창에 없음
    // 돌아가기 → 승인 안 함
    await userEvent.click(within(dialog).getByRole('button', { name: '돌아가서 확인하기' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(onDecide).not.toHaveBeenCalled()
    // 다시 눌러 그래도 승인
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    await userEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '그래도 승인' }))
    expect(onDecide).toHaveBeenCalledWith('approved', null)
  })

  it('모두 충족하면 확인창 없이 승인한다', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined)
    const checks = preChecks({ evidenceIds: ['a'], checked: new Set(['a']), resourceCount: 1, judgedCount: 1, reviewSaved: true })
    render(<ApprovalPanel run={ready} onDecide={onDecide} checks={checks} />)
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(onDecide).toHaveBeenCalledWith('approved', null)
  })

  it('승인 뒤에는 초록 완료 카드와 다음 할 일', async () => {
    const onPrint = vi.fn()
    render(
      <MemoryRouter>
        <ApprovalPanel
          run={{ ...ready, status: 'approved', report_revision: 2 }}
          onDecide={vi.fn()}
          done={{ report_revision: 2, approver: 'lead-01', created_at: '2025-09-26T09:30:00+09:00' }}
          onPrint={onPrint}
          nextHref="/candidates/C-2?as_of=2025-09-26"
        />
      </MemoryRouter>,
    )
    const done = screen.getByTestId('approval-done')
    expect(done).toHaveTextContent('2판으로 승인됨')
    expect(done).toHaveTextContent('lead-01')
    expect(screen.queryByRole('button', { name: '승인' })).toBeNull()
    expect(screen.getByRole('link', { name: '다음 배정 후보 →' })).toHaveAttribute('href', '/candidates/C-2?as_of=2025-09-26')
    await userEvent.click(screen.getByRole('button', { name: '인쇄용 보기' }))
    expect(onPrint).toHaveBeenCalled()
  })
})

describe('내일 아침 체크리스트·대응안', () => {
  it('체크리스트 다섯 칸과 "시스템은 명단을 만들지 않음"을 보인다', () => {
    render(<MorningChecklistView a={ACTION} />)
    const box = screen.getByTestId('morning-checklist')
    for (const k of ['① 꺼낼 명단', '② 연락할 곳', '③ 방법', '④ 기한', '⑤ 확인할 영역']) expect(box).toHaveTextContent(k)
    expect(box).toHaveTextContent('행복이음 위기정보 대상자')
    expect(box).toHaveTextContent('2025-10-02까지')
    expect(box).toHaveTextContent('건강·복약, 사회적 관계·연락')
    expect(screen.getByTestId('list-note')).toHaveTextContent('시스템은 명단을 만들지 않음')
  })

  it('대응안 5칸(주체·방법·기한·확인할 영역·연계 자원)과 위급 시 안내를 따로 보인다', () => {
    const a = { ...ACTION, action_plan: { ...ACTION.action_plan!, emergency_contacts: ['자살예방 상담전화 109'] } }
    render(<ActionPlanView a={a} />)
    const box = screen.getByTestId('action-plan')
    for (const k of ['주체', '방법', '기한', '확인할 영역', '연계 자원']) expect(within(box).getByText(k)).toBeInTheDocument()
    expect(box).toHaveTextContent('동 복지팀(맞춤형복지팀)')
    expect(box).toHaveTextContent('외로움안녕120')
    expect(screen.getByTestId('emergency-contacts')).toHaveTextContent('자살예방 상담전화 109')
  })

  it('체크리스트가 없는 후보(Level 0·1)는 아무것도 그리지 않는다', () => {
    const { container } = render(<MorningChecklistView a={{ ...ACTION, checklist: null }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('등급 설명·판단 기록', () => {
  it('조건마다 충족/미충족과 값·기준·기준까지 거리를 보인다', () => {
    const exp = REPORT_501.level_explanations![0]
    render(<LevelExplain exp={exp} checks={[{ condition: '관측 거점까지 직선거리', met: false, value: 0.3, threshold: 5, unit: 'km' }]} />)
    const rows = screen.getAllByTestId('rule-check')
    expect(rows).toHaveLength(4)
    const far = rows.find((r) => r.textContent?.includes('노인복지관·사회복지관(안부·관계 거점)까지 거리'))!
    expect(far).toHaveTextContent('✕ 미충족')
    expect(far).toHaveTextContent('값 0.3km (기준 5km) · 기준까지 4.7km')
    expect(rows[0]).not.toHaveTextContent('값 값')
    expect(rows[0]).toHaveTextContent('✓ 충족')
    expect(screen.getByTestId('level-explain')).toHaveTextContent('Level 3 가능')
  })

  it('조건 값의 단위: 비율은 %, 차이는 %p, km·위는 숫자 뒤 (최소값 값 같은 겹침 없음)', () => {
    render(
      <LevelExplain
        checks={[
          { condition: '이탈 크기 최소값', met: true, value: 0.153, threshold: 0.1, comparator: '>=', unit: '비율' },
          { condition: '구조 상위 (도시 안 상위 30%)', met: true, value: 5, threshold: 8, comparator: '<=', unit: '위' },
          { condition: '관측 거점까지 직선거리', met: true, value: 18.57, threshold: 5, comparator: '>=', unit: 'km' },
        ]}
      />,
    )
    const vals = screen.getAllByTestId('rule-check-value').map((x) => x.textContent)
    expect(vals[0]).toBe('— 값 15.3% (기준 10% 이상)')
    expect(vals[1]).toBe('— 값 5위 (기준 8위 이하)')
    expect(vals[2]).toBe('— 값 18.57km (기준 5km 이상)')
    expect(screen.getByTestId('level-explain')).not.toHaveTextContent('비율')
  })

  it('판단 기록은 단계·고른 것·누가 정했나를 보인다', () => {
    render(<DecisionTrace steps={REPORT_501.decision_trace!} />)
    const t = screen.getByTestId('decision-trace')
    expect(t).toHaveTextContent('요청 정리')
    expect(t).toHaveTextContent('SCHED-L2')
    expect(t).toHaveTextContent('LLM')
  })

  it('실행 상태 한 줄', () => {
    const g = (check: '신호' | '사실' | '표현', verdict: 'PASS' | 'REVISE', attempt = 1) => ({ guard: 'guard_report' as const, check, verdict, rule_ids: [], reasons: [], attempt })
    expect(runLine({ status: 'awaiting_approval', path: [], guard: [g('신호', 'PASS'), g('사실', 'REVISE'), g('사실', 'PASS', 2), g('표현', 'PASS')] })).toBe(
      '조사 완료 · 승인 대기 · 사실 검사 3종 통과',
    )
    expect(runLine({ status: 'running', path: ['intake', 'classify'], guard: [] })).toBe('조사 중 · 2단계 지남')
  })
})

describe('인쇄용 보기', () => {
  it('근거·할 수 있는 말·자원 판정·대응안·체크리스트·승인자·판·스냅샷·고지·대외비를 한 장에 담는다', () => {
    render(
      <PrintReport
        candidate={null}
        card={REPORT_501.evidence_card}
        report={REPORT_501}
        runId="run-1"
        snapshotId="snap-fake-1"
        judgements={[{ name: '외로움안녕120', judgement: '적합', memo: '연휴 운영 확인' }]}
        approval={{ decision: 'approved', approver: 'lead-01', created_at: '2025-09-26T09:30:00+09:00', report_revision: 1 }}
        followUp="2025-10-02"
      />,
    )
    const sheet = screen.getByTestId('print-report')
    expect(screen.getByTestId('distribution-mark')).toHaveTextContent('내부용(대외비)')
    expect(sheet).toHaveTextContent('1인세대 비율')
    expect(sheet).toHaveTextContent('할 수 있는 말')
    expect(sheet).toHaveTextContent('하면 안 되는 말')
    expect(screen.getByTestId('print-judgements')).toHaveTextContent('외로움안녕120 · 적합 · 연휴 운영 확인')
    expect(within(sheet).getByTestId('action-plan')).toBeInTheDocument()
    expect(within(sheet).getByTestId('morning-checklist')).toHaveTextContent('시스템은 명단을 만들지 않음')
    expect(sheet).toHaveTextContent('승인 · lead-01')
    expect(sheet).toHaveTextContent('1판 (근거 1판)')
    expect(sheet).toHaveTextContent('snap-fake-1')
    expect(sheet).toHaveTextContent('가짜 데이터')
    expect(screen.getByTestId('external-notice')).toHaveTextContent('특정 동네가 위험하다는 뜻이 아니라')
    expect(sheet).toHaveTextContent(DISCLAIMER)
    // 대응안 한 줄은 핵심 사실 목록에 중복으로 넣지 않음
    expect(within(sheet).queryByText(/^대응안 C-20250926-501/)).toBeNull()
  })

  it('추가 조사 뒤 회차 비교 문장은 사실 목록에서 빼고 따로 적는다', () => {
    const line = '1회차 대비 달라진 점: 추가 도구 calc_holiday_gap 실행 (담당자가 고름)'
    const report = { ...REPORT_501, round_changes: [line], fact_sentences: [...REPORT_501.fact_sentences, line] }
    render(<PrintReport candidate={null} card={REPORT_501.evidence_card} report={report} runId="r" snapshotId="snap-1" judgements={[]} approval={null} />)
    const sheet = screen.getByTestId('print-report')
    expect(sheet).toHaveTextContent('1회차 대비 달라진 점')
    expect(within(sheet).queryByText(line)).toBeNull()
    expect(sheet).toHaveTextContent('추가 도구 연휴 전후 차이 계산 실행')
  })

  it('승인 전이면 "승인 전"으로 적는다', () => {
    render(<PrintReport candidate={null} card={REPORT_501.evidence_card} report={REPORT_501} runId="r" snapshotId="snap-1" judgements={[]} approval={null} />)
    expect(screen.getByTestId('print-report')).toHaveTextContent('승인 전')
    expect(screen.getByTestId('print-report')).toHaveTextContent('저장된 자원 판정이 없습니다')
  })
})

describe('근거 표·용어', () => {
  it('한 줄 해석이 먼저 보이고 기술 정보는 "자세히"를 눌러야 보인다', async () => {
    render(<EvidenceTable card={REPORT_501.evidence_card} />)
    const lines = screen.getAllByTestId('evidence-line')
    expect(lines[0]).toHaveTextContent('강남 논현1동 1인세대 비율 0.61')
    expect(screen.queryByTestId('evidence-details')).toBeNull()
    await userEvent.click(screen.getAllByRole('button', { name: /자세히$/ })[0])
    expect(screen.getByTestId('evidence-details')).toHaveTextContent('EV-20250926-501-1')
    expect(screen.getAllByRole('button', { name: /접기$/ })[0]).toHaveAttribute('aria-expanded', 'true')
  })

  it('빠진 근거 코드를 한국어 이름으로', () => {
    expect(missingLabel('weather_asos')).toBe('기상 관측 날씨 (ASOS)')
    expect(missingLabel('resident_structure (개포3동 7개 지표 0, 품질 의심)')).toBe('주민등록 세대 구조 — 개포3동 7개 지표 0, 품질 의심')
    expect(missingLabel('모르는 값')).toBe('모르는 값')
    expect(replaceInputCodes('빠진 근거: weather_asos')).toBe('빠진 근거: 기상 관측 날씨 (ASOS)')
  })
})

describe('탭 키보드', () => {
  it('←/→/Home/End 로 탭을 옮긴다', async () => {
    function Harness() {
      const [v, setV] = useState<'a' | 'b' | 'c'>('a')
      return (
        <TabList
          label="시험"
          value={v}
          onChange={setV}
          tabs={[
            { key: 'a', label: 'A' },
            { key: 'b', label: 'B' },
            { key: 'c', label: 'C' },
          ]}
        />
      )
    }
    render(<Harness />)
    const tab = (n: string) => screen.getByRole('tab', { name: n })
    expect(tab('A')).toHaveAttribute('tabindex', '0')
    expect(tab('B')).toHaveAttribute('tabindex', '-1')
    tab('A').focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(tab('B')).toHaveAttribute('aria-selected', 'true')
    expect(tab('B')).toHaveFocus()
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(tab('C')).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{Home}')
    expect(tab('A')).toHaveAttribute('aria-selected', 'true')
  })
})

describe('다음 배정 후보·후속 확인', () => {
  const c = (id: string, rank: number | null, sel: boolean) => ({ candidate_id: id, queue_rank: rank, selected_for_review: sel }) as Candidate
  it('지금 후보 뒤 순번의 배정 후보, 없으면 처음 배정 후보', () => {
    const list = [c('A', 1, true), c('B', 2, true), c('C', 3, false)]
    expect(nextAssigned(list, 'A')?.candidate_id).toBe('B')
    expect(nextAssigned(list, 'B')?.candidate_id).toBe('A')
    expect(nextAssigned([c('A', 1, true)], 'A')).toBeNull()
  })
  it('기준일과 비교해 지남 / 이번 주 / 나중', () => {
    expect(followState({ follow_up_on: '2025-09-20' }, '2025-09-26')).toBe('overdue')
    expect(followState({ follow_up_on: '2025-10-02' }, '2025-09-26')).toBe('week')
    expect(followState({ follow_up_on: '2025-10-20' }, '2025-09-26')).toBe('later')
    // 서버가 준 상태(GET /followups 의 state)를 먼저 씀
    expect(followState({ follow_up_on: '2025-10-20', state: '지남' }, '2025-09-26')).toBe('overdue')
    expect(followState({ follow_up_on: '2025-09-20', state: '이번 주' }, '2025-09-26')).toBe('week')
  })
})

// ---- 후보 상세 화면 전체: 순서와 접힌 에이전트 경로
vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '강남', as_of: '2025-09-26', query: '?as_of=2025-09-26&city=강남' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

afterEach(() => {
  vi.restoreAllMocks()
})

describe('후보 상세 순서', () => {
  it('요약 → 근거 → 자원 → 대응안·보고서 → 승인 순서, 실행 단계는 한 줄로 접혀 있다', async () => {
    const cand: Candidate = {
      candidate_id: 'C-20250926-501', candidate_type: 'scheduled_access_review', city: '강남', adm_cd: '11230510', check_first_adm: [],
      analysis_cohort: '40대·50대 남성', service_eligibility_group: '40~64세 남성', trigger: 'R1 추석 연휴 D-7', input_status: '충족',
      missing_inputs: [], counter_result: '해당 없음', counter_reason: null, candidate_level: 'Level 2', level_reason: '구조 취약성 상위 + 예정된 트리거',
      queue_rank: 1, selected_for_review: true, selection_budget: 2, evidence_ids: [], is_shared_context: false,
    }
    vi.spyOn(api, 'candidates').mockResolvedValue({ as_of: '2025-09-26', city: '강남', snapshot_id: 's', selection_budget: 2, items: [cand] })
    vi.spyOn(api, 'runs').mockResolvedValue({ items: [{ run_id: 'run-1' }] } as never)
    vi.spyOn(api, 'run').mockResolvedValue({
      run_id: 'run-1', snapshot_id: 'snap-fake-1', status: 'awaiting_approval', report_revision: 1, report_stale: false, candidates: [cand],
      path: ['intake', 'classify', 'guard_report'], guard: [{ guard: 'guard_report', check: '사실', verdict: 'PASS', rule_ids: [], reasons: [], attempt: 1 }],
      abstain_reason: null, analysis_mode: 'simulated_replay', contract_check: null, decision_trace: [],
    } as never)
    vi.spyOn(api, 'resources').mockResolvedValue({ adm_cd: null, need_type: null, group: null, items: [] })
    vi.spyOn(api, 'runResources').mockResolvedValue({ run_id: 'run-1', snapshot_id: 'snap-fake-1', items: [] })
    vi.spyOn(api, 'evidence').mockResolvedValue({ run_id: 'run-1', snapshot_id: 'snap-fake-1', evidence_card: REPORT_501.evidence_card })
    vi.spyOn(api, 'report').mockResolvedValue({ run_id: 'run-1', snapshot_id: 'snap-fake-1', report_stale: false, report: REPORT_501 })
    vi.spyOn(api, 'history').mockResolvedValue({ run_id: 'run-1', reviews: [], approvals: [], report_revisions: [], finalized: [] })
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/candidates/C-20250926-501']}>
          <Routes>
            <Route path="/candidates/:id" element={<CandidateDetail />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    await screen.findByText('보고서 1판')
    const order = ['summary', 'evidence-table', 'morning-checklist', 'approval-panel'].map((t) => screen.getAllByTestId(t).filter((el) => !el.closest('[data-testid="print-report"]'))[0])
    for (let i = 1; i < order.length; i++) expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // 요약에 등급 설명과 할 수 있는 말
    const summary = screen.getByTestId('summary')
    // 거점 거리는 "노인복지관·사회복지관(안부·관계 거점)까지 거리"로, 행정복지센터를 넣지 않는 이유 한 줄 (심사 지적: 화면 속 모순)
    expect(within(summary).getByTestId('level-explain')).toHaveTextContent('노인복지관·사회복지관(안부·관계 거점)까지 거리')
    expect(within(summary).getByTestId('level-explain')).not.toHaveTextContent('관측 거점')
    expect(within(summary).getByTestId('hub-note-level')).toHaveTextContent('행정복지센터(주민센터)는 안부·관계 프로그램을 운영하지 않아')
    // 규칙 ID 는 "자세히" 안으로
    expect(within(summary).getByTestId('rule-id-details')).not.toHaveAttribute('open')
    expect(within(summary).getByTestId('claims')).toHaveTextContent('할 수 있는 말')
    // 실행 상태는 한 줄, 단계는 토글로
    expect(screen.getAllByText('조사 완료 · 승인 대기 · 사실 검사 1종 통과').length).toBeGreaterThan(0)
    expect(screen.queryByTestId('agent-path')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '에이전트 경로 보기' }))
    expect(within(screen.getByTestId('agent-path')).getByTestId('decision-trace')).toHaveTextContent('SCHED-L2') // 실행 상태가 비면 보고서의 판단 기록
    // 승인 전 확인: 근거 0/3, 검토 저장 안 함
    expect(screen.getByTestId('pre-checks')).toHaveTextContent('0 / 3')
    // 대외비 표시
    expect(screen.getAllByTestId('distribution-mark')[0]).toHaveTextContent('내부용(대외비)')
  })
})
