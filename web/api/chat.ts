// Vercel 서버 함수: POST /api/chat. 정적 배포 질문창을 Gemini(무료 등급)로 답한다.
// 키는 Vercel 환경 변수 GEMINI_API_KEY 에만 둔다 (레포·화면에 넣지 않음). 결제수단이 없는 키라 한도를 넘으면 429 → 멈춤.
import type { HomeResponse } from '../src/api/types'
import { CITY_CODE, GEMINI_MODEL, contextOf, geminiBody, parseRequest, readGemini, type ChatResult } from '../src/lib/geminiChat'

const TIMEOUT_MS = 25_000
// @types/node 없이도 빌드되게 (Vercel Node 런타임에는 process.env 가 있음)
const env = (globalThis as unknown as { process: { env: Record<string, string | undefined> } }).process.env

function json(body: ChatResult | { status: 'error'; message: string }, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } })
}

export async function POST(request: Request): Promise<Response> {
  const key = env.GEMINI_API_KEY
  if (!key) return json({ status: 'error', message: '질문 기능이 설정되지 않았습니다 (GEMINI_API_KEY 없음).' }, 503)
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return json({ status: 'error', message: '요청을 읽을 수 없습니다.' }, 400)
  }
  const req = parseRequest(body)
  if (typeof req === 'string') return json({ status: 'error', message: req }, 400)

  // 근거는 이 배포본의 정적 자료에서 직접 읽는다 (가장 가까운 계산일로 맞춤)
  const origin = new URL(request.url).origin
  const idx = (await fetch(`${origin}/data/home/index.json`).then((r) => (r.ok ? r.json() : null))) as { dates: string[] } | null
  if (!idx) return json({ status: 'error', message: '근거 자료 목록을 읽지 못했습니다.' }, 502)
  const before = idx.dates.filter((d) => d <= req.as_of)
  const day = before.length ? before[before.length - 1] : idx.dates[0]
  const home = (await fetch(`${origin}/data/home/${day}_${CITY_CODE[req.city]}.json`).then((r) => (r.ok ? r.json() : null))) as HomeResponse | null
  if (!home) return json({ status: 'error', message: '근거 자료를 읽지 못했습니다.' }, 502)
  const ctx = contextOf(home, req.adm_cd ?? null)

  const model = env.GEMINI_MODEL || GEMINI_MODEL
  const ac = new AbortController()
  const timer = setTimeout(() => ac.abort(), TIMEOUT_MS)
  try {
    const r = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key },
      body: JSON.stringify(geminiBody(ctx, req.question)),
      signal: ac.signal,
    })
    const data = await r.json().catch(() => null)
    return json(readGemini(r.status, data, ctx, model))
  } catch {
    return json({ status: 'error', message: ac.signal.aborted ? 'Gemini가 제때 답하지 않았습니다.' : 'Gemini에 연결하지 못했습니다.' })
  } finally {
    clearTimeout(timer)
  }
}
