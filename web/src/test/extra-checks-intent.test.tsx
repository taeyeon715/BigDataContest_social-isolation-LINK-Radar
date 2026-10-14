// 조사 루프 "추가로 확인한 것"(질문·고른 이유·풀림 여부·근거 링크)과 이어 묻기 질문 종류 칩.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type FollowupResponse, type TraceStep } from '../api'
import { FollowupPanel } from '../components/FollowupPanel'
import { DecisionTrace, ExtraChecks, extraChecksFromTrace, type ExtraChecksData } from '../components/ReportParts'
import { FOLLOWUP_ANSWER } from './fixtures'

afterEach(() => {
  vi.restoreAllMocks()
})

const DATA: ExtraChecksData = {
  rounds: 2,
  items: [
    {
      label: '연휴 회복 미확인',
      question: '춘천: 지난 연휴 중·뒤 결제가 평소 수준으로 돌아왔는지 아직 모름',
      city: '춘천',
      tool: 'calc_holiday_gap',
      round: 1,
      source: 'llm',
      why: '반증이 애매한 이탈 후보라 먼저 확인',
      status: 'unanswered',
      finding: '연휴 깊이·회복: 값 없음',
      evidence_ids: ['EV-20250926-HOL'],
    },
    {
      label: '의료 접근',
      question: '춘천 남면: 연휴 동안 문을 여는 의료 자원이 가까이 있는지',
      city: '춘천',
      adm_cd: '32010350',
      tool: 'find_resources',
      round: 2,
      source: 'rule',
      why: '규칙: 우선순위 2 (의료 접근)',
      status: 'answered',
      finding: '의료 접근 자원 후보 2건',
      evidence_ids: ['EV-20250926-RES-32010350'],
    },
  ],
  not_picked: ['폭염 반응 (춘천)'],
  stopped: '남은 질문 없음',
}

const step = (node: string, inputs: Record<string, unknown> = {}, reason: string | null = null): TraceStep => ({
  node,
  choice: null,
  next: null,
  source: 'rule',
  rule_ids: [],
  reason,
  inputs,
  changed: [],
  started_at: null,
  ms: null,
})

describe('추가로 확인한 것', () => {
  it('질문·이유·풀림 여부·근거 링크를 보여 준다', async () => {
    const onCite = vi.fn()
    render(<ExtraChecks data={DATA} onCite={onCite} />)
    const box = screen.getByTestId('extra-checks')
    expect(box).toHaveTextContent('추가로 확인한 것')
    expect(box).toHaveTextContent('등급·순서·반증 판정에 쓰지 않음')
    const items = screen.getAllByTestId('extra-check')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveAttribute('data-status', 'unanswered')
    expect(items[0]).toHaveTextContent('여전히 모름')
    expect(items[0]).toHaveTextContent('AI가 고름')
    expect(items[0]).toHaveTextContent('반증이 애매한 이탈 후보라 먼저 확인')
    expect(items[1]).toHaveTextContent('풀림')
    expect(items[1]).toHaveTextContent('규칙이 고름')
    await userEvent.click(within(items[1]).getByRole('button', { name: 'EV-20250926-RES-32010350' }))
    expect(onCite).toHaveBeenCalledWith('EV-20250926-RES-32010350')
    expect(box).toHaveTextContent('고르지 않은 질문: 폭염 반응 (춘천)')
    expect(box).toHaveTextContent('멈춘 이유: 남은 질문 없음')
  })

  it('판단 기록의 check_findings 칸에서 꺼내 판단 기록 위에 보여 준다', () => {
    const steps = [step('open_questions'), step('investigate'), step('check_findings', { rounds: 2, items: DATA.items }, '남은 질문 없음')]
    expect(extraChecksFromTrace(steps)?.items).toHaveLength(2)
    render(<DecisionTrace steps={steps} />)
    expect(screen.getByTestId('extra-checks')).toBeInTheDocument()
    expect(screen.getByTestId('decision-trace')).toHaveTextContent('미해결 질문')
    expect(screen.getByTestId('decision-trace')).toHaveTextContent('추가 확인 고르기')
  })

  it('조사 루프가 없으면 칸을 그리지 않는다', () => {
    render(<DecisionTrace steps={[step('intake')]} />)
    expect(screen.queryByTestId('extra-checks')).toBeNull()
    expect(extraChecksFromTrace([step('intake')])).toBeNull()
  })
})

describe('이어 묻기 질문 종류', () => {
  it('답에 질문 종류 칩을 붙인다', async () => {
    vi.spyOn(api, 'followup').mockResolvedValue({ ...FOLLOWUP_ANSWER, run_id: 'run-x', intent: 'resources' } as FollowupResponse)
    render(<FollowupPanel runId="run-x" onCite={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '연결할 자원은?' }))
    const chip = await screen.findByTestId('followup-intent')
    expect(chip).toHaveTextContent('질문 종류 · 자원·대응')
  })

  it('카드에 없는 비교는 NO_DATA 와 새 조사 안내', async () => {
    const res = {
      ...FOLLOWUP_ANSWER,
      run_id: 'run-x',
      status: 'abstained',
      answer: null,
      cited_evidence_ids: [],
      abstain_reason: 'NO_DATA: 이 카드에는 비교할 다른 기간(지난주 등)의 근거가 없습니다.',
      needed_data: ['기준일을 바꾼 새 조사'],
      intent: 'compare',
    } as FollowupResponse
    vi.spyOn(api, 'followup').mockResolvedValue(res)
    render(<FollowupPanel runId="run-x" onCite={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('이어 묻기 질문'), '지난주랑 비교하면?')
    await userEvent.click(screen.getByRole('button', { name: '묻기' }))
    expect(await screen.findByTestId('followup-new-run')).toHaveTextContent('기준일을 바꾼 새 조사')
    expect(screen.getByTestId('followup-intent')).toHaveTextContent('비교')
  })
})
