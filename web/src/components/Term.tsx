import { useId, type ReactNode } from 'react'

/** 화면에 나오는 낯선 말 풀이. 말 옆 "?"에 마우스를 올리거나 키보드로 가면 풀이가 뜬다 */
export const GLOSSARY: Record<string, string> = {
  등급: 'Level 0 일반 관찰 · Level 1 변화 관찰 · Level 2 검토 후보 · Level 3 우선 현장 검토. 위험 점수가 아니라 먼저 확인할 순서입니다.',
  '과거 재생': '지난 날짜를 기준일로 두고, 그날 알 수 있었던 자료만 써서 다시 돌려 본 결과입니다.',
  '가정한 공개 시점': '자료가 실제로 언제 공개됐는지 몰라서, 관측일에 공급 지연을 더해 "이때 알 수 있었다"고 가정한 날짜입니다.',
  '공유 맥락': '도시 단위 신호를 동에 같이 보여 준 것입니다. 그 동의 값이 바뀌었다는 뜻이 아닙니다.',
  '해석 상태': '직접 확인 = 원자료 값 그대로 · 계산값 = 원자료로 다시 계산 · 참고(확정 아님) = 탐색용 참고 · 확인 안 됨 = 자료 없음',
  '근거 일부 빠짐': '필수 근거 중 일부가 없어 있는 근거만으로 판단했습니다 (부분 판정).',
  '다른 원인 확인': '결제가 줄어든 것이 공휴일·날씨·도시 전체 변화 같은 다른 이유로 설명되는지 확인한 결과입니다 (반증).',
  '검토 배정': '한 번에 볼 수 있는 수(K)만큼 먼저 볼 후보를 고른 것입니다. 등급과는 따로 정합니다.',
  '분석 집단': '자료에서 같은 성·연령으로 묶은 집단입니다 (코호트).',
  '사실 검사': '보고서 문장의 숫자·지역·방향이 근거 표와 맞는지 규칙으로 대조한 검사입니다.',
  '확인할 영역': '판단도구 문항 채점이 아니라, 현장에서 먼저 물어볼 영역입니다.',
  대외비: '내부 결재·업무용입니다. 동 이름과 집단이 함께 나가면 특정 동네에 낙인이 생길 수 있어 밖으로 내보내지 않습니다.',
}

export function Term({ children, def, name }: { children?: ReactNode; def?: string; name?: string }) {
  const id = useId()
  const label = name ?? (typeof children === 'string' ? children : '')
  const text = def ?? GLOSSARY[label] ?? ''
  if (!text) return <>{children ?? label}</>
  return (
    <span className="group relative inline-flex items-center gap-0.5">
      <span>{children ?? label}</span>
      <button
        type="button"
        className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-ink-600/40 text-[12px] leading-none text-ink-600 hover:bg-canvas"
        aria-label={`${label} 뜻`}
        aria-describedby={id}
        onClick={(e) => e.preventDefault()}
      >
        ?
      </button>
      <span
        role="tooltip"
        id={id}
        className="absolute left-0 top-full z-30 mt-1 hidden w-64 max-w-[80vw] whitespace-normal rounded-xl bg-ink-900 px-3 py-2 text-xs font-normal leading-relaxed text-white shadow-lift group-focus-within:block group-hover:block print:hidden"
      >
        {text}
      </span>
    </span>
  )
}
