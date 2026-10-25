import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HomeJump } from '../components/Layout'

describe('HomeJump', () => {
  it('lists sections 1-4 by name and scrolls to the clicked one', () => {
    const target = document.createElement('section')
    target.id = 'home-3'
    target.scrollIntoView = vi.fn()
    document.body.appendChild(target)
    render(<HomeJump />)
    const names = screen.getAllByRole('button').map((b) => b.textContent)
    expect(names).toEqual(['1요약', '2먼저 볼 곳', '3근거', '4질문하기'])
    fireEvent.click(screen.getByRole('button', { name: /근거/ }))
    expect(target.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' })
    expect(screen.getByRole('button', { name: /근거/ })).toHaveAttribute('aria-current', 'location')
    target.remove()
  })
})
