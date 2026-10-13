import { describe, expect, it } from 'vitest'
import { requestHeaders } from '../api/client'

describe('API 요청 헤더', () => {
  it('토큰이 있으면 Bearer 헤더를 붙인다', () => {
    expect(requestHeaders(true, 'demo-token')).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer demo-token',
    })
  })
  it('토큰이 없으면 Authorization 을 보내지 않는다 (API 개발 모드)', () => {
    expect(requestHeaders(false, '')).toEqual({})
    expect(requestHeaders(true, '')).toEqual({ 'Content-Type': 'application/json' })
  })
})
