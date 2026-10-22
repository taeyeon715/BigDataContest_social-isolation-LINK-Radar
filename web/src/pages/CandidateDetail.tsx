import { StructuralRisk } from '../components/StructuralRisk'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  api,
  ApiError,
  APPROVER_NAME,
  REVIEWER_NAME,
  DISTRIBUTION_INTERNAL,
  isAborted,
  type Candidate,
  type Decision,
  type EvidenceCard,
  type MoreFocus,
  type ReportResponse,
  type ResourceRecommendation,
  type RunHistoryResponse,
  type RunResource,
  type RunStatusResponse,
} from '../api'
import { ApprovalPanel, approvalBlock, preChecks, regenerating, type ShownReport } from '../components/ApprovalPanel'
import { FollowupPanel } from '../components/FollowupPanel'
import { ClaimsBox, EvidenceTable } from '../components/EvidenceTable'
import { PageBody, PageHero, useFooterRun } from '../components/Layout'
import { PrintReport, type PrintJudgement } from '../components/PrintReport'
import { ActionPlanView, CandidateContext, CheckAreasView, DecisionTrace, EventTimeline, ExtraChecks, HeatStageLabel, LevelExplain, MorningChecklistView } from '../components/ReportParts'
import { ResourcePanel, type JudgementDraft } from '../components/ResourcePanel'
import { OutcomeForm } from '../components/OutcomeForm'
import { ObjectionForm } from '../components/ObjectionForm'
import { ProtectivePanel } from '../components/ProtectivePanel'
import { Term } from '../components/Term'
import { Badge, Card, CounterChip, Empty, ErrorBox, HoldNotice, InputStatusBadge, LevelBadge, Loading, RUN_SLOW_TEXT, SelectedBadge, TimeLabelBar, TypeChip , PersonName, RoleNote, useRole } from '../components/ui'
import { useFilters } from '../lib/filters'
import { useRunPoll } from '../lib/useRunPoll'
import {
  DECISION_LABEL,
  ERROR_TEXT,
  HUB_NOTE,
  MODE_LABEL,
  REVISION_REASON_LABEL,
  evidenceIndex,
  mentionsHub,
  plainText,
  VERDICT_LABEL,
  reportText,
  splitRefs,
  limitText,
  NODE_LABEL,
  PHASE_LABEL,
  roundChangesView,
  admName,
  candidatePlace,
  counterView,
  errorText,
  fmtTime,
  groupOf,
  isActionSentence,
  missingLabel,
} from '../lib/labels'

const BUSY = new Set(['queued', 'running'])
const PREVIEW_LIMIT = 5 // 조사 전 자원 미리 보기 수

/** 담당자가 고치는 검토 값. 실행(runId)마다 따로 둔다 (후보를 바꿔도 이전 후보의 판정·메모가 넘어가지 않게) */
export type EditState = {
  runId: string | null
  draft: JudgementDraft
  memo: string
  followUp: string
  checked: Set<string>
  touched: boolean // 마지막 저장·복원 뒤 화면에서 고쳤나
  restored: boolean // 이 실행의 저장 이력을 한 번이라도 불러왔나
}
export const emptyEdit = (runId: string | null): EditState => ({ runId, draft: {}, memo: '', followUp: '', checked: new Set(), touched: false, restored: false })

/** 이 실행의 마지막 저장 검토 (검토는 저장할 때마다 전체 값을 보냄 → 마지막 한 건이 지금 저장된 값) */
export function savedEdit(runId: string, h: RunHistoryResponse): EditState {
  const lr = h.reviews.at(-1)
  const e = emptyEdit(runId)
  if (!lr) return { ...e, restored: true }
  const draft: JudgementDraft = {}
  lr.resource_judgements.forEach((j) => (draft[j.resource_id] = { judgement: j.judgement, memo: j.memo ?? '' }))
  return { ...e, draft, memo: lr.memo ?? '', followUp: lr.follow_up_on ?? '', checked: new Set(lr.checked_evidence_ids), restored: true }
}

/** 이력이 왔을 때: 고치던 것이 없으면 저장값 그대로, 처음 불러오는데 이미 고치던 것이 있으면 합침, 그 밖에는 고치던 값을 둠 */
export function applySaved(cur: EditState, saved: EditState): EditState {
  if (cur.runId !== saved.runId || !cur.touched) return saved
  if (cur.restored) return cur
  return {
    ...cur,
    draft: { ...saved.draft, ...cur.draft },
    memo: cur.memo || saved.memo,
    followUp: cur.followUp || saved.followUp,
    checked: new Set([...saved.checked, ...cur.checked]),
    restored: true,
  }
}

/** 실행 자원은 자원 하나에 한 줄 (candidate_ids). 이 후보에게 추천된 자원을 앞에 둠.
 * 옛 서버는 후보마다 한 줄씩 줄 수 있어 자원 ID 로 한 번 더 합침 (판정은 자원 ID 단위) */
export function uniqueResources(items: RunResource[], candidateId: string): RunResource[] {
  const mine = (r: RunResource) => ((r.candidate_ids ?? [r.candidate_id]).includes(candidateId) ? 0 : 1)
  const seen = new Set<string>()
  return [...items]
    .sort((a, b) => mine(a) - mine(b))
    .filter((r) => {
      if (seen.has(r.resource_id)) return false
      seen.add(r.resource_id)
      return true
    })
}

/** 실행 상태 한 줄: "조사 완료 · 사실 검사 3종 통과" */
export function runLine(run: Pick<RunStatusResponse, 'status' | 'guard' | 'path'>): string {
  if (BUSY.has(run.status)) return `조사 중 · ${run.path.length}단계 지남`
  const head = { awaiting_approval: '조사 완료 · 승인 대기', approved: '조사 완료 · 승인됨', held: '조사 완료 · 보류 기록' }[run.status as string] ?? PHASE_LABEL[run.status]
  const last = new Map<string, (typeof run.guard)[number]>()
  run.guard.forEach((g) => {
    const prev = last.get(g.check)
    if (!prev || prev.attempt <= g.attempt) last.set(g.check, g)
  })
  if (last.size === 0) return head
  const bad = Array.from(last.values()).filter((g) => g.verdict !== 'PASS')
  return bad.length === 0 ? `${head} · 사실 검사 ${last.size}종 통과` : `${head} · ${bad.map((g) => `${g.check} 검사 ${g.verdict}`).join(', ')}`
}

/** 에이전트가 지나온 단계와 검사 결과 (펼쳤을 때) */
function RunProgress({ run }: { run: RunStatusResponse }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs text-ink-600">
        실행 {run.run_id} · 스냅샷 {run.snapshot_id} · <Term name="과거 재생">{MODE_LABEL[run.analysis_mode]}</Term>
      </div>
      <ol className="flex flex-wrap gap-1 text-xs" aria-label="지나온 단계">
        {run.path.map((n, i) => (
          <li key={`${n}-${i}`} className="chip bg-canvas text-ink-600">
            {i + 1}. {NODE_LABEL[n] ?? n}
          </li>
        ))}
        {BUSY.has(run.status) && <li className="chip animate-pulse bg-brand-50 text-brand-700">진행 중…</li>}
      </ol>
      {run.guard.length > 0 && (
        <div className="flex flex-wrap gap-1 text-xs">
          {run.guard.map((g, i) => (
            <span key={i} className={`chip ${g.verdict === 'PASS' ? 'bg-okay-soft text-okay' : 'bg-caution-soft text-caution'}`} title={[g.rule_ids.length > 0 ? `규칙 ${g.rule_ids.join(',')}` : '', ...g.reasons].filter(Boolean).join(', ')}>
              {g.check} 검사 {g.attempt}차: {VERDICT_LABEL[g.verdict] ?? g.verdict}
            </span>
          ))}
        </div>
      )}
      {run.contract_check?.warnings.map((w) => (
        <div key={w} className="text-xs text-caution">
          계약 경고: {limitText(w)}
        </div>
      ))}
    </div>
  )
}

/** 조사 전 자원 미리 보기 (GET /resources, 원장 기준). 판정은 조사 실행에 묶인 목록에서만 함 */
export function ResourcePreview({
  items,
  total,
  needTypes = [],
  scopeTotal = null,
}: {
  items: ResourceRecommendation[]
  total: number | null
  needTypes?: string[]
  scopeTotal?: number | null
}) {
  const n = total ?? items.length
  return (
    <div className="mt-4 border-t border-line pt-3" data-testid="resource-preview">
      <div className="text-xs font-semibold text-ink-600">
        근처 자원 미리 보기 · {n > items.length ? `${n}건 중 ${items.length}건` : `${items.length}건`}
      </div>
      {needTypes.length > 0 && (
        <div className="mt-0.5 text-xs text-ink-600" data-testid="resource-preview-needs">
          이 후보에 필요한 것({needTypes.join('·')})에 맞는 자원 {n}건
          {scopeTotal !== null && scopeTotal > n ? ` (이 동·집단에서 볼 수 있는 ${scopeTotal}건 중)` : ''}
        </div>
      )}
      {items.length === 0 ? (
        <div className="mt-1 text-xs text-ink-600">맞는 자원이 원장에 없습니다.</div>
      ) : (
        <ul className="mt-1 flex flex-wrap gap-1.5">
          {items.map((r) => (
            <li key={r.resource_id} className="chip bg-canvas text-ink-800">
              {r.name}
              {r.phone_based ? ' · 전화' : r.distance_km !== null ? ` · ${r.distance_km}km` : ''}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-1 text-xs text-ink-600">판정은 조사를 시작한 뒤 이 조사에 묶인 목록에서 합니다.</div>
    </div>
  )
}

/** 보고서 문장: 자원·입력 코드는 이름으로, [EV-…] 는 "근거 n · 지표 이름" 버튼(누르면 근거 표 그 행으로) (FE-09) */
export function RefText({
  text,
  idx,
  names = {},
  onRef,
}: {
  text: string
  idx: Map<string, { n: number; name: string }>
  names?: Record<string, string>
  onRef: (id: string) => void
}): ReactNode {
  return splitRefs(reportText(text, names)).map((p, i) => {
    if ('text' in p) return <span key={i}>{p.text}</span>
    const r = idx.get(p.ref)
    return (
      <button
        key={i}
        type="button"
        className="mx-0.5 rounded bg-brand-50 px-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-100"
        title={`근거 ID ${p.ref}`}
        data-testid="fact-ref"
        onClick={() => onRef(p.ref)}
      >
        {r ? `근거 ${r.n} · ${r.name}` : '근거 보기'}
      </button>
    )
  })
}

/** 다음 배정 후보: 지금 후보보다 배정 순번이 뒤인 것 중 첫째, 없으면 다른 배정 후보 */
export function nextAssigned(list: Candidate[], id: string): Candidate | null {
  const sel = list.filter((c) => c.selected_for_review && c.candidate_id !== id).sort((a, b) => (a.queue_rank ?? 99) - (b.queue_rank ?? 99))
  const me = list.find((c) => c.candidate_id === id)
  return sel.find((c) => (c.queue_rank ?? 99) > (me?.queue_rank ?? 0)) ?? sel[0] ?? null
}

export default function CandidateDetail() {
  const { id = '' } = useParams()
  const { city, as_of, query } = useFilters()
  const [cand, setCand] = useState<Candidate | null>(null)
  const [list, setList] = useState<Candidate[]>([])
  const [candErr, setCandErr] = useState<ApiError | null>(null)
  const [runsErr, setRunsErr] = useState<ApiError | null>(null)
  const [startErr, setStartErr] = useState<ApiError | null>(null)
  const [lookupTick, setLookupTick] = useState(0)
  const [runId, setRunId] = useState<string | null>(null)
  const [lookedUp, setLookedUp] = useState(false)
  const [starting, setStarting] = useState(false)
  const { run, error: runErr, retrying, refresh } = useRunPoll(runId)
  // 근거·보고서·자원·이력 불러오기 오류 (조용히 비우지 않고 보임, FE-01)
  const [loadErr, setLoadErr] = useState<{ part: string; error: ApiError } | null>(null)
  const [loadTick, setLoadTick] = useState(0)
  const [previewErr, setPreviewErr] = useState<ApiError | null>(null)
  const [previewTick, setPreviewTick] = useState(0)
  const [approverShown, setApproverShown] = useState<string | null>(null)
  // 조사 시작·검토 저장은 검토 권한 이상 (API 명세 §1). 공용 토큰은 기본 조회라 잠그고 이유를 보임
  const { can } = useRole()
  const canReview = can('검토')
  const [card, setCard] = useState<EvidenceCard | null>(null)
  const [report, setReport] = useState<ReportResponse | null>(null)
  const [history, setHistory] = useState<RunHistoryResponse | null>(null)
  const [resources, setResources] = useState<ResourceRecommendation[] | null>(null)
  const [resourceScope, setResourceScope] = useState<'run' | 'global' | null>(null)
  const [resourceTotal, setResourceTotal] = useState<number | null>(null) // 전체 자원 조회의 total (미리 보기)
  const [resourceNeeds, setResourceNeeds] = useState<{ needTypes: string[]; scopeTotal: number | null }>({ needTypes: [], scopeTotal: null })
  const [monitoringRunId, setMonitoringRunId] = useState<string | null>(null) // 이 후보를 읽은 정기 감시 실행
  const [hl, setHl] = useState<string | null>(null)
  // 검토 편집값은 실행 ID 와 함께 둔다. 지금 실행과 다른 실행의 값이면 빈 값으로 본다
  const [editState, setEditState] = useState<EditState>(() => emptyEdit(null))
  const runRef = useRef(runId)
  runRef.current = runId
  const edit = editState.runId === runId ? editState : emptyEdit(runId)
  const { draft, memo, followUp, checked } = edit
  const dirty = edit.touched // 저장 뒤 화면에서 바꾼 것이 있나
  /** forRun 실행의 편집값만 고침. 그 사이 다른 실행으로 바뀌었으면 아무것도 안 함 */
  const patchEdit = useCallback((forRun: string | null, f: (e: EditState) => Partial<EditState>) => {
    if (forRun !== runRef.current) return
    setEditState((s) => {
      const base = s.runId === forRun ? s : emptyEdit(forRun)
      return { ...base, ...f(base) }
    })
  }, [])
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const [savedNow, setSavedNow] = useState(false)
  const [showPath, setShowPath] = useState(false)
  const [printView, setPrintView] = useState(false)
  useFooterRun(runId)

  // 후보와 기존 실행 찾기
  useEffect(() => {
    const ac = new AbortController()
    const { signal } = ac
    // 후보를 바꾸면 이전 후보의 요약·오류를 바로 지움 (FE-14)
    setLookedUp(false)
    setRunId(null)
    setPrintView(false)
    setCand(null)
    setCandErr(null)
    setRunsErr(null)
    setStartErr(null)
    const asErr = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '알 수 없는 오류가 생겼습니다.'))
    Promise.all([
      api.candidates(as_of, city, undefined, undefined, { signal }).catch(asErr),
      api.runs(id, 1, { signal }).catch(asErr),
    ]).then(([cl, runs]) => {
      if (signal.aborted) return
      if (cl instanceof ApiError) {
        if (!isAborted(cl)) setCandErr(cl)
      } else {
        setList(cl.items)
        setCand(cl.items.find((c) => c.candidate_id === id) ?? null)
        setMonitoringRunId(cl.monitoring_run_id ?? null)
      }
      if (runs instanceof ApiError) {
        // 기존 실행을 못 찾았는데 "조사 시작"을 보이면 같은 후보에 실행이 겹침 → 오류로 보이고 다시 시도
        if (!isAborted(runs)) setRunsErr(runs)
        return
      }
      const prev = runs.items[0]
      if (prev) setRunId(prev.run_id)
      setLookedUp(true)
    })
    return () => ac.abort()
  }, [id, as_of, city, lookupTick])

  const candidate = cand ?? run?.candidates.find((c) => c.candidate_id === id) ?? null

  // 자원: 실행이 있으면 그 실행에 묶인 목록(GET /runs/{id}/resources, 아래), 없으면 전체 자원 조회
  useEffect(() => {
    if (!lookedUp || runId || !candidate) return
    let alive = true
    const ac = new AbortController()
    setPreviewErr(null)
    const adm = candidate.adm_cd ?? candidate.check_first_adm[0] ?? null
    api
      .resources({
        adm_cd: adm,
        group: groupOf(candidate),
        city: candidate.city,
        limit: PREVIEW_LIMIT,
        ...(candidate.cause_type ? { cause_type: candidate.cause_type } : {}),
      }, { signal: ac.signal })
      .then((r) => {
        if (!alive) return
        setResources(r.items)
        setResourceTotal(r.total ?? r.items.length)
        setResourceNeeds({ needTypes: r.need_types ?? [], scopeTotal: r.scope_total ?? null })
        setResourceScope('global')
      })
      .catch((e) => {
        // 실패를 "맞는 자원 없음"으로 보이지 않게 오류로 (FE-01)
        if (alive && !isAborted(e)) setPreviewErr(e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '자원 미리 보기를 불러오지 못했습니다.'))
      })
    return () => {
      alive = false
      ac.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate?.candidate_id, runId, lookedUp, previewTick])

  // 다른 실행으로 바뀌면 이전 실행의 근거·보고서를 지움
  useEffect(() => {
    setCard(null)
    setReport(null)
    setHistory(null)
    setSavedNow(false)
    setSaveMsg(null)
    setEditState(emptyEdit(runId))
    setHl(null)
    setLoadErr(null)
    setApproverShown(null)
    if (runId) {
      setResources(null)
      setResourceScope(null)
    }
  }, [runId])

  // 실행이 끝나면 근거·보고서·이력. 상태가 바뀌면 이전 요청의 늦은 응답은 버림 (alive)
  const readyKey = run && !BUSY.has(run.status) ? `${run.status}-${run.report_revision}-${run.published_report_revision}-${run.report_stale}` : null
  useEffect(() => {
    if (!runId || !readyKey) return
    let alive = true
    const ac = new AbortController()
    const o = { signal: ac.signal }
    setLoadErr(null)
    const fail = (part: string) => (e: unknown) => {
      if (!alive || isAborted(e)) return
      setLoadErr({ part, error: e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '알 수 없는 오류가 생겼습니다.') })
    }
    api
      .evidence(runId, o)
      .then((e) => {
        if (alive) setCard(e.evidence_card)
      })
      .catch(fail('근거'))
    api
      .report(runId, o)
      .then((r) => {
        if (alive && r.run_id === runId) setReport(r)
      })
      .catch(fail('보고서'))
    api
      .runResources(runId, o)
      .then((r) => {
        if (!alive || r.run_id !== runId) return
        setResources(uniqueResources(r.items, id))
        setResourceScope('run')
      })
      .catch((e) => {
        // 아직 실행 중(RUN_NOT_READY)이면 다음 상태 변화 때 다시 부름
        if (e instanceof ApiError && e.code === 'RUN_NOT_READY') return
        fail('추천 자원')(e)
      })
    api
      .history(runId, o)
      .then((h) => {
        if (!alive || runRef.current !== runId) return
        setHistory(h)
        // 이 실행의 저장 검토로 복원. 같은 실행에서 고치던 값은 새 보고서 판이 와도 둠
        const saved = savedEdit(runId, h)
        setEditState((s) => applySaved(s.runId === runId ? s : emptyEdit(runId), saved))
      })
      .catch(fail('검토·승인 이력'))
    return () => {
      alive = false
      ac.abort()
    }
  }, [runId, readyKey, loadTick])

  /** 이력만 다시 (저장·승인 뒤). 실행이 바뀌었으면 버림. 실패해도 처리 안 된 오류가 나지 않게 */
  const reloadHistory = (forRun: string) =>
    api
      .history(forRun)
      .then((h) => {
        if (runRef.current === forRun) setHistory(h)
      })
      .catch((e) => {
        if (runRef.current === forRun && !isAborted(e)) setLoadErr({ part: '검토·승인 이력', error: e as ApiError })
      })

  // 화면에 실제로 띄운 보고서. 승인·검토는 이 판으로만 보낸다
  const shown: ShownReport =
    report && report.run_id === runId
      ? { run_id: report.run_id, snapshot_id: report.snapshot_id, report_revision: report.report.report_revision }
      : null

  const start = async () => {
    setStarting(true)
    setStartErr(null)
    try {
      // 화면이 읽은 정기 감시 실행과 도시를 같이 보냄 (그 실행의 바로 이 후보로 조사, API 명세 §3.6)
      const acc = await api.createRun({
        mode: 'qa',
        candidate_id: id,
        as_of,
        analysis_mode: 'simulated_replay',
        city: candidate?.city ?? city,
        ...(cand && monitoringRunId ? { monitoring_run_id: monitoringRunId } : {}),
      })
      setCard(null)
      setReport(null)
      setHistory(null)
      setRunId(acc.run_id)
    } catch (e) {
      setStartErr(e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', '조사를 시작하지 못했습니다.'))
    } finally {
      setStarting(false)
    }
  }

  // 이 실행에 묶인 추천 자원만 판정 대상 (다른 실행·전체 조회 자원의 판정은 보내지 않음)
  const runResourceIds = useMemo(
    () => new Set(resourceScope === 'run' && resources ? resources.map((r) => r.resource_id) : []),
    [resources, resourceScope],
  )
  const judgements = Object.entries(draft).filter(([rid, v]) => v.judgement && runResourceIds.has(rid))
  const canSave = !!card && resourceScope === 'run' && resources !== null

  const saveReview = async () => {
    if (!runId || !canSave || !card) return
    const forRun = runId
    setSaveMsg(null)
    try {
      const r = await api.review(forRun, {
        reviewer: REVIEWER_NAME,
        // 지금 근거 카드에 있는 ID만 (추가 조사로 빠진 근거는 보내지 않음, EVIDENCE_NOT_IN_RUN)
        checked_evidence_ids: Array.from(checked).filter((eid) => card.evidence.some((e) => e.evidence_id === eid)),
        resource_judgements: judgements.map(([rid, v]) => ({ resource_id: rid, judgement: v.judgement!, memo: v.memo || null })),
        memo: memo || null,
        follow_up_on: followUp || null,
        report_revision: shown?.report_revision ?? null,
      })
      if (runRef.current !== forRun) return // 저장하는 사이 다른 실행으로 바뀜
      patchEdit(forRun, () => ({ touched: false }))
      setSavedNow(true)
      setSaveMsg(
        r.changed
          ? `검토를 저장했습니다. 자원 판정이 바뀌어 보고서를 ${r.report_revision}판으로 다시 만듭니다.`
          : r.report_stale
            ? `검토를 저장했습니다. 보고서 ${r.report_revision}판을 아직 만드는 중입니다.`
            : '검토를 저장했습니다. 보고서는 그대로입니다.',
      )
      refresh()
      reloadHistory(forRun)
    } catch (e) {
      if (runRef.current !== forRun) return
      setSaveMsg(e instanceof ApiError ? `${errorText(e.code, e.message).title} (오류 코드 ${e.code})` : '저장하지 못했습니다.')
    }
  }

  const decide = async (d: Decision, m: string | null, focus?: MoreFocus | null) => {
    if (!run || !runId) return
    // 보내는 판 = 화면에 띄운 보고서의 판. 지금 실행 상태와 다르면 보내지 않음
    const block = approvalBlock(run, shown)
    if (block || !shown) throw new ApiError(409, 'REPORT_NOT_SHOWN', block ?? '보고서를 불러온 뒤 승인하세요.')
    const rev = shown.report_revision
    const res = await api.approve(runId, {
      decision: d,
      report_revision: rev,
      approver: APPROVER_NAME,
      idempotency_key: `${runId}:${d}:${rev}`,
      memo: m,
      ...(d === 'more' && focus ? { focus } : {}),
    })
    // 승인자는 서버가 기록한 이름으로 보임 (인증을 켜면 토큰의 이름). 응답에 없으면 이력에서 (FE-16)
    if (res?.approver && runRef.current === runId) setApproverShown(res.approver)
    // 이력(승인자·시각)을 먼저 받아 두고 상태를 바꿔, 완료 카드가 승인자 없이 잠깐 뜨지 않게
    await reloadHistory(runId)
    await refresh()
  }

  const toggleChecked = (eid: string) => {
    patchEdit(runId, (e) => {
      const n = new Set(e.checked)
      if (n.has(eid)) n.delete(eid)
      else n.add(eid)
      return { checked: n, touched: true }
    })
  }

  // 이어 묻기 답의 근거 ID → 근거 표 그 행으로
  const cite = (eid: string) => {
    setHl(eid)
    document.getElementById(eid)?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }

  const openPrint = () => {
    setPrintView(true)
    window.scrollTo?.({ top: 0 })
  }

  const cv = candidate?.counter_result ? counterView(candidate) : null
  const names = useMemo(() => Object.fromEntries((resources ?? []).map((r) => [r.resource_id, r.name])), [resources])
  const ready = run && !BUSY.has(run.status)
  const rep = report?.report ?? null
  const actions = rep?.actions?.filter((a) => a.candidate_id === id) ?? []
  const shownActions = actions.length > 0 ? actions : (rep?.actions ?? [])
  const exp = rep?.level_explanations?.find((e) => e.candidate_id === id) ?? null
  const trace = run?.decision_trace?.length ? run.decision_trace : (rep?.decision_trace ?? [])
  const roundLines = rep?.round_changes ?? []
  const facts = rep ? rep.fact_sentences.filter((s) => !roundLines.includes(s) && !isActionSentence(s, shownActions.map((a) => a.action_plan?.text ?? ''))) : []
  const rounds = roundLines.length > 0 ? roundChangesView(roundLines) : null
  const regen = run ? regenerating(run) : false
  const moreCandidates = (run?.candidates ?? []).map((c) => ({ candidate_id: c.candidate_id, label: `${c.candidate_level ?? '등급 전'} · ${candidatePlace(c)}` }))
  const agentError = run?.status === 'failed' ? (run.errors.map((e) => e.error_code).find(Boolean) ?? 'AGENT_ERROR') : null
  const reviewSaved = !dirty && (savedNow || (history?.reviews.length ?? 0) > 0)
  const checks = card
    ? preChecks({
        evidenceIds: card.evidence.map((e) => e.evidence_id),
        checked,
        resourceCount: resources?.length ?? 0,
        judgedCount: judgements.length,
        reviewSaved,
      })
    : undefined
  const lastApproval = history?.approvals.filter((a) => a.decision === 'approved').at(-1) ?? null
  const next = nextAssigned(list, id)
  const nextHref = next ? `/candidates/${next.candidate_id}${query}` : `/candidates${query}`
  const lastReview = history?.reviews.at(-1) ?? null
  const printJudgements: PrintJudgement[] = (lastReview?.resource_judgements ?? []).map((j) => ({ name: names[j.resource_id] ?? j.resource_id, judgement: j.judgement, memo: j.memo ?? null }))
  const adm = candidate?.adm_cd ?? candidate?.check_first_adm[0] ?? null
  const evIdx = useMemo(() => evidenceIndex(card?.evidence ?? []), [card])
  const refName = (id: string) => { const e = evIdx.get(id); return e ? `근거 ${e.n}` : undefined }
  const refFull = (id: string) => { const e = evIdx.get(id); return e ? `근거 ${e.n} · ${e.name}` : undefined }
  // 승인자: 서버 이력의 기록이 먼저, 이력을 못 받았으면 승인 응답의 이름 (FE-16)
  const doneApproval =
    lastApproval ?? (approverShown && run?.status === 'approved' ? { report_revision: run.report_revision ?? 0, approver: approverShown, created_at: new Date().toISOString() } : null)
  const printSheet =
    card && rep && runId ? (
      <PrintReport
        candidate={candidate}
        card={card}
        report={rep}
        runId={runId}
        snapshotId={report!.snapshot_id}
        judgements={printJudgements}
        resourceNames={names}
        approval={history?.approvals.at(-1) ?? null}
        followUp={lastReview?.follow_up_on ?? null}
      />
    ) : null

  if (printView && printSheet)
    return (
      <>
        <PageHero title="인쇄용 보기" />
        <PageBody>
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-card bg-surface px-5 py-3 shadow-card print:hidden">
            <span className="text-sm text-ink-600">결재·보고용 한 장입니다. PDF는 브라우저 인쇄에서 "PDF로 저장"을 고르세요.</span>
            <div className="flex gap-2">
              <button className="btn-ghost" onClick={() => setPrintView(false)}>
                ← 상세로 돌아가기
              </button>
              <button className="btn-primary" onClick={() => window.print()}>
                인쇄
              </button>
            </div>
          </div>
          {printSheet}
        </PageBody>
      </>
    )

  return (
    <>
      <PageHero title="후보 상세" sources={['candidates', 'runs', 'resources']}>
        <Link to={`/candidates${query}`} className="text-sm text-white hover:text-white">
          ← 후보 목록
        </Link>
      </PageHero>
      <PageBody>
        {/* 인쇄 단축키(Ctrl+P)로 찍어도 보고서 한 장만 나가게 */}
        {printSheet && <div className="hidden print:block">{printSheet}</div>}
        <div className="flex flex-col gap-5 print:hidden">
          {candErr && <ErrorBox error={candErr} onRetry={() => setLookupTick((t) => t + 1)} />}
          {runsErr && (
            <div data-testid="runs-error">
              <ErrorBox error={runsErr} onRetry={() => setLookupTick((t) => t + 1)} />
            </div>
          )}
          {!lookedUp && !runsErr && (
            <Card>
              <Loading label="후보와 지난 조사를 찾는 중" onRetry={() => setLookupTick((t) => t + 1)} />
            </Card>
          )}
          {lookedUp && !candidate && !run && (
            <Card>
              <Empty>
                {city} · {as_of} 목록에 {id} 후보가 없습니다. 기준일을 바꾸거나 이 후보로 조사를 시작해 보세요.
              </Empty>
            </Card>
          )}

          {/* ① 요약: 등급·이유·할 수 있는 말 */}
          {candidate && (
            <section className="card p-5 sm:p-6" data-testid="summary">
              <div className="flex flex-wrap items-center gap-2">
                <LevelBadge level={candidate.candidate_level} withName />
                <Term name="등급" />
                <TypeChip type={candidate.candidate_type} />
                <InputStatusBadge status={candidate.input_status} />
                <SelectedBadge c={candidate} />
                {run && (
                  <span className="ml-auto text-xs text-ink-600" data-testid="run-line">
                    {runLine(run)}
                  </span>
                )}
              </div>
              <h2 className="mt-3 text-xl font-bold">{`${candidatePlace(candidate)} · ${groupOf(candidate) ?? ''}`}</h2>
              <div className="mt-2 text-base text-ink-900">
                <b>왜 이 후보인가</b> · {candidate.level_reason ? plainText(candidate.level_reason) : '—'}
                {candidate.trigger && ` · 트리거 ${candidate.trigger}`}
                {candidate.heat_stage && (
                  <>
                    {' · '}
                    <HeatStageLabel stage={candidate.heat_stage} />
                  </>
                )}
              </div>
              {/* 등급 조건 표가 있으면 그 아래 한 번만 보임 */}
              {!exp && !(candidate.rule_checks?.length ?? 0) && mentionsHub(candidate.level_reason ?? '') && (
                <div className="mt-1 text-xs text-ink-600" data-testid="hub-note">
                  {HUB_NOTE}
                </div>
              )}
              <div className="mt-1 text-xs text-ink-600">
                <Term name="분석 집단">분석 집단</Term> {candidate.analysis_cohort} · 서비스 대상 {candidate.service_eligibility_group ?? '—'}
                {candidate.check_first_adm.length > 0 && (
                  <>
                    {' '}
                    · 먼저 확인할 동(<Term>공유 맥락</Term>): {candidate.check_first_adm.map(admName).join(', ')}
                  </>
                )}
                {adm && (
                  <>
                    {' '}
                    ·{' '}
                    <Link className="font-semibold text-brand-700 hover:underline" to={`/map${query}&sel=${adm}`}>
                      지도에서 보기
                    </Link>
                  </>
                )}
              </div>
              {candidate.candidate_level === '판단 보류' && (
                <div className="mt-4">
                  <HoldNotice
                    title="판단 보류: 근거가 부족해 등급을 정하지 않았습니다"
                    reasons={[...(candidate.counter_reason ? [candidate.counter_reason] : []), ...candidate.missing_inputs.map((m) => `빠진 근거: ${missingLabel(m)}`)]}
                  />
                </div>
              )}
              {candidate.candidate_level !== '판단 보류' && candidate.missing_inputs.length > 0 && (
                <div className="mt-2 text-sm text-caution">빠진 근거: {candidate.missing_inputs.map(missingLabel).join(', ')}</div>
              )}
              {(exp || (candidate.rule_checks?.length ?? 0) > 0) && (
                <div className="mt-4">
                  <LevelExplain exp={exp} checks={candidate.rule_checks} nameOf={admName} ruleId={candidate.rule_id} ruleVersion={candidate.rule_version} />
                </div>
              )}
              {(candidate.event_timeline?.length ?? 0) > 0 && !shownActions.some((a) => (a.action_plan?.holiday_timeline?.length ?? 0) > 0) && (
                <div className="mt-4">
                  <EventTimeline steps={candidate.event_timeline} bundleId={candidate.event_bundle_id} />
                </div>
              )}
              {card && <StructuralRisk items={card.structural_risk} candidateId={candidate.candidate_id} />}
              {(candidate.context?.length ?? 0) > 0 && (
                <div className="mt-4">
                  <CandidateContext items={candidate.context} onCite={runId ? cite : undefined} refName={refName} />
                </div>
              )}
              {cv && candidate.candidate_type === 'unexpected_activity_deviation' && (
                <div className="mt-4 flex flex-wrap items-center gap-2" data-testid="counter-summary">
                  <span className="text-xs font-semibold text-ink-600">
                    <Term>다른 원인 확인</Term>
                  </span>
                  <CounterChip c={candidate} />
                  <span className="text-ink-800">{cv.reason}</span>
                  {cv.tone === 'check' && <div className="w-full text-xs text-ink-600">도시 공통 하락은 공휴일이 원인이라는 증거가 아닙니다. 평소 수준으로 돌아오는지 더 지켜봅니다.</div>}
                  {cv.tone === 'review' && <div className="w-full text-xs text-ink-600">회복 판정 기간이 끝나면 다시 판정합니다.</div>}
                  {cv.tone === 'reject' && <div className="w-full text-xs text-ink-600">기각된 경보도 Level 1로 남겨 사유를 기록합니다.</div>}
                </div>
              )}
              {ready && card && (
                <div className="mt-4">
                  <ClaimsBox card={card} />
                </div>
              )}
            </section>
          )}

          {/* 보호 자원 칸(낙인 완화) + 이의·정정 기록 (윤리기준 사후관리·피해구제) */}
          {candidate && (
            <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2" data-testid="protective-objection">
              <ProtectivePanel city={candidate.city} admCd={adm} />
              <ObjectionForm city={candidate.city} candidateId={candidate.candidate_id} admCd={adm} runId={runId} />
            </div>
          )}

          {lookedUp && !runId && (
            <Card title="조사 실행">
              <p className="mb-3 text-ink-600">이 후보로 만든 실행이 없습니다. 조사를 시작하면 근거 카드·자원·보고서를 만들고 승인 대기에서 멈춥니다.</p>
              <button className="btn-primary" onClick={start} disabled={starting || !canReview}>
                {starting ? '시작하는 중…' : '조사 시작'}
              </button>
              {!canReview && (
                <div className="mt-2">
                  <RoleNote need="검토" />
                </div>
              )}
              {startErr && (
                <div className="mt-3">
                  <ErrorBox error={startErr} onRetry={start} />
                </div>
              )}
              {previewErr && (
                <div className="mt-4" data-testid="preview-error">
                  <ErrorBox error={previewErr} onRetry={() => setPreviewTick((t) => t + 1)} />
                </div>
              )}
              {!previewErr && resourceScope === 'global' && resources && (
                <ResourcePreview items={resources} total={resourceTotal} needTypes={resourceNeeds.needTypes} scopeTotal={resourceNeeds.scopeTotal} />
              )}
            </Card>
          )}

          {runErr && <ErrorBox error={runErr} onRetry={refresh} />}
          {retrying > 0 && !runErr && (
            <div className="rounded-xl bg-caution-soft px-3 py-2 text-sm text-ink-900" role="status" data-testid="run-retrying">
              API 연결이 잠깐 끊겨 조사 상태를 다시 묻는 중입니다 ({retrying}번째). 조사는 서버에서 계속됩니다.
            </div>
          )}
          {loadErr && (
            <div data-testid="load-error">
              <ErrorBox
                error={new ApiError(loadErr.error.status, loadErr.error.code, `${loadErr.part}을(를) 불러오지 못했습니다. ${loadErr.error.message}`)}
                onRetry={() => setLoadTick((t) => t + 1)}
              />
            </div>
          )}
          {agentError && (
            <div className="rounded-2xl border border-caution/40 bg-caution-soft px-4 py-3 text-ink-900" role="alert" data-testid="run-failed">
              <div className="font-semibold">{ERROR_TEXT[agentError] ?? ERROR_TEXT.AGENT_ERROR}</div>
              <div className="mt-1 text-xs text-ink-600">오류 코드 {agentError}</div>
              <button className="btn-primary mt-3" onClick={start} disabled={starting || !canReview}>
                새 실행으로 다시 조사
              </button>
            </div>
          )}
          {run && <TimeLabelBar label={card?.time_label} />}

          {run && BUSY.has(run.status) && (
            <Card>
              <Loading label={`근거를 모으고 검사하는 중 (${run.path.length}단계 지남)`} onRetry={refresh} slowText={RUN_SLOW_TEXT} stuckText={RUN_SLOW_TEXT} />
            </Card>
          )}

          {ready && card && (
            <>
              {/* ② 근거 */}
              <Card
                title="근거"
                right={
                  <span className="text-xs text-ink-600">
                    확인한 근거 {card.evidence.filter((e) => checked.has(e.evidence_id)).length} / {card.evidence.length} · 근거 {card.evidence_revision}판 · 기준일 {card.as_of}
                  </span>
                }
              >
                <EvidenceTable card={card} checked={checked} onToggle={toggleChecked} highlight={hl} />
                {card.limitations.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {card.limitations.map((l) => (
                      <Badge key={l}>{limitText(reportText(l, names))}</Badge>
                    ))}
                  </div>
                )}
              </Card>

              {/* ③ 자원 판정 */}
              <Card
                title="추천 자원과 담당자 판정"
                right={
                  resourceScope === 'run' ? (
                    <span className="text-xs text-ink-600" data-testid="resource-scope">
                      이 조사에 묶인 목록 · 스냅샷 {run.snapshot_id}
                    </span>
                  ) : undefined
                }
              >
                {resources === null ? (
                  <Loading />
                ) : (
                  <ResourcePanel
                    items={resources}
                    draft={draft}
                    disabled={run.status !== 'awaiting_approval'}
                    onChange={(rid, v) => patchEdit(runId, (e) => ({ draft: { ...e.draft, [rid]: v }, touched: true }))}
                  />
                )}
                <div className="mt-4 grid gap-3 md:grid-cols-[1fr_200px]">
                  <textarea
                    className="field min-h-[70px]"
                    placeholder="담당자 메모 (예: 남면 이장님께 먼저 연락)"
                    aria-label="담당자 메모"
                    value={memo}
                    onChange={(e) => {
                      const v = e.target.value
                      patchEdit(runId, () => ({ memo: v, touched: true }))
                    }}
                  />
                  <label className="flex flex-col gap-1 text-xs text-ink-600">
                    후속 확인일
                    <input
                      type="date"
                      className="field"
                      value={followUp}
                      onChange={(e) => {
                        const v = e.target.value
                        patchEdit(runId, () => ({ followUp: v, touched: true }))
                      }}
                      aria-label="후속 확인일"
                    />
                  </label>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <button className="btn-primary" onClick={saveReview} disabled={run.status !== 'awaiting_approval' || !canSave || !canReview}>
                    검토 저장
                  </button>
                  {!canReview && <RoleNote need="검토" />}
                  {dirty && <span className="text-xs text-caution">저장하지 않은 변경이 있습니다</span>}
                  {saveMsg && (
                    <span className="text-sm text-brand-700" role="status">
                      {saveMsg}
                    </span>
                  )}
                </div>
              </Card>

              {/* ④ 대응안·보고서 + ⑤ 승인 (넓은 화면에서는 승인이 오른쪽에 따라옴) */}
              <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
                <div className="flex min-w-0 flex-col gap-5">
                  {shownActions.length > 0 && (
                    <Card title="대응안 · 내일 아침 체크리스트" right={<Badge tone="dark">{rep?.distribution ?? DISTRIBUTION_INTERNAL}</Badge>}>
                      <div className="flex flex-col gap-5">
                        {shownActions.map((a) => (
                          <div key={a.candidate_id} className="flex flex-col gap-4">
                            <MorningChecklistView a={a} />
                            <div>
                              <div className="mb-2 text-xs font-semibold text-ink-600">대응안 5칸</div>
                              <ActionPlanView a={a} />
                            </div>
                            <CheckAreasView a={a} />
                          </div>
                        ))}
                      </div>
                    </Card>
                  )}
                  {report && rep && (
                    <Card
                      title={`보고서 ${rep.report_revision}판`}
                      right={
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded border-2 border-ink-900 px-2 py-0.5 text-xs font-black" data-testid="distribution-mark">
                            <Term name="대외비">{rep.distribution ?? DISTRIBUTION_INTERNAL}</Term>
                          </span>
                          {(report.report_stale || regen) && (
                            <span data-testid="report-regenerating">
                              <Badge tone="caution">보고서 다시 만드는 중 ({rep.report_revision}판 표시)</Badge>
                            </span>
                          )}
                          <button className="btn-ghost py-1" onClick={openPrint}>
                            인쇄용 보기
                          </button>
                        </div>
                      }
                    >
                      {rounds && (
                        <div className="mb-4 rounded-xl border border-brand-100 bg-brand-50 px-3 py-2" data-testid="round-changes">
                          <div className="text-xs font-semibold text-brand-700">{rounds.title}</div>
                          <ul className="mt-1 list-disc pl-5 text-ink-900">
                            {rounds.items.map((s) => (
                              <li key={s}>{reportText(s, names)}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <div className="text-xs font-semibold text-ink-600">핵심 사실 (근거 카드에서 만든 문장 · 근거 이름을 누르면 근거 표로)</div>
                      <ul className="mt-1 list-disc pl-5" data-testid="facts">
                        {facts.map((s) => (
                          <li key={s}>
                            <RefText text={s} idx={evIdx} names={names} onRef={cite} />
                          </li>
                        ))}
                      </ul>
                      {rep.narrative && (
                        <>
                          <div className="mt-3 text-xs font-semibold text-ink-600">
                            설명 (<Term>사실 검사</Term> 통과)
                          </div>
                          <p className="mt-1 text-ink-800">
                            <RefText text={rep.narrative} idx={evIdx} names={names} onRef={cite} />
                          </p>
                        </>
                      )}
                      {(rep.investigation?.items.length ?? 0) > 0 && (
                        <div className="mt-4">
                          <ExtraChecks data={rep.investigation} onCite={cite} refName={refName} />
                        </div>
                      )}
                      {rep.external_notice && (
                        <div className="mt-4 rounded-xl bg-canvas px-3 py-2" data-testid="external-notice">
                          <div className="text-xs font-semibold text-ink-600">밖으로 안내할 때는 이 문구만 (동 이름·집단은 내보내지 않음)</div>
                          <div className="mt-0.5 text-ink-900">{rep.external_notice}</div>
                        </div>
                      )}
                    </Card>
                  )}
                  {report && rep && runId && (
                    <Card title="이어 묻기">
                      <FollowupPanel key={runId} runId={runId} onCite={cite} refName={refFull} candidates={run?.candidates ?? []} />
                    </Card>
                  )}
                  <Card title="보고서 판 이력">
                    {history && history.report_revisions.length > 0 ? (
                      <ol className="flex flex-col gap-2 text-sm">
                        {history.report_revisions.map((r) => (
                          <li key={r.report_revision} className="flex items-center justify-between gap-2">
                            <span>
                              <b>{r.report_revision}판</b> · {REVISION_REASON_LABEL[r.reason] ?? r.reason}
                            </span>
                            <span className="text-xs text-ink-600">{fmtTime(r.created_at)}</span>
                          </li>
                        ))}
                        {regen && <li className="text-caution">{run.report_revision}판 만드는 중…</li>}
                      </ol>
                    ) : (
                      <Empty>이력 없음</Empty>
                    )}
                    {history && history.approvals.length > 0 && (
                      <div className="mt-3 border-t border-line pt-3 text-xs text-ink-600">
                        {history.approvals.map((a) => (
                          <div key={a.approval_id} title={`기록 ID ${a.approval_id}`}>
                            {DECISION_LABEL[a.decision] ?? a.decision} · {a.report_revision}판 · <PersonName name={a.approver} /> · {fmtTime(a.created_at)}
                          </div>
                        ))}
                      </div>
                    )}
                  </Card>
                </div>
                <div className="xl:sticky xl:top-4 xl:max-h-[calc(100vh-2rem)] xl:self-start xl:overflow-y-auto">
                  <Card title="승인">
                    <ApprovalPanel
                      run={run}
                      shown={shown}
                      onDecide={decide}
                      checks={checks}
                      done={doneApproval}
                      onPrint={printSheet ? openPrint : undefined}
                      nextHref={nextHref}
                      moreCandidates={moreCandidates}
                      moreDefaultIds={[id]}
                      outcome={runId ? <OutcomeForm runId={runId} candidateId={id} /> : undefined}
                    />
                  </Card>
                </div>
              </div>
            </>
          )}

          {/* 실행 상태: 한 줄만, 단계는 접어 둠 */}
          {run && (
            <Card
              title="조사 상태"
              right={
                <div className="flex flex-wrap gap-2">
                  <button className="btn-ghost py-1" aria-expanded={showPath} onClick={() => setShowPath((v) => !v)}>
                    {showPath ? '에이전트 경로 접기' : '에이전트 경로 보기'}
                  </button>
                  {ready && run.status !== 'awaiting_approval' && (
                    <button className="btn-ghost py-1" onClick={start} disabled={!canReview}>
                      새 실행으로 다시 조사
                    </button>
                  )}
                </div>
              }
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={BUSY.has(run.status) ? 'blue' : 'dark'}>{PHASE_LABEL[run.status]}</Badge>
                <span className="text-ink-800">{runLine(run)}</span>
              </div>
              {showPath && (
                <div className="mt-4 flex flex-col gap-4" data-testid="agent-path">
                  <RunProgress run={run} />
                  <div>
                    <div className="mb-1 text-xs font-semibold text-ink-600">판단 기록 (단계마다 무엇을 골랐고 누가 정했나)</div>
                    <DecisionTrace steps={trace} onCite={cite} hideExtra={(rep?.investigation?.items.length ?? 0) > 0} />
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
      </PageBody>
    </>
  )
}
