import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DatePicker } from '../components/DatePicker'

describe('DatePicker', () => {
  it('opens a month calendar, marks computed days and picks a date', () => {
    const onChange = vi.fn()
    render(<DatePicker value="2025-09-26" min="2025-07-01" max="2025-12-31" marked={['2025-09-23', '2025-09-26']} today="2025-09-30" onChange={onChange} />)
    expect(screen.queryByTestId('calendar')).toBeNull()
    fireEvent.click(screen.getByTestId('date-picker'))
    expect(screen.getByText('2025년 9월')).toBeInTheDocument()
    expect(screen.getByLabelText('2025-09-23 (계산일)')).toBeInTheDocument()
    expect(screen.getByLabelText('2025-09-26 (계산일)')).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByLabelText('2025-09-24'))
    expect(onChange).toHaveBeenCalledWith('2025-09-24')
    expect(screen.queryByTestId('calendar')).toBeNull() // 고르면 닫힘
  })

  it('stays inside the data range', () => {
    render(<DatePicker value="2025-07-03" min="2025-07-01" max="2025-12-31" onChange={() => {}} />)
    fireEvent.click(screen.getByTestId('date-picker'))
    expect(screen.getByLabelText('이전 달')).toBeDisabled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('calendar')).toBeNull()
  })
})
