import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { StructuralRiskReference } from '../api'
import { StructuralRisk } from '../components/StructuralRisk'

const reference: StructuralRiskReference = {
  candidate_id: 'C-1', city: '춘천', adm_cd: '32010350', dong: '남면', policy_group: '고령층', status: 'available',
  period_label: '기준일 2025-09-26 공개 자료: 주민등록 8월, 통신 7·8월', mapping_note: '60~64세가 포함됩니다.',
  note: '연령 간 위험 수준으로 읽지 않습니다.', comparison_note: '대응 등급 Level 2는 rules-v1.1로 정했고, 칸 근거 축은 참고입니다.',
  source_path: 'rules-v2.0 칸 근거 축', source_hash: null,
  cells: [{ sex: '남', age_band: '60대+', pop: 966, axes_on: ['S', 'V'], cell_type: '구조·경제 취약형', s: 0.412, h: 0.2, v: 0.15, a_km: 1.07, small: false }],
}

it('shows as-of months, axes and distinction from official level', () => {
  render(<StructuralRisk items={[reference]} candidateId="C-1" />)
  expect(screen.getByText(/주민등록 8월, 통신 7·8월/)).toBeVisible()
  expect(screen.getByText('혼자 사는 구조 + 경제 취약')).toBeVisible()
  expect(screen.getByText('구조·경제 취약형')).toBeVisible()
  expect(screen.getByText('0.20')).toBeVisible()
  expect(screen.getByText(/60~64세/)).toBeVisible()
  expect(screen.getByText(/대응 등급 Level 2/)).toBeVisible()
  expect(screen.queryByText('rules-v2.0 칸 근거 축')).toBeNull()
})

it('never shows another candidates reference when the selected candidate changes', () => {
  const { rerender } = render(<StructuralRisk items={[reference]} candidateId="C-1" />)
  rerender(<StructuralRisk items={[reference]} candidateId="C-2" />)
  expect(screen.queryByText('구조·경제 취약형')).toBeNull()
})

it('shows unavailable note without a table', () => {
  render(<StructuralRisk items={[{ ...reference, status: 'unavailable', cells: [], note: '기준일에 공개된 달을 확인하지 못했습니다.' }]} />)
  expect(screen.getByText('기준일에 공개된 달을 확인하지 못했습니다.')).toBeVisible()
  expect(screen.queryByRole('table')).toBeNull()
})
