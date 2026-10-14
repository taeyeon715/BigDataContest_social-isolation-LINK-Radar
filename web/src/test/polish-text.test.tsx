// 화면 글자 다듬기: 자원 ID → 이름, "근거 근거" 겹침, 한계 칩 자료 이름, 답의 사실/설명 나누기,
// 이어 묻기 직전 대상 칩, 후보 카드·추가 조사 범위의 코드 숨김, 조사 중 안내.
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, DISCLAIMER, type Candidate, type FollowupResponse } from '../api'
import { CandidateCard } from '../components/CandidateCard'
import { withRefs } from '../components/EvidenceTable'
import { FollowupPanel, targetLabel } from '../components/FollowupPanel'
import { MoreForm } from '../components/MoreForm'
import { Loading, RUN_SLOW_TEXT, STUCK_HINT_MS, STUCK_TEXT } from '../components/ui'
import { limitText, refsToText, reportText, resourceIdsToNames, splitAnswer, splitRefs } from '../lib/labels'
import { EXAMPLES } from '../pages/QA'
import { FOLLOWUP_ANSWER } from './fixtures'

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const NAMES = { 'EP-C2200577': '강촌동아약국', 'WN-WLF0001-CC': '춘천시 노인맞춤돌봄', R0001: '남면행정복지센터' }

describe('자원 ID → 이름 (원장 ID 모양 모두)', () => {
  it('R0000 말고 EP-C·WN-WLF 모양도 이름으로, "ID (이름)"은 이름 한 번만', () => {
    expect(resourceIdsToNames('담당자 검토: 자원 EP-C2200577 (강촌동아약국) 적합', NAMES)).toBe('담당자 검토: 자원 강촌동아약국 적합')
    expect(resourceIdsToNames('연계 자원 WN-WLF0001-CC, R0001', NAMES)).toBe('연계 자원 춘천시 노인맞춤돌봄, 남면행정복지센터')
    // 이름을 모르는 ID 뒤에 이름이 바로 오면 ID 를 뺌
    expect(resourceIdsToNames('확인할 자원(추천 제외): PP-GN02 강남구 어르신 상담 — 확인일 없음', {})).toBe('확인할 자원(추천 제외): 강남구 어르신 상담 — 확인일 없음')
    // 근거·후보 ID 는 건드리지 않음
    expect(resourceIdsToNames('[EV-20250926-STR-32010350] C-20250926-001', NAMES)).toBe('[EV-20250926-STR-32010350] C-20250926-001')
    expect(reportText('담당자 검토: 자원 EP-C2200577 (강촌동아약국) 적합', NAMES)).not.toMatch(/EP-C/)
  })
})

describe('"근거 근거 4" 겹침 없음', () => {
  const idx = new Map([['EV-1', { n: 4, name: '1인세대 비율' }]])
  it('splitRefs 는 "근거 [EV-…]" 앞의 "근거"를 뺀다', () => {
    expect(splitRefs('멀다는 근거 [EV-1] 때문입니다')).toEqual([{ text: '멀다는 ' }, { ref: 'EV-1' }, { text: ' 때문입니다' }])
  })
  it('인쇄 글자: "근거 [EV-…]" → "근거 4", 그냥 [EV-…] → "(근거 4)"', () => {
    expect(refsToText('멀다는 근거 [EV-1] 때문', idx)).toBe('멀다는 근거 4 때문')
    expect(refsToText('비율 0.61 [EV-1].', idx)).toBe('비율 0.61 (근거 4).')
  })
  it('답 문장 버튼', () => {
    render(<p data-testid="p">{withRefs('구조 근거 [EV-1]는 높다', vi.fn(), (id) => (id === 'EV-1' ? '근거 4 · 1인세대 비율' : undefined))}</p>)
    expect(screen.getByTestId('p').textContent).toBe('구조 근거 4 · 1인세대 비율는 높다')
  })
})

describe('한계 칩과 답 나누기', () => {
  it('한계 앞머리 자료 코드는 쉬운 이름', () => {
    expect(limitText('card1: 공개일(2025-09-29)이 기준일(2025-09-26) 뒤')).toBe('카드 데이터1: 공개일(2025-09-29)이 기준일(2025-09-26) 뒤')
    expect(limitText('주민등록 1인세대는 1인가구와 다름')).toBe('주민등록 1인세대는 1인가구와 다름')
  })
  it('고지 줄을 경계로 사실과 설명을 나눈다', () => {
    const a = splitAnswer(`사실 하나 [EV-1].\n가정한 공개 시점: …\n${DISCLAIMER}\n설명 하나.\n설명 둘.`)
    expect(a.facts).toEqual(['사실 하나 [EV-1].', '가정한 공개 시점: …'])
    expect(a.explanation).toEqual(['설명 하나.', '설명 둘.'])
    expect(a.disclaimer).toBe(true)
    expect(splitAnswer('사실만').explanation).toEqual([])
  })
  it('질의응답 예시는 지금 동작과 맞는 질문 (밤 카드 결제는 답, 밤 체류 인구는 보류)', () => {
    expect(EXAMPLES).toContain('강남 60대의 밤 카드 결제는 어때?')
    expect(EXAMPLES).toContain('강남 60대의 밤 체류 인구는 어때?')
    expect(EXAMPLES).not.toContain('강남 60대의 밤 활동은 어때?')
  })
})

const CAND: Candidate = {
  candidate_id: 'C-20250926-007',
  candidate_type: 'scheduled_access_review',
  city: '춘천',
  adm_cd: '32010350',
  check_first_adm: [],
  analysis_cohort: '60대·70대·80대+',
  service_eligibility_group: '65세 이상',
  trigger: 'R1 추석 연휴 D-7',
  input_status: '충족',
  missing_inputs: [],
  counter_result: '해당 없음',
  counter_reason: null,
  candidate_level: 'Level 3',
  level_reason: null,
  is_shared_context: false,
  selected_for_review: true,
  queue_rank: 1,
  evidence_ids: [],
} as unknown as Candidate

describe('이어 묻기', () => {
  it('직전 대상 칩: 대상을 읽은 답이 오면 "직전 대상: 춘천 남면 · 고령층"', async () => {
    const res: FollowupResponse = {
      ...FOLLOWUP_ANSWER,
      run_id: 'run-x',
      answer: `1인세대 비율 0.47(비율) — 춘천 남면, 2025-08, 방향 없음 [EV-20250926-STR-32010350].\n${DISCLAIMER}\n구조 근거 [EV-20250926-STR-32010350]는 남면의 1인세대 비율입니다.`,
      source: 'llm',
      target: { candidate_ids: ['C-20250926-007'], adm_cds: ['32010350'] },
    }
    vi.spyOn(api, 'followup').mockResolvedValue(res)
    render(<FollowupPanel runId="run-x" onCite={vi.fn()} refName={() => '근거 4 · 1인세대 비율'} candidates={[CAND]} />)
    expect(screen.queryByTestId('followup-target')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: '이 후보는 왜 이 등급이야?' }))
    expect(await screen.findByTestId('followup-target')).toHaveTextContent('직전 대상: 춘천 남면 · 고령층')
    // 사실 목록과 설명이 따로, "근거 근거" 겹침 없음
    expect(screen.getByTestId('followup-facts')).toHaveTextContent('1인세대 비율 0.47')
    const exp = screen.getByTestId('followup-explanation')
    expect(exp).toHaveTextContent('구조 근거 4 · 1인세대 비율는')
    expect(exp).not.toHaveTextContent('근거 근거')
  })

  it('대상 이름: 후보를 모르면 동 이름, 아무것도 없으면 null', () => {
    expect(targetLabel({ candidate_ids: ['C-x'], adm_cds: ['32010350'] }, [])).toBe('남면')
    expect(targetLabel(null)).toBeNull()
  })
})

describe('코드 숨김', () => {
  it('후보 카드 아래에 후보 ID 대신 "자세히 보기" (ID 는 title)', () => {
    render(
      <MemoryRouter>
        <CandidateCard c={CAND} query="" />
      </MemoryRouter>,
    )
    const card = screen.getByTestId('candidate-card')
    expect(card).not.toHaveTextContent('C-20250926-007')
    expect(within(card).getByTestId('candidate-card-more')).toHaveAttribute('title', '후보 ID C-20250926-007')
  })

  it('추가 조사 범위: 도구 코드 이름은 화면에 없고 title 로만', () => {
    render(<MoreForm candidates={[{ candidate_id: 'C-1', label: 'Level 3 · 춘천 남면' }]} remaining={1} busy={false} onSubmit={vi.fn()} onCancel={vi.fn()} />)
    const form = screen.getByTestId('more-form')
    expect(form).not.toHaveTextContent('get_mobility_layer')
    expect(form).not.toHaveTextContent('C-1')
    expect(within(form).getByText('이동·체류 자료 더 보기').closest('label')).toHaveAttribute('title', '도구 이름 get_mobility_layer')
  })
})

describe('조사 중 안내', () => {
  it('진행 중으로 확인된 실행은 20초가 지나도 "답이 없습니다" 대신 조사 중 안내', () => {
    vi.useFakeTimers()
    render(<Loading label="근거를 모으는 중" slowText={RUN_SLOW_TEXT} stuckText={RUN_SLOW_TEXT} />)
    act(() => {
      vi.advanceTimersByTime(STUCK_HINT_MS + 100)
    })
    expect(screen.getByTestId('slow-hint')).toHaveTextContent('조사 중입니다')
    expect(screen.queryByText(STUCK_TEXT)).toBeNull()
  })
})
