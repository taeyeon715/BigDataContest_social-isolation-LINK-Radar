import { useRef, type KeyboardEvent, type ReactNode } from 'react'

/** 탭 줄. ←/→ 로 옮기고 Home/End 로 처음·끝 (WAI-ARIA tabs). 넘치면 가로로 밀리고 스크롤 막대는 숨김 */
export function TabList<K extends string>({
  tabs,
  value,
  onChange,
  label,
  idPrefix = 'tab',
}: {
  tabs: { key: K; label: ReactNode }[]
  value: K
  onChange: (k: K) => void
  label: string
  idPrefix?: string
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.findIndex((t) => t.key === value)
    let j = -1
    if (e.key === 'ArrowRight') j = (i + 1) % tabs.length
    else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length
    else if (e.key === 'Home') j = 0
    else if (e.key === 'End') j = tabs.length - 1
    if (j < 0) return
    e.preventDefault()
    onChange(tabs[j].key)
    refs.current[tabs[j].key]?.focus()
  }
  return (
    <div className="no-scrollbar flex gap-1 overflow-x-auto overflow-y-hidden" role="tablist" aria-label={label} onKeyDown={onKey}>
      {tabs.map((t) => {
        const on = t.key === value
        return (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[t.key] = el
            }}
            id={`${idPrefix}-${t.key}`}
            role="tab"
            aria-selected={on}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={on ? 0 : -1}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 pb-3 pt-1 text-sm font-semibold ${
              on ? 'border-brand-600 text-brand-600' : 'border-transparent text-ink-600 hover:text-ink-900'
            }`}
            onClick={() => onChange(t.key)}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
