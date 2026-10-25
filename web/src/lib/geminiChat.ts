// 정적 배포 질문창의 Gemini 연결 규칙 (서버 함수 web/api/chat.ts 와 시험이 같이 씀).
// - 근거는 서버가 자기 배포본의 /data/home JSON 에서 읽는다 (화면이 보낸 근거를 믿지 않음)
// - 근거에 없는 숫자가 든 문장은 뺀다. 등급·후보는 바꾸지 못한다
// - 무료 등급만: 429(한도 초과)면 다른 모델로 넘기지 않고 멈춘다
import type { HomeCandidate, HomeResponse } from '../api/types'

export const GEMINI_MODEL = 'gemini-2.5-flash-lite' // 무료 등급 있음 (ai.google.dev 가격표, 2026-10-22 갱신본 확인)
export const MAX_QUESTION = 300
export const MAX_OUTPUT_TOKENS = 500
export const MAX_SENTENCES = 4
export const CITY_CODE: Record<string, string> = { 강남: 'GN', 춘천: 'CC' }

export type ChatRequest = { question: string; as_of: string; city: string; adm_cd?: string | null }
export type ChatResult =
  | { status: 'ok'; lines: string[]; model: string; dropped: number }
  | { status: 'limit'; message: string }
  | { status: 'error'; message: string }

export const LIMIT_MESSAGE = '오늘 무료 질문 한도가 찼어요. 근거 카드에서 찾은 답으로 대신합니다.'

/** 요청 검사: 질문 길이, 기준일 모양, 도시 */
export function parseRequest(body: unknown): ChatRequest | string {
  const b = (body ?? {}) as Partial<ChatRequest>
  const question = typeof b.question === 'string' ? b.question.trim() : ''
  if (!question) return '질문이 비어 있습니다.'
  if (question.length > MAX_QUESTION) return `질문은 ${MAX_QUESTION}자 안으로 적어 주세요.`
  if (typeof b.as_of !== 'string' || !/^2025-\d{2}-\d{2}$/.test(b.as_of)) return '기준일이 올바르지 않습니다.'
  if (typeof b.city !== 'string' || !(b.city in CITY_CODE)) return '도시는 강남·춘천만 됩니다.'
  const adm = typeof b.adm_cd === 'string' && /^\d{8}$/.test(b.adm_cd) ? b.adm_cd : null
  return { question, as_of: b.as_of, city: b.city, adm_cd: adm }
}

function candView(c: HomeCandidate) {
  return {
    동: c.adm_nm ?? '도시 전체',
    집단: c.group ?? c.analysis_cohort,
    등급: c.candidate_level,
    등급_이유: (c.level_reason ?? '').replace(/\s*\[rules-[^\]]*\]/g, ''),
    할_일: c.response_type,
    근거_신뢰도: c.evidence_confidence,
    다른_원인_확인: c.counter_reason,
    빠진_근거: c.missing_inputs,
    연결_자원: c.resources.map((r) => `${r.name}(${r.resource_type})`),
  }
}

/** Gemini 에 넘길 근거: 요약 + 고른 동 후보(없으면 등급 높은 후보 5개) */
export function contextOf(home: HomeResponse, adm: string | null) {
  const picked = adm ? home.candidates.filter((c) => c.adm_cd === adm || c.check_first_adm.includes(adm)) : []
  const rank: Record<string, number> = { 'Level 3': 3, 'Level 2': 2, '판단 보류': 1, 'Level 1': 1 }
  const top = (picked.length ? picked : [...home.candidates].sort((a, b) => (rank[b.candidate_level ?? ''] ?? 0) - (rank[a.candidate_level ?? ''] ?? 0))).slice(0, 5)
  return {
    기준일: home.as_of,
    도시: home.city,
    후보_수: home.summary.counts,
    점검_시점: home.summary.triggers.map((t) => t.name),
    연휴_주의: home.summary.holiday_watch,
    먼저_볼_동: home.areas.filter((a) => a.level === 'Level 3').map((a) => a.adm_nm),
    후보: top.map(candView),
  }
}

export const SYSTEM_PROMPT = [
  '너는 복지 담당자를 돕는 LINK-Radar 설명 도우미다. 아래 [근거] JSON 안의 내용만으로 한국어로 답한다.',
  '- 근거에 없는 사실·숫자·지역·기관을 지어내지 않는다. 모르면 "이 화면 근거로는 알 수 없습니다"라고 말한다.',
  '- 등급(Level)은 규칙이 정한 확인 순서다. 등급을 바꾸거나 새로 매기지 않는다. 위험 점수라고 부르지 않는다.',
  '- 개인의 고립·질병·위험을 진단하지 않는다. 동·성·연령 집단 단위로만 말한다. 최종 판단은 담당자가 한다고 한다.',
  `- ${MAX_SENTENCES}문장 이내, 쉬운 말로. 목록 기호·마크다운을 쓰지 않는다.`,
].join('\n')

export function geminiBody(ctx: unknown, question: string) {
  return {
    systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
    contents: [{ role: 'user', parts: [{ text: `[근거]\n${JSON.stringify(ctx)}\n\n[질문]\n${question}` }] }],
    generationConfig: { maxOutputTokens: MAX_OUTPUT_TOKENS, temperature: 0.2 },
  }
}

/** 응답 → 문장. 근거에 없는 숫자가 든 문장은 뺀다 (몇 개 뺐는지 같이) */
export function guardAnswer(text: string, ctx: unknown): { lines: string[]; dropped: number } {
  const known = new Set((JSON.stringify(ctx).match(/\d+(?:[.,]\d+)?/g) ?? []).map((n) => n.replace(',', '')))
  const sentences = text
    .replace(/[*#`]/g, '')
    .split(/(?<=[.!?。])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const keep = sentences.filter((s) => (s.match(/\d+(?:[.,]\d+)?/g) ?? []).every((n) => known.has(n.replace(',', ''))))
  return { lines: keep.slice(0, MAX_SENTENCES), dropped: sentences.length - keep.length }
}

/** Gemini 응답 JSON → 결과. 429 는 무료 한도 초과로 멈춤 */
export function readGemini(status: number, data: unknown, ctx: unknown, model: string): ChatResult {
  if (status === 429) return { status: 'limit', message: LIMIT_MESSAGE }
  if (status !== 200) return { status: 'error', message: `Gemini 응답 오류 (HTTP ${status})` }
  const d = data as { candidates?: { content?: { parts?: { text?: string; thought?: boolean }[] }; finishReason?: string }[] }
  const cand = d.candidates?.[0]
  const text = (cand?.content?.parts ?? []).filter((p) => !p.thought).map((p) => p.text ?? '').join('')
  if (!text.trim()) return { status: 'error', message: `답을 받지 못했습니다 (${cand?.finishReason ?? '빈 응답'})` }
  const { lines, dropped } = guardAnswer(text, ctx)
  if (!lines.length) return { status: 'error', message: '근거에 없는 숫자가 들어간 답이라 보여 주지 않습니다.' }
  return { status: 'ok', lines, model, dropped }
}
