// 결재용 인쇄 보기. 근거 + 할 수 있는 말 + 자원 판정 + 대응안·체크리스트 + 승인자·판·스냅샷 + 고지, 대외비 표시.
import type { ReactNode } from 'react'
import type { Candidate, EvidenceCard, Report } from '../api'
import { DISCLAIMER, DISTRIBUTION_INTERNAL } from '../api'
import {
  DECISION_LABEL,
  LEVEL_NAME,
  TYPE_LABEL,
  candidatePlace,
  evidenceIndex,
  fmtTime,
  fmtValue,
  groupOf,
  isActionSentence,
  refsToText,
  reportText,
  roundChangesView,
} from '../lib/labels'
import { ClaimsBox, evidenceLine } from './EvidenceTable'
import { ActionPlanView, MorningChecklistView } from './ReportParts'

export type PrintJudgement = { name: string; judgement: string; memo: string | null }
export type PrintApproval = { decision: string; approver: string; created_at: string; report_revision: number } | null

/** CSS content 문자열로 (따옴표·역슬래시·줄바꿈 이스케이프) */
function cssString(s: string): string {
  return `"${s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\r\n]+/g, ' ')}"`
}

/** 인쇄할 때 장마다 위·아래 여백에 대외비 표시와 쪽 번호 (Chromium 131+ @page 여백 상자, FE-08) */
export function pageMarginCss(distribution: string): string {
  const d = cssString(distribution)
  return `@media print { @page { size: A4; margin: 18mm 14mm 18mm 14mm;
  @top-right { content: ${d}; font-size: 9pt; font-weight: 700; color: #14171F; }
  @bottom-right { content: ${d} " · " counter(page) " / " counter(pages); font-size: 9pt; font-weight: 700; color: #14171F; }
  @bottom-left { content: "LINK-Radar 검토 보고서 · 개인 판정 아님"; font-size: 8pt; color: #4A5163; } } }`
}

export function PrintReport({
  candidate,
  card,
  report,
  runId,
  snapshotId,
  judgements,
  approval,
  followUp,
  resourceNames = {},
}: {
  candidate: Candidate | null
  card: EvidenceCard
  report: Report
  runId: string
  snapshotId: string
  judgements: PrintJudgement[]
  approval: PrintApproval
  followUp?: string | null
  /** 자원 코드(R0000) → 이름 */
  resourceNames?: Record<string, string>
}) {
  const distribution = report.distribution ?? DISTRIBUTION_INTERNAL
  const actions = report.actions ?? []
  const roundLines = report.round_changes ?? []
  const rounds = roundLines.length > 0 ? roundChangesView(roundLines) : null
  // 회차 비교 문장은 사실 목록에서 빼고 따로 (화면과 같게)
  const facts = report.fact_sentences.filter((s) => !roundLines.includes(s) && !isActionSentence(s, actions.map((a) => a.action_plan?.text ?? '')))
  const exp = report.level_explanations?.find((e) => e.candidate_id === candidate?.candidate_id) ?? report.level_explanations?.[0]
  const fake = snapshotId.startsWith('snap-fake')
  // 보고서 문장의 코드: 자원 코드 → 이름, 입력 코드 → 한국어, [EV-…] → "(근거 n)" (화면과 같은 함수, FE-09)
  const idx = evidenceIndex(card.evidence)
  const text = (s: string) => refsToText(reportText(s, resourceNames), idx)
  const meta: [string, string][] = [
    ['후보', candidate ? `${candidatePlace(candidate)} · ${groupOf(candidate) ?? candidate.service_eligibility_group ?? ''}` : card.title],
    ['등급', candidate?.candidate_level ? `${candidate.candidate_level}${candidate.candidate_level in LEVEL_NAME ? ` · ${LEVEL_NAME[candidate.candidate_level]}` : ''}` : '—'],
    ['종류 · 트리거', candidate ? `${TYPE_LABEL[candidate.candidate_type]}${candidate.trigger ? ` · ${candidate.trigger}` : ''}` : '—'],
    ['기준일', card.as_of],
    ['보고서 판', `${report.report_revision}판 (근거 ${card.evidence_revision}판)`],
    ['스냅샷', snapshotId],
    ['실행 ID', runId],
    ['결재', approval ? `${DECISION_LABEL[approval.decision] ?? approval.decision} · ${approval.approver} · ${fmtTime(approval.created_at)} · ${approval.report_revision}판` : '승인 전'],
  ]
  if (followUp) meta.push(['후속 확인일', followUp])

  return (
    <article className="print-sheet card relative p-6 text-ink-900 print:rounded-none print:p-0 print:shadow-none" data-testid="print-report">
      {/* 인쇄하면 장마다 위·아래 여백에 대외비 표시 (@page 여백 상자) */}
      <style>{pageMarginCss(distribution)}</style>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b-2 border-ink-900 pb-3">
        <div>
          <div className="text-xs text-ink-600">LINK-Radar 검토 보고서</div>
          <h1 className="text-lg font-bold">{card.title}</h1>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="rounded border-2 border-ink-900 px-2 py-0.5 text-sm font-black" data-testid="distribution-mark">
            {distribution}
          </span>
          {fake && <span className="text-xs font-semibold text-caution">가짜 데이터 (화면 확인용) · 분석 결과 아님</span>}
        </div>
      </header>

      <table className="mt-3 w-full text-sm">
        <tbody>
          {meta.map(([k, v]) => (
            <tr key={k} className="border-b border-line">
              <th scope="row" className="w-28 py-1 pr-3 text-left text-xs font-semibold text-ink-600">
                {k}
              </th>
              <td className="py-1">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <Section title="근거">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-ink-900 text-left text-xs text-ink-600">
              <th className="w-10 py-1 pr-2">번호</th>
              <th className="py-1 pr-2">근거와 뜻</th>
              <th className="py-1 pr-2">값</th>
              <th className="py-1 pr-2">관측 기간</th>

            </tr>
          </thead>
          <tbody>
            {card.evidence.map((e, i) => {
              const l = evidenceLine(e)
              return (
                <tr key={e.evidence_id} className="border-b border-line align-top">
                  <td className="py-1 pr-2 text-xs font-semibold">근거 {i + 1}</td>
                  <td className="py-1 pr-2">
                    <b>{l.head}</b>
                    {l.sub && <div className="text-xs">{l.sub}</div>}
                  </td>
                  <td className="whitespace-nowrap py-1 pr-2">{fmtValue(e.value, e.unit)}</td>
                  <td className="whitespace-nowrap py-1 pr-2 text-xs">{e.observed_from ? `${e.observed_from}~${e.observed_to}` : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {card.limitations.length > 0 && <div className="mt-1 text-xs text-ink-600">한계: {card.limitations.map(text).join(' · ')}</div>}
      </Section>

      <Section title="할 수 있는 말 / 하면 안 되는 말">
        <ClaimsBox card={card} fold={false} />
      </Section>

      {exp && exp.lines.length > 0 && (
        <Section title="등급을 정한 이유">
          <ul className="list-disc pl-5 text-sm">
            {exp.lines.map((l) => (
              <li key={l}>{text(l)}</li>
            ))}
          </ul>
        </Section>
      )}

      {rounds && (
        <Section title={rounds.title}>
          <ul className="list-disc pl-5 text-sm">
            {rounds.items.map((l) => (
              <li key={l}>{text(l)}</li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="핵심 사실">
        <ul className="list-disc pl-5 text-sm">
          {facts.map((s) => (
            <li key={s}>{text(s)}</li>
          ))}
        </ul>
        {report.narrative && <p className="mt-2 text-sm">{text(report.narrative)}</p>}
      </Section>

      <Section title="자원 판정 (담당자)">
        {judgements.length === 0 ? (
          <div className="text-sm text-ink-600">저장된 자원 판정이 없습니다.</div>
        ) : (
          <ul className="text-sm" data-testid="print-judgements">
            {judgements.map((j) => (
              <li key={j.name}>
                <b>{j.name}</b> · {j.judgement}
                {j.memo ? ` · ${j.memo}` : ''}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {actions.map((a) => (
        <Section key={a.candidate_id} title={`대응안 · ${a.where ?? a.candidate_id}`}>
          <ActionPlanView a={a} />
          <div className="mt-3">
            <MorningChecklistView a={a} />
          </div>
        </Section>
      ))}

      <footer className="mt-5 border-t-2 border-ink-900 pt-3 text-sm">
        {report.external_notice && (
          <div className="mb-2">
            <span className="text-xs font-semibold text-ink-600">밖으로 안내할 때 쓰는 문구</span>
            <div data-testid="external-notice">{report.external_notice}</div>
          </div>
        )}
        <div className="font-bold">{DISCLAIMER}</div>
        <div className="text-xs text-ink-600">
          집계 데이터로 먼저 확인할 동·집단을 제안합니다. 개인의 고립을 판정하지 않습니다. 이 문서는 {distribution}입니다. 동 이름과 집단을 밖으로 내보내지 않습니다.
        </div>
        <div className="mt-2 text-[10px] leading-tight text-ink-600" data-testid="evidence-id-key">
          근거 번호와 기록 ID: {card.evidence.map((e, i) => `근거 ${i + 1} = ${e.evidence_id}`).join(' · ')}
        </div>
      </footer>
    </article>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-4 break-inside-avoid-page">
      <h2 className="mb-1 border-b border-line pb-0.5 text-sm font-bold">{title}</h2>
      {children}
    </section>
  )
}
