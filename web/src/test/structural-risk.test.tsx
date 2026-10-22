import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { StructuralRiskReference } from '../api'
import { StructuralRisk } from '../components/StructuralRisk'

const reference: StructuralRiskReference = {
  candidate_id: 'C-1', city: '춘천', adm_cd: '32010350', dong: '남면', policy_group: '고령층', status: 'available',
  period_label: '2025.12 기준 사후 계산', mapping_note: '60~64세가 포함됩니다.', note: '연령 간 수준 비교에 쓰지 않습니다.',
  comparison_note: '대응 등급 Level 2는 기준일의 구조·접근성·시점으로 정합니다.', source_path: 'reports/source.csv', source_hash: 'abc',
  cells: [{ sex: '남성', age_band: '60대+', stage: '경계', risk_type: '취약형', behavior: .2, vulnerability: .8 }],
}

it('shows the as-of limitation, original cells and distinction from official level', () => {
  render(<StructuralRisk items={[reference]} candidateId="C-1" />)
  expect(screen.getByText(/2025.12 기준 사후 계산/)).toBeVisible()
  expect(screen.getByText('경계')).toBeVisible()
  expect(screen.getByText('0.20')).toBeVisible()
  expect(screen.getByText(/60~64세/)).toBeVisible()
  expect(screen.getByText(/대응 등급 Level 2/)).toBeVisible()
  expect(screen.queryByText('reports/source.csv')).toBeNull()
})

it('never shows another candidates reference when the selected candidate changes', () => {
  const { rerender } = render(<StructuralRisk items={[reference]} candidateId="C-1" />)
  rerender(<StructuralRisk items={[reference]} candidateId="C-2" />)
  expect(screen.queryByText('경계')).toBeNull()
})

it('shows unavailable source note without a risk stage', () => {
  render(<StructuralRisk items={[{ ...reference, status: 'unavailable', cells: [], note: '참고 자료를 확인할 수 없습니다.' }]} />)
  expect(screen.getByText('참고 자료를 확인할 수 없습니다.')).toBeVisible()
  expect(screen.queryByRole('table')).toBeNull()
})
