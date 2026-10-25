import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HolidayWatch } from '../components/HolidayWatch'

const LINES = [
  '2025 추석 연휴 주의 지역: 춘천 효자2동·소양동의 60대 이상 (Level 3, 2개 동 4칸, 연휴 10/03~10/09)',
  '근거: 지난 연휴(광복절)에 60대 이상 의료 결제가 40·50대보다 더 줄었음(춘천 남 -18%·여 -26%, 신한카드)',
  '할 일: 연휴 전 기존 돌봄 대상자 안부 확인',
  '관측 한계: 2025 추석 1회 관측',
]

describe('HolidayWatch', () => {
  it('shows the title, the place line and labelled rows', () => {
    render(<HolidayWatch lines={LINES} />)
    expect(screen.getByText('2025 추석 연휴 주의 지역')).toBeInTheDocument()
    expect(screen.getByText(/춘천 효자2동·소양동의 60대 이상/)).toBeInTheDocument()
    expect(screen.getByText('근거')).toBeInTheDocument()
    expect(screen.getByText('관측 한계')).toBeInTheDocument()
    expect(screen.getByText('2025 추석 1회 관측')).toBeInTheDocument()
  })

  it('renders nothing without lines', () => {
    const { container } = render(<HolidayWatch lines={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
