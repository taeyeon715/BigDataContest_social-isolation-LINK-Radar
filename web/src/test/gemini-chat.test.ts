import { describe, expect, it } from 'vitest'
import type { HomeResponse } from '../api'
import { LIMIT_MESSAGE, MAX_QUESTION, contextOf, geminiBody, guardAnswer, parseRequest, readGemini } from '../lib/geminiChat'

const HOME = {
  as_of: '2025-09-26', city: '춘천', snapshot_id: 'dev-x', rule_version: 'rules-v2.0', selection_budget: 2,
  summary: { counts: { 'Level 3': 1, 'Level 2': 0, 'Level 1': 0, '판단 보류': 0 }, triggers: [], holiday_watch: ['춘천 남 -18%·여 -26%'], actions: [] },
  areas: [{ adm_cd: '32010640', adm_nm: '효자2동', level: 'Level 3', candidate_ids: ['C-1'], deviation_first: false }],
  candidates: [{
    candidate_id: 'C-1', candidate_type: 'scheduled_access_review', adm_cd: '32010640', adm_nm: '효자2동', check_first_adm: [],
    group: '고령층', analysis_cohort: '60대 이상', candidate_level: 'Level 3', level_reason: '남녀 60대 이상 Level 3 [rules-v2.0]',
    trigger: 'R1', response_type: '현장 확인', evidence_confidence: '보통', input_status: '충족', missing_inputs: [],
    counter_result: '해당 없음', counter_reason: null, queue_rank: 1, selected_for_review: true, cause_type: null,
    resources: [{ name: '가람약국', resource_type: '휴일 운영 약국', eligibility: '확인 필요', distance_km: 0.8, phone_based: false }],
  }],
  monitoring_run_id: null,
} as HomeResponse

describe('gemini chat rules', () => {
  it('checks the request', () => {
    expect(parseRequest({ question: ' ', as_of: '2025-09-26', city: '춘천' })).toBe('질문이 비어 있습니다.')
    expect(parseRequest({ question: 'x'.repeat(MAX_QUESTION + 1), as_of: '2025-09-26', city: '춘천' })).toMatch(/자 안으로/)
    expect(parseRequest({ question: '왜?', as_of: '2026-09-26', city: '춘천' })).toMatch(/기준일/)
    expect(parseRequest({ question: '왜?', as_of: '2025-09-26', city: '서울' })).toMatch(/도시/)
    expect(parseRequest({ question: '왜?', as_of: '2025-09-26', city: '춘천', adm_cd: 'x' })).toEqual({ question: '왜?', as_of: '2025-09-26', city: '춘천', adm_cd: null })
  })

  it('sends only this screen evidence, without the rule tag', () => {
    const ctx = contextOf(HOME, '32010640')
    expect(ctx.후보).toHaveLength(1)
    expect(ctx.후보[0].등급_이유).toBe('남녀 60대 이상 Level 3')
    const body = geminiBody(ctx, '왜?')
    expect(body.systemInstruction.parts[0].text).toContain('근거에 없는 사실·숫자')
    expect(JSON.stringify(body)).not.toContain('key')
  })

  it('drops sentences with numbers that are not in the evidence', () => {
    const ctx = contextOf(HOME, null)
    const g = guardAnswer('효자2동은 60대 이상 칸이 Level 3입니다. 남성은 -18% 줄었습니다. 주민 1234명이 고립돼 있습니다.', ctx)
    expect(g.lines).toEqual(['효자2동은 60대 이상 칸이 Level 3입니다.', '남성은 -18% 줄었습니다.'])
    expect(g.dropped).toBe(1)
  })

  it('stops on the free-tier limit (429) instead of switching models', () => {
    expect(readGemini(429, { error: { status: 'RESOURCE_EXHAUSTED' } }, {}, 'm')).toEqual({ status: 'limit', message: LIMIT_MESSAGE })
    expect(readGemini(500, null, {}, 'm').status).toBe('error')
    const ok = readGemini(200, { candidates: [{ content: { parts: [{ text: '생각', thought: true }, { text: '효자2동을 먼저 봅니다.' }] } }] }, contextOf(HOME, null), 'm')
    expect(ok).toEqual({ status: 'ok', lines: ['효자2동을 먼저 봅니다.'], model: 'm', dropped: 0 })
    expect(readGemini(200, { candidates: [{ content: { parts: [{ text: '9999명입니다.' }] } }] }, {}, 'm').status).toBe('error')
  })
})
