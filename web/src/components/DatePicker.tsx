import { useEffect, useRef, useState } from 'react'

// 기준일 달력: 날짜 칸을 누르면 월 달력이 열린다. marked(계산해 둔 날)는 점으로 표시.
// 정적 배포에서는 고른 날 이전의 가장 가까운 계산일 결과를 보여 준다 (Home 이 맞춤).
type Props = {
  value: string // YYYY-MM-DD
  min: string
  max: string
  onChange: (d: string) => void
  marked?: string[] | null
  today?: string | null // 작년 같은 날 (오늘 기준)
}

const WEEK = ['일', '월', '화', '수', '목', '금', '토']
const pad = (n: number) => String(n).padStart(2, '0')
const ymd = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`

export function DatePicker({ value, min, max, onChange, marked, today }: Props) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState(() => ({ y: Number(value.slice(0, 4)), m: Number(value.slice(5, 7)) - 1 }))
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    setView({ y: Number(value.slice(0, 4)), m: Number(value.slice(5, 7)) - 1 })
  }, [value])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  const marks = new Set(marked ?? [])
  const first = new Date(view.y, view.m, 1).getDay()
  const days = new Date(view.y, view.m + 1, 0).getDate()
  const cells: (number | null)[] = [...Array(first).fill(null), ...Array.from({ length: days }, (_, i) => i + 1)]
  const monthKey = `${view.y}-${pad(view.m + 1)}`
  const canPrev = monthKey > min.slice(0, 7)
  const canNext = monthKey < max.slice(0, 7)
  const move = (k: number) => setView(({ y, m }) => ({ y: m + k < 0 ? y - 1 : m + k > 11 ? y + 1 : y, m: (m + k + 12) % 12 }))

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        className="flex items-center gap-2 rounded-pill bg-white/70 px-4 py-1.5 text-sm font-semibold text-ink-900 hover:bg-white"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`기준일 ${value}, 달력 열기`}
        data-testid="date-picker"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M3 10h18M8 3v4M16 3v4" />
        </svg>
        {value}
      </button>
      {open && (
        <div role="dialog" aria-label="기준일 고르기" className="absolute left-0 top-full z-[1000] mt-2 w-[300px] rounded-2xl border border-line bg-surface p-4 shadow-lift" data-testid="calendar">
          <div className="mb-3 flex items-center justify-between">
            <button type="button" className="grid h-8 w-8 place-items-center rounded-lg text-ink-800 hover:bg-canvas disabled:opacity-30" onClick={() => move(-1)} disabled={!canPrev} aria-label="이전 달">
              ‹
            </button>
            <span className="text-base font-extrabold text-ink-900">
              {view.y}년 {view.m + 1}월
            </span>
            <button type="button" className="grid h-8 w-8 place-items-center rounded-lg text-ink-800 hover:bg-canvas disabled:opacity-30" onClick={() => move(1)} disabled={!canNext} aria-label="다음 달">
              ›
            </button>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center text-xs font-bold text-ink-600">
            {WEEK.map((w) => (
              <span key={w} className="py-1">
                {w}
              </span>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {cells.map((d, i) => {
              if (d === null) return <span key={`e${i}`} />
              const date = ymd(view.y, view.m, d)
              const out = date < min || date > max
              const sel = date === value
              return (
                <button
                  key={date}
                  type="button"
                  disabled={out}
                  onClick={() => {
                    onChange(date)
                    setOpen(false)
                  }}
                  aria-label={`${date}${marks.has(date) ? ' (계산일)' : ''}`}
                  aria-pressed={sel}
                  className={`relative h-9 rounded-lg text-sm font-semibold transition ${
                    sel ? 'bg-brand-600 text-white' : out ? 'text-ink-200' : 'text-ink-900 hover:bg-brand-50'
                  } ${date === today && !sel ? 'ring-2 ring-brand-400' : ''}`}
                >
                  {d}
                  {marks.has(date) && <span className={`absolute bottom-1 left-1/2 h-1 w-1 -translate-x-1/2 rounded-full ${sel ? 'bg-white' : 'bg-brand-600'}`} />}
                </button>
              )
            })}
          </div>
          <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-t border-line pt-3 text-xs text-ink-600">
            {marked && (
              <span className="flex items-center gap-1">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-brand-600" /> 계산해 둔 날 (다른 날은 가장 가까운 이전 계산일)
              </span>
            )}
            {today && (
              <span className="flex items-center gap-1">
                <span className="inline-block h-3 w-3 rounded ring-2 ring-brand-400" /> 작년 같은 날
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
