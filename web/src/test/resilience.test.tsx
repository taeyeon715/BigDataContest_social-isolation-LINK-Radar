// API가 꺼지거나 느릴 때(FE-01~04), 확인창 포커스(FE-05), 판정 라디오 키보드(FE-13), 보고서 근거 이름표·인쇄(FE-08·09).
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, currentRole, setRole, type RunStatusResponse } from '../api'
import { ApprovalPanel, ConfirmDialog, preChecks } from '../components/ApprovalPanel'
import { pageMarginCss, PrintReport } from '../components/PrintReport'
import { JudgementRadios } from '../components/ResourcePanel'
import { ErrorBox, Loading, PersonName, SLOW_TEXT, STUCK_TEXT } from '../components/ui'
import { evidenceIndex, plainText, refsToText, reportText, splitRefs } from '../lib/labels'
import { useAsync } from '../lib/useAsync'
import { backoffMs, MAX_RETRIES, useRunPoll } from '../lib/useRunPoll'
import Overview from '../pages/Overview'
import { RefText } from '../pages/CandidateDetail'
import { REPORT_501 } from './fixtures'

vi.mock('../lib/filters', () => ({ useFilters: () => ({ city: '강남', as_of: '2025-09-26', query: '?as_of=2025-09-26&city=강남' }) }))
vi.mock('../components/Layout', () => ({
  PageBody: ({ children }: any) => <div>{children}</div>,
  PageHero: ({ children }: any) => <div>{children}</div>,
  useFooterRun: () => {},
}))

afterEach(() => {
  setRole('승인')
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const down = () => new ApiError(0, 'NETWORK_ERROR', 'API 서버에 연결할 수 없습니다. API가 켜져 있는지 확인하세요.')

describe('현황: API가 꺼지면 카드마다 오류 (0건·없음으로 보이지 않음, FE-01)', () => {
  it('후보·후속 확인·현장 결과·바뀐 후보·후보 수 카드가 모두 오류와 [다시 시도]를 보인다', async () => {
    for (const k of ['overview', 'candidates', 'followups', 'outcomeSummary', 'activeSnapshot', 'capabilities'] as const)
      vi.spyOn(api, k).mockRejectedValue(down())
    render(
      <MemoryRouter>
        <Overview />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getAllByTestId('error-box').length).toBeGreaterThanOrEqual(5))
    // 빈 상태 문장이 나오면 안 됨
    expect(screen.queryByText(/후속 확인할 후보가 없습니다/)).toBeNull()
    expect(screen.queryByText(/아직 적은 현장 확인 결과가 없습니다/)).toBeNull()
    expect(screen.queryByText(/검토 배정된 후보가 없습니다/)).toBeNull()
    expect(screen.queryByText(/바뀐 후보가 없습니다/)).toBeNull()
    expect(screen.queryByText(/후속 확인 0건/)).toBeNull()
    expect(screen.queryByTestId('hero-count')).toBeNull()
    expect(screen.getByTestId('hero-error')).toHaveTextContent('0건이라는 뜻이 아닙니다')
    expect(within(screen.getByTestId('today-row')).getByRole('alert')).toHaveTextContent('API 서버에 연결할 수 없습니다')
    expect(within(screen.getByTestId('follow-card')).getByRole('button', { name: '다시 시도' })).toBeInTheDocument()
    expect(within(screen.getByTestId('outcome-summary')).getByRole('alert')).toBeInTheDocument()
  })

  it('[다시 시도]를 누르면 그 카드만 다시 부르고, 성공하면 빈 상태를 보인다', async () => {
    for (const k of ['overview', 'candidates', 'outcomeSummary', 'activeSnapshot', 'capabilities'] as const) vi.spyOn(api, k).mockRejectedValue(down())
    const fu = vi.spyOn(api, 'followups').mockRejectedValueOnce(down()).mockResolvedValue({ as_of: '2025-09-26', city: '강남', date_from: null, date_to: null, items: [] })
    render(
      <MemoryRouter>
        <Overview />
      </MemoryRouter>,
    )
    const card = await screen.findByTestId('follow-card')
    await userEvent.click(await within(card).findByRole('button', { name: '다시 시도' }))
    expect(await within(card).findByText(/후속 확인할 후보가 없습니다/)).toBeInTheDocument()
    expect(fu).toHaveBeenCalledTimes(2)
    expect(screen.getByText('이번 주 후속 확인 0건')).toBeInTheDocument()
  })
})

describe('useAsync: 도시·기준일이 바뀌면 옛 값을 지움 (FE-03)', () => {
  it('키가 바뀌면 새 응답 전까지 data 가 null 이고, 옛 요청은 끊는다', async () => {
    const signals: AbortSignal[] = []
    let resolveB: (v: string) => void = () => {}
    const fn = vi.fn((key: string, signal: AbortSignal) => {
      signals.push(signal)
      return key === 'A' ? Promise.resolve('data-A') : new Promise<string>((r) => (resolveB = r))
    })
    const { result, rerender } = renderHook(({ k }) => useAsync((s) => fn(k, s), [k]), { initialProps: { k: 'A' } })
    await waitFor(() => expect(result.current.data).toBe('data-A'))
    rerender({ k: 'B' })
    expect(result.current.data).toBeNull() // 옛 도시 숫자를 보이지 않음
    expect(result.current.loading).toBe(true)
    expect(signals[0].aborted).toBe(true)
    await act(async () => resolveB('data-B'))
    expect(result.current.data).toBe('data-B')
  })

  it('언마운트하면 진행 중 요청을 끊는다', () => {
    let sig: AbortSignal | null = null
    const { unmount } = renderHook(() =>
      useAsync((s) => {
        sig = s
        return new Promise(() => {})
      }, []),
    )
    unmount()
    expect(sig!.aborted).toBe(true)
  })
})

describe('느린 응답 안내 (FE-04)', () => {
  it('3초 뒤 "처음 계산 중" 안내, 20초 뒤 [다시 시도]', () => {
    vi.useFakeTimers()
    const retry = vi.fn()
    render(<Loading onRetry={retry} />)
    expect(screen.queryByTestId('slow-hint')).toBeNull()
    act(() => vi.advanceTimersByTime(3100))
    expect(screen.getByTestId('slow-hint')).toHaveTextContent(SLOW_TEXT)
    act(() => vi.advanceTimersByTime(17000))
    expect(screen.getByTestId('slow-hint')).toHaveTextContent(STUCK_TEXT)
    act(() => screen.getByRole('button', { name: '다시 시도' }).click())
    expect(retry).toHaveBeenCalled()
  })
})

describe('요청 시간 제한·취소 (FE-04, FE-23)', () => {
  it('시간 제한이 지나면 TIMEOUT, 밖에서 끊으면 ABORTED', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_u, init) =>
        new Promise((_res, rej) => {
          init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')))
        }) as never,
    )
    await expect(api.health({ timeoutMs: 20 })).rejects.toMatchObject({ code: 'TIMEOUT' })
    const ac = new AbortController()
    const p = api.health({ signal: ac.signal })
    ac.abort()
    await expect(p).rejects.toMatchObject({ code: 'ABORTED' })
  })

  it('JSON 이 아닌 502 응답도 쉬운 문장의 ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 }))
    await expect(api.health()).rejects.toMatchObject({ status: 502, code: 'HTTP_ERROR' })
  })
})

const runRes = (status: RunStatusResponse['status']): RunStatusResponse =>
  ({ run_id: 'run-1', status, report_revision: 1, published_report_revision: 1, report_stale: false, path: [], guard: [], candidates: [], errors: [], abstain_reason: null }) as unknown as RunStatusResponse

describe('조사 상태 폴링 (FE-02)', () => {
  it('잠깐 끊긴 오류는 2·4초 간격으로 다시 시도해 폴링을 이어 간다', async () => {
    vi.useFakeTimers()
    const run = vi
      .spyOn(api, 'run')
      .mockResolvedValueOnce(runRes('running'))
      .mockRejectedValueOnce(down())
      .mockRejectedValueOnce(down())
      .mockResolvedValueOnce(runRes('running'))
      .mockResolvedValue(runRes('awaiting_approval'))
    const { result } = renderHook(() => useRunPoll('run-1'))
    await act(async () => {})
    expect(result.current.run?.status).toBe('running')
    await act(async () => vi.advanceTimersByTime(1000)) // 1차 실패
    expect(result.current.retrying).toBe(1)
    expect(result.current.error).toBeNull()
    await act(async () => vi.advanceTimersByTime(backoffMs(1))) // 2차 실패
    expect(result.current.retrying).toBe(2)
    await act(async () => vi.advanceTimersByTime(backoffMs(2))) // 성공, 다시 1초 폴링
    expect(result.current.retrying).toBe(0)
    await act(async () => vi.advanceTimersByTime(1000))
    expect(result.current.run?.status).toBe('awaiting_approval')
    const n = run.mock.calls.length
    await act(async () => vi.advanceTimersByTime(10_000)) // 끝났으면 멈춤
    expect(run.mock.calls.length).toBe(n)
  })

  it(`${MAX_RETRIES}번 다시 시도해도 안 되면 오류를 보인다. 404 같은 오류는 바로`, async () => {
    vi.useFakeTimers()
    vi.spyOn(api, 'run').mockRejectedValue(down())
    const { result } = renderHook(() => useRunPoll('run-1'))
    await act(async () => {})
    for (let i = 1; i <= MAX_RETRIES; i++) await act(async () => vi.advanceTimersByTime(backoffMs(i)))
    await act(async () => {})
    expect(result.current.error?.code).toBe('NETWORK_ERROR')
    vi.spyOn(api, 'run').mockRejectedValue(new ApiError(404, 'RUN_NOT_FOUND', '없음'))
    const b = renderHook(() => useRunPoll('run-2'))
    await act(async () => {})
    expect(b.result.current.error?.code).toBe('RUN_NOT_FOUND')
  })

  it('페이지를 떠나면 진행 중 요청을 끊고 더 부르지 않는다', async () => {
    vi.useFakeTimers()
    let signal: AbortSignal | undefined
    const run = vi.spyOn(api, 'run').mockImplementation(async (_id, o) => {
      signal = o?.signal
      return runRes('running')
    })
    const { unmount } = renderHook(() => useRunPoll('run-1'))
    await act(async () => {})
    // 두 번째 요청이 진행 중일 때 떠남
    let release: () => void = () => {}
    run.mockImplementation((_id, o) => {
      signal = o?.signal
      return new Promise((res) => (release = () => res(runRes('running'))))
    })
    await act(async () => vi.advanceTimersByTime(1000))
    const calls = run.mock.calls.length
    unmount()
    expect(signal?.aborted).toBe(true)
    await act(async () => {
      release()
      vi.advanceTimersByTime(30_000)
    })
    expect(run.mock.calls.length).toBe(calls)
  })

  it('진행 중 요청이 있을 때 refresh() 는 줄을 하나 더 만들지 않는다', async () => {
    let release: () => void = () => {}
    const run = vi.spyOn(api, 'run').mockImplementation(() => new Promise((res) => (release = () => res(runRes('awaiting_approval')))))
    const { result } = renderHook(() => useRunPoll('run-1'))
    let p1: Promise<void> = Promise.resolve()
    let p2: Promise<void> = Promise.resolve()
    act(() => {
      p1 = result.current.refresh()
      p2 = result.current.refresh()
    })
    expect(run).toHaveBeenCalledTimes(1)
    await act(async () => release())
    await act(async () => {
      await Promise.resolve()
      release()
      await Promise.all([p1, p2])
    })
    expect(run).toHaveBeenCalledTimes(2) // 처음 1번 + 묶인 다시 부르기 1번
  })
})

describe('"그래도 승인" 확인창 (FE-05)', () => {
  it('Tab·Shift+Tab 이 창 안에서만 돌고 Esc 로 닫으면 [승인] 버튼으로 포커스가 돌아간다', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined)
    const checks = preChecks({ evidenceIds: ['a'], checked: new Set(), resourceCount: 0, judgedCount: 0, reviewSaved: true })
    render(
      <>
        <ApprovalPanel run={{ status: 'awaiting_approval', report_revision: 1, report_stale: false, abstain_reason: null }} onDecide={onDecide} checks={checks} />
        <button>창 밖 버튼</button>
      </>,
    )
    const approve = screen.getByRole('button', { name: '승인' })
    await userEvent.click(approve)
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.tagName).toBe('DIALOG')
    const cancel = within(dialog).getByRole('button', { name: '돌아가서 확인하기' })
    const ok = within(dialog).getByRole('button', { name: '그래도 승인' })
    expect(cancel).toHaveFocus()
    await userEvent.tab()
    expect(ok).toHaveFocus()
    await userEvent.tab() // 끝에서 다시 처음으로 (창 밖으로 안 나감)
    expect(cancel).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(ok).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(approve).toHaveFocus()
    expect(onDecide).not.toHaveBeenCalled()
  })

  it('확인창 단독: 열리면 [돌아가서 확인하기]에 포커스', () => {
    render(<ConfirmDialog unmet={[{ key: 'k', label: '근거를 모두 확인함', ok: false }]} onOk={() => {}} onCancel={() => {}} />)
    expect(screen.getByRole('button', { name: '돌아가서 확인하기' })).toHaveFocus()
  })
})

describe('판정 라디오 (FE-13)', () => {
  function Host() {
    const [v, setV] = useState<'적합' | '보류' | '부적합' | null>(null)
    return <JudgementRadios label="외로움안녕120 판정" value={v} onPick={setV} />
  }
  it('Tab 은 묶음에 한 번만 멈추고 화살표로 옮기며 고른다', async () => {
    render(
      <>
        <Host />
        <input aria-label="다음 칸" />
      </>,
    )
    await userEvent.tab()
    expect(screen.getByRole('radio', { name: '적합' })).toHaveFocus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { name: '보류' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: '보류' })).toHaveFocus()
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(screen.getByRole('radio', { name: '부적합' })).toHaveAttribute('aria-checked', 'true')
    await userEvent.tab()
    expect(screen.getByLabelText('다음 칸')).toHaveFocus()
  })
})

describe('보고서 문장의 근거 코드 → 이름표 (FE-09)', () => {
  const ev = REPORT_501.evidence_card.evidence
  const idx = evidenceIndex(ev)
  it('[EV-…] 를 "근거 n · 지표 이름" 버튼으로, 누르면 그 근거로', async () => {
    const onRef = vi.fn()
    render(<p>{RefText({ text: `구조 취약성이 높습니다 [${ev[0].evidence_id}].`, idx, onRef })}</p>)
    const btn = screen.getByTestId('fact-ref')
    expect(btn).toHaveTextContent(`근거 1 · ${plainText(ev[0].metric)}`)
    expect(btn).not.toHaveTextContent('EV-')
    await userEvent.click(btn)
    expect(onRef).toHaveBeenCalledWith(ev[0].evidence_id)
  })
  it('인쇄용 글자는 "(근거 n)", 자원 코드는 이름, 거점 이름은 쉬운 말', () => {
    expect(refsToText(`값 [${ev[1].evidence_id}]`, idx)).toBe('값 (근거 2)')
    expect(reportText('R0007 먼저 연락', { R0007: '외로움안녕120' })).toBe('외로움안녕120 먼저 연락')
    expect(plainText('관측 거점까지 직선거리 18.57km')).toBe('안부·관계 거점까지 거리 18.57km')
    expect(splitRefs('a [EV-1-2] b')).toEqual([{ text: 'a ' }, { ref: 'EV-1-2' }, { text: ' b' }])
  })
  it('인쇄 보기: 근거 ID 대신 번호, 장마다 대외비(@page 여백 상자), 승인자는 기록 그대로', () => {
    const report = { ...REPORT_501, fact_sentences: [`사실 하나 [${ev[0].evidence_id}]`] }
    render(
      <PrintReport
        candidate={null}
        card={REPORT_501.evidence_card}
        report={report}
        runId="run-1"
        snapshotId="snap-fake-1"
        judgements={[]}
        approval={{ decision: 'approved', approver: '김담당', created_at: '2025-09-26T09:30:00+09:00', report_revision: 1 }}
      />,
    )
    const sheet = screen.getByTestId('print-report')
    expect(sheet).toHaveTextContent('사실 하나 (근거 1)')
    expect(sheet).toHaveTextContent('승인 · 김담당')
    expect(sheet.querySelector('style')?.textContent).toContain('@bottom-right')
    expect(pageMarginCss('내부용(대외비)')).toContain('"내부용(대외비)"')
    expect(pageMarginCss('a"b')).toContain('"a\\"b"')
  })
})

describe('권한·횟수 한도·공용 계정 (보안 브랜치 반영)', () => {
  it('조회 역할이면 승인·보류·추가 조사를 잠그고 "승인 권한이 필요합니다"', () => {
    setRole('조회')
    render(<ApprovalPanel run={{ status: 'awaiting_approval', report_revision: 1, report_stale: false, abstain_reason: null }} onDecide={vi.fn()} />)
    expect(screen.getByRole('button', { name: '승인' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '보류' })).toBeDisabled()
    expect(screen.getByTestId('role-note')).toHaveTextContent('승인 권한이 필요합니다')
  })

  it('POST 가 403 FORBIDDEN 이면 역할을 낮춰 잡는다 (실행 만들기 → 조회)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error_code: 'FORBIDDEN', message: '검토 권한이 필요합니다.', details: [] }), { status: 403 }),
    )
    await expect(api.createRun({ mode: 'qa', candidate_id: 'C-1', as_of: '2025-09-26' } as never)).rejects.toMatchObject({ code: 'FORBIDDEN' })
    expect(currentRole()).toBe('조회')
  })

  it('429 RATE_LIMITED 는 Retry-After 초를 쉬운 문장에 넣는다', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error_code: 'RATE_LIMITED', message: 'too many', details: [] }), { status: 429, headers: { 'Retry-After': '42' } }),
    )
    const e = await api.qa({ question: 'x', as_of: '2025-09-26' }).catch((x) => x)
    expect(e.retryAfter).toBe(42)
    render(<ErrorBox error={e} />)
    expect(screen.getByRole('alert')).toHaveTextContent('42초 뒤 다시 시도하세요')
  })

  it('"공용:<이름>" 은 그대로 보이고 공용 계정 표시를 붙인다', () => {
    render(<PersonName name="공용:demo" />)
    expect(screen.getByText(/공용:demo/)).toBeInTheDocument()
    expect(screen.getByTestId('shared-account')).toHaveTextContent('공용 계정')
  })
})
