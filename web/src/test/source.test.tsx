// 화면마다 X-Data-Source 출처 띠 (경로 종류별로 기억)
import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { noteSource, sourceKind } from '../api/client'
import { SourceBadge } from '../components/Layout'

describe('출처 띠', () => {
  it('경로 첫 마디로 종류를 나눈다 (/runs/{id}/resources 는 runs)', () => {
    expect(sourceKind('/overview')).toBe('overview')
    expect(sourceKind('/runs/run-1/resources')).toBe('runs')
    expect(sourceKind('/resources')).toBe('resources')
  })

  it('화면이 부른 경로의 출처만 보이고, 섞이면 나눠 보인다', () => {
    noteSource('/overview', 'fake')
    noteSource('/runs/run-1', 'real')
    noteSource('/data/capabilities', 'config')
    const { rerender } = render(<SourceBadge kinds={['runs']} />)
    expect(screen.getByTestId('source-badge')).toHaveTextContent('실제 데이터')
    rerender(<SourceBadge kinds={['overview']} />)
    expect(screen.getByTestId('source-badge')).toHaveTextContent('가짜 데이터 · 화면 확인용')
    rerender(<SourceBadge kinds={['overview', 'runs', 'map']} />)
    const badge = screen.getByTestId('source-badge')
    expect(badge).toHaveTextContent('현황 가짜 데이터')
    expect(badge).toHaveTextContent('조사 실행 실제 데이터')
    expect(badge).not.toHaveTextContent('지도') // 아직 응답 없음
    // 출처가 바뀌면 다시 그림
    act(() => noteSource('/overview', 'real'))
    expect(screen.getByTestId('source-badge')).not.toHaveTextContent('가짜')
  })
})
