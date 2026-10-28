'use client'

// Store detail - the "why" page. The narrative is shown as prose with its
// internal [rec:/event:/src:] citation markers stripped; the page never prints
// a citation id, a retrieval chunk or a raw engine token from the citation
// layer - the ONE deliberate exception is the served `recommendation_id`,
// which the product positions as citable provenance (PRODUCT.md) and which
// rides a quiet footer line under the evidence panel. Every figure is printed
// as served. No client math, no rewriting, GET only - including the "Ask"
// panel, whose advisory suggestion is an LLM reading of the same evidence,
// labelled a suggestion and never a decision (rag/advisor.py invariants).
import { use } from 'react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AppChrome, LoadingCard } from '../../_components/chrome'
import { actionLabel, api, display } from '../../_lib/api'
import type { AdvisoryResponse, Recommendation, WhyPayload } from '../../types'

const MARKER = /\s*\[(rec|event|src):[^\]]+\]/g

// Budget-friendly by construction: the three questions that cover most of a
// stakeholder's intent are one click each; the free-text field stays for
// follow-ups. Every ask is a budget-capped LLM call (per-IP hourly + daily
// cap), so presets keep the cap spent on real questions, not typos.
const ASK_PRESETS = [
  'Why does this store need a decision?',
  'What should I check before approving?',
  'What would change this decision?',
]

// `advisory_status` is served for the audit log, not for stakeholders - it
// names machine flags ("deterministic", "LLM_ADVISORY_ENABLED") the contract
// keeps off this page. Translate the known outcomes to human sentences;
// anything unrecognized still degrades honestly to "engine answer only".

// The markers are citation plumbing, not sentence content. Removing the leading
// whitespace keeps "0.62 [rec:…]." from becoming "0.62 .".
function narrative(text: unknown): string {
  return String(text || '').replace(MARKER, '')
}

// Contract rule: forecast_status is a first-class signal, not a machine token
// - ERROR reads "feed delayed", any other gap reads "no forecast signal".
function forecastText(status: unknown): string {
  return String(status || '').toUpperCase() === 'ERROR' ? 'feed delayed' : 'no forecast signal'
}

function Field({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === '') return null
  const long = String(value).length > 44
  return (
    <div className={long ? 'kv kv-long' : 'kv'}>
      <span>{label}</span>
      <strong>{display(value)}</strong>
    </div>
  )
}

// A guard pass is green; a degrade (LLM off/unavailable) is amber; anything
// else is red - the same fail-closed signal the API reports.
function Guard({ name, value }: { name: string; value: unknown }) {
  const text = String(value ?? '')
  const tone = text === 'passed' ? 'ok' : /unavailable|off/.test(text) ? 'warn' : ''
  return <span className={`badge ${tone}`}>{name}: {text}</span>
}

type Phase =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'waking' }  // 502/504: cold start, one 30s retry scheduled
  | { kind: 'missing' } // 404: this store id is not on the API
  | { kind: 'error' }   // anything else

type AskState =
  | { kind: 'idle' }
  | { kind: 'asking' }
  | { kind: 'done'; result: AdvisoryResponse }
  | { kind: 'error'; error: string }

function advisoryStatusLine(status: unknown): string {
  const text = String(status || '')
  if (/^llm-advisory/i.test(text)) return "LLM suggestion, grounded against this store's record."
  if (/no grounded evidence/i.test(text)) return 'Engine answer only - there is no grounded evidence for this store yet.'
  if (/budget/i.test(text)) return "Engine answer only - today's LLM advisory budget is spent."
  if (/off/i.test(text)) return 'Engine answer only - the LLM advisory layer is switched off on this deployment.'
  if (/grounding-failed/i.test(text)) return 'Engine answer only - the LLM suggestion failed its grounding guard and was refused.'
  if (/unavailable/i.test(text)) return 'Engine answer only - the LLM layer was unreachable.'
  return 'Engine answer only.'
}

export default function StoreDetail({ params }: { params: Promise<{ id: string }> }) {
  const id = use(params).id

  const [data, setData] = useState<WhyPayload | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' })
  const [askState, setAskState] = useState<AskState>({ kind: 'idle' })
  const [askText, setAskText] = useState('')

  const controllerRef = useRef<AbortController | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const askControllerRef = useRef<AbortController | null>(null)

  const fetchStore = useCallback(async (store: string) => {
    controllerRef.current?.abort()
    if (timerRef.current) clearTimeout(timerRef.current)
    const controller = new AbortController()
    controllerRef.current = controller
    setPhase({ kind: 'loading' })
    setData(null)
    // A different store is a different question: any in-flight or rendered
    // advisory belongs to the previous store and must not leak into this one.
    askControllerRef.current?.abort()
    setAskState({ kind: 'idle' })
    setAskText('')
    try {
      const response = await fetch(api(`/why/${encodeURIComponent(store)}`), {
        cache: 'no-store',
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      if (response.status === 404) {
        setPhase({ kind: 'missing' })
        return
      }
      // Cold start: free-tier services sleep after ~15 min idle, so on 502/504
      // wait 30s and retry once before showing an error
      // (docs/FRONTEND_CONTRACT.md "Poll").
      if (response.status === 502 || response.status === 504) {
        setPhase({ kind: 'waking' })
        if (timerRef.current) clearTimeout(timerRef.current)
        timerRef.current = setTimeout(() => void fetchStore(store), 30000)
        return
      }
      if (!response.ok) throw new Error(String(response.status))
      const payload = (await response.json()) as WhyPayload
      if (controller.signal.aborted) return
      setData(payload)
      setPhase({ kind: 'ready' })
    } catch {
      if (controller.signal.aborted) return
      setPhase({ kind: 'error' })
    }
  }, [])

  useEffect(() => {
    void fetchStore(id)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      controllerRef.current?.abort()
      askControllerRef.current?.abort()
    }
  }, [id, fetchStore])

  const askQuestion = useCallback(async (store: string, question: string) => {
    const trimmed = question.trim()
    if (!trimmed) return
    askControllerRef.current?.abort()
    const controller = new AbortController()
    askControllerRef.current = controller
    setAskState({ kind: 'asking' })
    try {
      // GET only, like every call on this surface: the advisory reads the
      // record and suggests; it never writes (POST stays approve-only).
      const response = await fetch(
        api(`/advisory/${encodeURIComponent(store)}?question=${encodeURIComponent(trimmed)}`),
        { cache: 'no-store', signal: controller.signal },
      )
      if (controller.signal.aborted) return
      if (!response.ok) {
        let error: string
        if (response.status === 429) {
          error = 'The advisory layer is rate-limited right now (a per-client hourly cap on LLM calls). Try again in a few minutes - the recommendation above stays the decision either way.'
        } else if (response.status === 409) {
          error = "There isn't grounded evidence to answer that yet. The engine's next sweep refreshes the record; nothing was guessed."
        } else if (response.status === 503) {
          error = 'The advisory layer is switched off on this deployment. The recommendation above is unaffected.'
        } else if (response.status === 400) {
          error = 'Questions are capped at 500 characters - shorten it and ask again.'
        } else {
          error = "Couldn't get an answer just now. Nothing was recorded - this surface only asks."
        }
        setAskState({ kind: 'error', error })
        return
      }
      const result = await response.json() as AdvisoryResponse
      if (controller.signal.aborted) return
      setAskState({ kind: 'done', result })
    } catch {
      if (controller.signal.aborted) return
      setAskState({ kind: 'error', error: "Couldn't reach the advisory layer. Nothing was recorded - this surface only asks." })
    }
  }, [])

  const rec: Recommendation | null = data?.evidence?.latest_recommendation ?? null
  const rows: Array<[string, unknown]> = rec
    ? ([
      ['Confidence', rec.confidence],
      ['Store health score', rec.store_health_score],
      ['Recovery', rec.recovery_pct != null ? `${rec.recovery_pct}% (${display(rec.recovery_direction)})` : null],
      ['Window', rec.days_elapsed != null && rec.days_remaining != null ? `Day ${rec.days_elapsed} · ${rec.days_remaining} days left` : null],
      ['Forecast', rec.forecast_signal_available === false
        ? `No forecast signal (business data gap, not an integration error)`
        : String(rec.forecast_status || '').toUpperCase() === 'ERROR'
          ? 'Feed delayed - the forecast feed, not the store, is the problem'
          : rec.forecast_status],
      ['Baseline → current', rec.baseline_forecast != null ? `${rec.baseline_forecast} → ${display(rec.current_forecast)}` : null],
      ['Reason (engine wording)', rec.reason],
      ['Generated at', rec.generated_at],
    ] as Array<[string, unknown]>).filter(([, value]) => value !== null && value !== undefined && value !== '')
    : []

  const guard = data?.guard || {}
  const guardPassed = guard.numeric_grounding === 'passed' && guard.citations === 'passed'
  const waiting = phase.kind === 'loading' || phase.kind === 'waking'

  return (
    <AppChrome
      loading={waiting}
      footerExtra="Read-only"
      navlinks={<><Link href="/">Overview</Link><span className="nav-status"><i aria-hidden="true" />Read-only</span></>}
    >
      <section className="hero">
        <div>
          <Link href="/" className="back">← Back to board</Link>
          <div className="eyebrow" style={{ marginTop: 24 }}>Store detail</div>
          <h1>Store {display(data?.store_id ?? id)}</h1>
          <p>What the engine recommends for this store, and the evidence behind it.</p>
        </div>
        {rec ? (
          <div className="hero-meta">
            {/* The raw token never renders on a stakeholder surface: the board
                says "Extend the intervention" via its *_label fields, so the
                hero says the same sentence (the /why evidence carries no label
                field, hence the client-side map in _lib/api.ts). */}
            <div className="live-dot"><i aria-hidden="true" />{display(actionLabel(rec.recommendation))}</div>
            <strong>confidence {display(rec.confidence)}</strong>
            {rec.forecast_status && rec.forecast_status !== 'AVAILABLE' ? <span>forecast: {forecastText(rec.forecast_status)}</span> : null}
          </div>
        ) : null}
      </section>

      {phase.kind === 'missing' ? (
        // (a) 404 is a real state, not a dead end: name it and offer the way back.
        <div className="notice">
          <strong>Store not found.</strong> Store {display(id)} is not in the current cohort -
          check the id, or open the board to see the stores up for review.
        </div>
      ) : null}
      {phase.kind === 'error' ? (
        // (c) other failures keep a visible Retry button - the load is one
        // click away, not a "try again in a moment" sentence.
        <div className="notice">
          Couldn't load this store.
          {' '}
          <button type="button" className="link-button" onClick={() => void fetchStore(id)}>
            Retry
          </button>
        </div>
      ) : null}

      {waiting ? (
        <LoadingCard
          kicker={phase.kind === 'waking' ? 'API waking up' : 'Store evidence'}
          title={phase.kind === 'waking' ? 'Waiting for the data API' : 'Fetching store evidence from the API'}
          copy={phase.kind === 'waking'
            ? 'The free-tier API sleeps when idle. This can take up to a minute; this page retries automatically.'
            : 'Pulling the recommendation, its evidence and the verification record for this store.'}
        />
      ) : data && (
        <>
          <div className="detail-grid">
            <article className="panel">
              <h2 className="section-title">Why this recommendation</h2>
              <p className="narrative">{narrative(data.narrative)}</p>
            </article>
            <aside className="panel">
              <h2 className="section-title">Evidence the engine used</h2>
              {rows.length ? rows.map(([label, value]) => <Field key={label} label={label} value={value} />) : <p className="directional-note">No recommendation on record for this store yet, so there is nothing to explain. Recommendations are produced by the next sweep.</p>}
              {Object.keys(guard).length ? (
                <>
                  <h3 className="section-title" style={{ marginTop: 28 }}>Verification</h3>
                  {guardPassed ? <p className="grounding">✓ Figures traced to the record</p> : (
                    <div className="chips">
                      {Object.entries(guard).map(([name, value]) => (
                        <Guard key={name} name={name.replace(/_/g, ' ')} value={value} />
                      ))}
                    </div>
                  )}
                </>
              ) : null}
              {/* The raw `evidence` payload is operator provenance
                  (docs/FRONTEND_CONTRACT.md): the ids and internal tokens it
                  carries stay off this page. The human-facing fields above are
                  the whole stakeholder view. */}
              {rec?.recommendation_id ? (
                // The one citable affordance: the product's numbers are recomputable,
                // and this id is what a stakeholder can quote back. Quiet, bottom
                // of the panel; nothing else on the page cites.
                <p className="provenance">
                  Provenance for every figure above · <code>{display(rec.recommendation_id)}</code>
                </p>
              ) : null}
            </aside>
          </div>
          <section className="ask-panel" aria-label="Ask about this store">
            <h2 className="section-title">Ask about this store</h2>
            <p className="ask-sub">
              A grounded suggestion from the off-path LLM layer, for a human reviewer. It reads
              the same evidence as this page, and it never writes anything - the recommendation
              above stays the only decision.
            </p>
            <div className="ask-presets">
              {ASK_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  className="ask-preset"
                  disabled={askState.kind === 'asking'}
                  onClick={() => { setAskText(preset); void askQuestion(id, preset) }}
                >
                  {preset}
                </button>
              ))}
            </div>
            <form
              className="ask-form"
              onSubmit={(event) => { event.preventDefault(); void askQuestion(id, askText) }}
            >
              <label className="dialog-label" htmlFor="ask-question">Your question</label>
              <div className="ask-row">
                {/* maxLength mirrors the API's 500-char guard (rag/question_guard.py):
                    the input refuses the bytes the endpoint would refuse. */}
                <input
                  id="ask-question"
                  className="dialog-input"
                  value={askText}
                  maxLength={500}
                  placeholder="e.g. What should I check before approving?"
                  onChange={(event) => setAskText(event.target.value)}
                />
                <button type="submit" className="button-primary" disabled={askState.kind === 'asking' || !askText.trim()}>
                  {askState.kind === 'asking' ? 'Asking…' : 'Ask'}
                </button>
              </div>
              <p className="ask-budget">
                The advisory layer is budget-capped; when the cap is spent it serves the engine&apos;s
                own answer and says so.
              </p>
            </form>
            {askState.kind === 'asking' ? (
              <p className="ask-status" role="status">Reading the grounded evidence for this store…</p>
            ) : null}
            {askState.kind === 'error' ? (
              <p className="dialog-error" role="alert">{askState.error}</p>
            ) : null}
            {askState.kind === 'done' && askState.result.advisory ? (
              <div className="ask-result" role="status">
                {/* The permanent honesty line: this panel suggests, it never
                    decides or records (rag/advisor.py invariants, mirrored in
                    docs/FRONTEND_CONTRACT.md). */}
                <p className="ask-honesty">Suggestion - not a decision, records nothing</p>
                <p className="ask-action">
                  Suggested action: <strong>{display(actionLabel(askState.result.advisory.suggested_action))}</strong>
                </p>
                {narrative(askState.result.advisory.note) ? (
                  <p className="ask-note">{narrative(askState.result.advisory.note)}</p>
                ) : null}
                {/* advisory_status is audit-log wording - the human sentence
                    below is its translation, never the raw string. */}
                <p className="ask-status">{advisoryStatusLine(askState.result.advisory_status)}</p>
              </div>
            ) : null}
          </section>
        </>
      )}
    </AppChrome>
  )
}
