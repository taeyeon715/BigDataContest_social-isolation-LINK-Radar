// 연휴 주의 블록: 첫 줄은 제목(주의 지역), 나머지는 "근거: …", "할 일: …", "관측 한계: …"
export function HolidayWatch({ lines, compact = false }: { lines?: string[]; compact?: boolean }) {
  if (!lines?.length) return null
  const [head, ...rest] = lines
  const i = head.indexOf(': ')
  const title = i > 0 ? head.slice(0, i) : head
  const where = i > 0 ? head.slice(i + 2) : ''
  return (
    <section className={`rounded-card border-l-4 border-caution bg-surface ${compact ? 'p-3' : 'p-5'} shadow-card`} aria-label="연휴 주의 지역" data-testid="holiday-watch">
      <div className="text-xs font-semibold text-caution">{title}</div>
      {where && <div className="mt-1 text-base font-bold text-ink-900">{where}</div>}
      <dl className="mt-2 grid gap-1 text-sm text-ink-800 sm:grid-cols-[6rem_1fr]">
        {rest.map((l) => {
          const j = l.indexOf(': ')
          return j > 0 ? (
            <div key={l} className="contents">
              <dt className="font-semibold text-ink-600">{l.slice(0, j)}</dt>
              <dd>{l.slice(j + 2)}</dd>
            </div>
          ) : (
            <dd key={l} className="sm:col-span-2">{l}</dd>
          )
        })}
      </dl>
    </section>
  )
}
