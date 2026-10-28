'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { AppChrome, LoadingCard } from './_components/chrome'
import { actionLabel, api, display } from './_lib/api'
import type { Board, BoardRow } from './types'

// Stakeholder view. The backend's five buckets are unchanged - this is display
// grouping only, so a non-technical reviewer gets one answer per store: decide
// now, cannot evaluate, or nothing to do. Grouped rows still print their own
// state word (`row.state`), so "No action needed" can never overstate a store
// that is flat or declining.
const GROUPS = ['needs_intervention', 'insufficient_data', 'no_action']

const GROUP_LABELS: Record<string, string> = {
  needs_intervention: 'Need a decision',
  insufficient_data: 'Needs review (no data)',
  no_action: 'No action needed',
}

const GROUP_TONES: Record<string, string> = {
  needs_intervention: 'attention',
  insufficient_data: 'review',
  no_action: 'idle',
}

// The three backend buckets that mean the engine is not asking for anything.
const NO_ACTION_BUCKETS = ['recovering', 'working_well', 'watch']

// The tile's caption is the honest split of that count: a store building
// momentum and a store sitting flat are not the same reassurance.
const NO_ACTION_CAPTIONS: [string, string][] = [
  ['recovering', 'building momentum'],
  ['working_well', 'on track'],
  ['watch', 'flat, no action'],
]

// Stated once per group, never on every row: 47 identical sentences told the
// reader nothing the count did not.
const GROUP_NOTES: Record<string, string> = {
  insufficient_data:
    'No forecast signal reached these stores, so the campaign effect cannot be evaluated. No data is not zero - it is no verdict. Mark a store reviewed once its data gap is understood: use the Review button beside its id, or mark the visible set in one pass below.',
  needs_intervention:
    'The engine has already chosen. Approving records the recommended action; the alternative shown is what runs only if the decision-time guardrails refuse that choice.',
}

// Fallback only. The API serves `state` per row; this keeps an older API from
// rendering a blank cell.
const BUCKET_STATE: Record<string, string> = {
  needs_intervention: 'Needs decision',
  insufficient_data: 'Needs review',
  recovering: 'Building momentum',
  working_well: 'On track',
  watch: 'Watch',
}

// Three stores per open group on the first screen; the rest sit behind
// "Show all". Eight rows pushed the second group (and the footer) too far down.
const PAGE_SIZE = 3
const TOKEN_KEY = 'rdia.approvalToken'

function rowsFor(board: Board | null, group: string): BoardRow[] {
  if (group !== 'no_action') return [...(board?.buckets?.[group] || [])]
  return NO_ACTION_BUCKETS.flatMap(bucket => board?.buckets?.[bucket] || [])
}

function countFor(board: Board | null, group: string): number | undefined {
  if (group !== 'no_action') return board?.counts?.[group]
  return NO_ACTION_BUCKETS.reduce((total, bucket) => total + (board?.counts?.[bucket] || 0), 0)
}

function captionFor(board: Board | null, group: string): string {
  if (group !== 'no_action') return ''
  return NO_ACTION_CAPTIONS
    .filter(([bucket]) => board?.counts?.[bucket])
    .map(([bucket, text]) => `${board?.counts?.[bucket]} ${text}`)
    .join(' · ')
}

function stateOf(row: BoardRow): string {
  return String(row.state || BUCKET_STATE[row.bucket || ''] || '')
}

// The series context a first-time visitor needs: which campaign and the plan's
// fixed window. The campaign name is operator-set on the API when available;
// otherwise it falls back to the series' locked Campaign 18 (Part 2). 60 days is
// the recovery plan's fixed window (CONTEXT.md), not a client-side calculation.
// The store count lives in the gate line, not here, so the header says 85 once.
const CAMPAIGN_FALLBACK = 'Campaign 18'
const RECOVERY_WINDOW = '60-day recovery'

function contextLine(board: Board | null): string {
  const label = board?.campaign?.label || CAMPAIGN_FALLBACK
  // NBSP before the dot keeps it attached to the preceding segment if the line
  // wraps; the regular space after it is the break point, so a line never
  // starts with a dangling separator.
  return [label, RECOVERY_WINDOW].join('\u00A0· ')
}

// The one action on the board is gated: a record is approvable only when the
// API says the human gate holds it and it has not been decided already.
function isPending(row: BoardRow): boolean {
  if (row.requires_human_approval !== true) return false
  return row.decision_status !== 'approved' && row.decision_status !== 'rejected'
}

// A no-data row is a review, not a budget approval. The same per-store write
// records both, but the action should say what the human is actually doing.
function isReview(row: BoardRow): boolean {
  return row.bucket === 'insufficient_data' || row.default_action === 'NEEDS_REVIEW'
}

// Accessible name for the per-store action button, said in full so a card's
// single "Approve" is never ambiguous in a screen-reader button list.
function decisionActionLabel(row: BoardRow): string {
  return isReview(row)
    ? `Mark store ${row.store_id} reviewed`
    : `Approve store ${row.store_id}`
}

// Freshness is a property of the data, not of the poll interval: the old board
// said "updated every 60 seconds" three times and never said when the figures
// were produced. The stamp is served display-ready, so the browser never
// re-formats it into the viewer's timezone.

// forecast_status stays a first-class signal: a delayed feed is never rendered
// as zero, and a missing forecast outside the no-data group still says so.
// AVAILABLE is the default case and carries no chip - 37 identical "Forecast
// available" badges were pure noise. Unknown machine tokens get the neutral
// chip, not a class that implies a verdict the API did not make.
function FeedChip({ status, quiet = false }: { status?: BoardRow['forecast_status']; quiet?: boolean }) {
  const value = String(status || 'NO_DATA').toUpperCase()
  if (value === 'AVAILABLE' || (quiet && value === 'NO_DATA')) return null
  const tone = value === 'ERROR' ? 'error' : value === 'NO_DATA' ? 'no_data' : 'other'
  return (
    <span className={`status ${tone}`}>
      {value === 'ERROR' ? 'Feed delayed' : 'No forecast signal'}
    </span>
  )
}

// The decision block: default vs alternative, what the action commits, and the
// counterfactual. The engine's own vocabulary (EXTEND_INTERVENTION, "cautious")
// is served pre-labelled as *_label fields so both board surfaces say the same
// sentence. A row the engine re-decided after a human ruling says so, instead
// of asking for the same decision twice.
function Decision({ row }: { row: BoardRow }) {
  if (!row.default_action) return null
  const decided = row.decision_status === 'approved' || row.decision_status === 'rejected'
  return (
    <div className="decision">
      <span className="decision-kicker">
        {decided ? `Already decided · ${display(row.decision_status_label)}` : 'Decision needed'}
      </span>
      <div className="decision-grid">
        <div>
          <span>Recommended</span>
          <strong>{display(row.default_action_label || actionLabel(row.default_action))}</strong>
        </div>
        <div>
          <span>Fallback</span>
          <strong>{display(row.fallback_action_label || actionLabel(row.fallback_action))}</strong>
        </div>
        <div>
          <span>Commitment</span>
          <strong>{display(row.commitment_label || row.risk)}</strong>
        </div>
      </div>
      {row.cost_of_inaction ? <p>{display(row.cost_of_inaction)}</p> : null}
      <small className="decision-note">
        {decided
          ? 'Recorded in the approval ledger.'
          : 'Approving records the recommended action. The fallback runs only if the guards refuse that action at decision time.'}
      </small>
    </div>
  )
}

// One card: two facts (Health, Recovery), the window on the reason line, and
// the verdict. Confidence, velocity, forecast baseline, measured uplift and DiD
// are deliberately absent: they are evidence for the store page, not inputs to
// a portfolio decision.
// "On track" and "Watch" are two different reassurances (integer in the tile
// caption, different state word here): a store that is moving earns the quiet
// green chip; a flat one keeps the neutral one. The honest split is legible on
// the row, not only in the group caption.
function stateTone(row: BoardRow): string {
  if (row.bucket === 'recovering' || row.bucket === 'working_well') return ' state-ok'
  if (row.bucket === 'watch') return ' state-watch'
  return ''
}

function StoreRow({
  row,
  quietFeed = false,
  onApprove,
}: {
  row: BoardRow
  quietFeed?: boolean
  onApprove: (row: BoardRow) => void
}) {
  const days = row.days_remaining
  const decided = row.decision_status === 'approved' || row.decision_status === 'rejected'
  return (
    <article className="store-row">
      <div className="store-top">
        <div className="store-leading">
          {/* The why page must name itself: since the single-link collapse
              the bare id was the only signal, and a bold number doesn't read
              as clickable (found live, 2026-10-29). */}
          <Link href={`/store/${row.store_id}`} className="store-id">
            <span className="why-lead">Why store</span> {display(row.store_id)}
          </Link>
          <span className={`state${stateTone(row)}`}>{display(stateOf(row))}</span>
        </div>
      </div>
      <div className="row-title">
        <p className="reason">{display(row.why_not_working || row.reason)}</p>
        <span className="row-meta">
          {/* The window rides beside the reason it qualifies: it answers "how
              long is left on this?", which is a reason-line question, not a
              fact-row peer of Recovery %. */}
          {days !== null && days !== undefined ? <span className="window-note">{display(days)} days left</span> : null}
          <FeedChip status={row.forecast_status} quiet={quietFeed} />
        </span>
      </div>
      <dl className="facts">
        <div className="fact">
          <dt className="fact-label">Health</dt>
          <dd className="fact-value">{display(row.store_health_score)}</dd>
        </div>
        <div className="fact">
          <dt className="fact-label">Recovery</dt>
          <dd className="fact-value">{display(row.recovery_pct)}%</dd>
        </div>
      </dl>
      <Decision row={row} />
      <div className="card-actions">
        {isPending(row) ? (
          <>
            <span className="approve-hint">Needs your approval</span>
            <button
              type="button"
              className="approve-button"
              aria-label={decisionActionLabel(row)}
              onClick={() => onApprove(row)}
            >
              Approve
            </button>
          </>
        ) : decided ? (
          <span className="approved-note">{display(row.decision_status_label)}</span>
        ) : null}
      </div>
    </article>
  )
}

// The write action. The backend refuses unauthenticated approvals (503 when no
// token is configured, 401/403 otherwise), so the dialog collects the operator
// token and keeps it in this tab's session storage only. The decision itself is
// re-gated server-side, so a stale board cannot force an approval through.
// The same dialog also covers the bulk-review pass: with a `queue`, it walks
// every store's own POST in sequence - each write is separately re-gated, so
// one refused store never blocks the others (it is counted and skipped).
function ApproveDialog({
  target,
  queue,
  onClose,
  onApproved,
}: {
  target: BoardRow
  queue?: BoardRow[]
  onClose: () => void
  onApproved: () => void
}) {
  const ref = useRef<HTMLDialogElement | null>(null)
  const [token, setToken] = useState('')
  const [remembered, setRemembered] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [progress, setProgress] = useState({ recorded: 0, skipped: 0 })

  const targets = queue && queue.length > 0 ? queue : [target]

  useEffect(() => {
    ref.current?.showModal()
    try {
      const stored = window.sessionStorage.getItem(TOKEN_KEY)
      if (stored) {
        setToken(stored)
        setRemembered(true)
      }
    } catch {
      // Storage can be blocked; the field still works for this action.
    }
  }, [])

  function forgetToken() {
    setRemembered(false)
    setToken('')
    try {
      window.sessionStorage.removeItem(TOKEN_KEY)
    } catch {
      // ignore
    }
  }

  // The API error messages are machine plumbing (guard internals); only prose
  // that reads like a sentence may reach the stakeholder. This one element's
  // discipline is "never print a raw token".
  function humanCopy(text: unknown): string {
    const s = typeof text === 'string' ? text : ''
    return s && s.split(' ').length >= 3 && !/_/.test(s) && s.length <= 140 ? s : ''
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = token.trim()
    if (!value || busy) return
    setBusy(true)
    setError('')
    try {
      let recorded = 0
      let skipped = 0
      for (const row of targets) {
        const response = await fetch(api(`/approve/${row.store_id}`), {
          method: 'POST',
          headers: { 'content-type': 'application/json', authorization: `Bearer ${value}` },
          body: JSON.stringify({}),
        })
        if (response.ok) {
          recorded += 1
          setProgress({ recorded, skipped })
          continue
        }
        const payload = await response.json().catch(() => null)
        const detail = payload && typeof payload.detail === 'object' ? payload.detail : null
        const message = detail?.message || (typeof payload?.detail === 'string' ? payload.detail : '')
        const status = response.status
        // Hard stops: the token itself is wrong or the deployment is closed -
        // waiting would repeat the same refusal for every remaining store.
        if (status === 401) { setError('That token was not accepted. Check it and try again.'); return }
        if (status === 403) {
          setError(humanCopy(message) || 'This token is not allowed to record decisions.')
          return
        }
        if (status === 503) {
          setError('Decisions are disabled on this deployment (no approval token is configured).')
          return
        }
        if (status === 404 && targets.length === 1) {
          setError('This store is no longer pending a decision. The board will refresh.')
          return
        }
        if (status === 409 && targets.length === 1) {
          setError('The decision-time guardrails refused this action. Nothing was recorded.')
          return
        }
        // Batch: a refused or already-decided store is a skip, not a stop.
        skipped += 1
        setProgress({ recorded, skipped })
      }
      if (recorded === 0 && targets.length === 1) {
        setError('The decision was not recorded.')
        return
      }
      try {
        window.sessionStorage.setItem(TOKEN_KEY, value)
      } catch {
        // ignore
      }
      setProgress({ recorded, skipped })
      setDone(true)
      onApproved()
      window.setTimeout(() => ref.current?.close(), recorded > 1 ? 2600 : 1800)
    } catch {
      setError('Could not reach the approval service. Nothing was recorded.')
    } finally {
      setBusy(false)
    }
  }

  const fallback = target.fallback_action_label || actionLabel(target.fallback_action)
  const review = isReview(target)
  const verb = review ? 'Mark store' : 'Approve store'

  return (
    <dialog
      ref={ref}
      className="approve-dialog"
      onClose={onClose}
      onCancel={event => {
        if (busy) event.preventDefault()
      }}
      onClick={event => {
        if (event.target === ref.current && !busy) ref.current?.close()
      }}
    >
      <form className="dialog-body" onSubmit={submit}>
        {done ? (
          <div role="status">
            <span className="dialog-eyebrow">Recorded</span>
            <h2 className="dialog-title">
              {progress.recorded > 1
                ? `${progress.recorded} stores marked reviewed`
                : `Store ${display(target.store_id)} ${review ? 'marked reviewed' : 'approved'}`}
            </h2>
            <p className="dialog-sub">
              {progress.recorded > 1
                ? 'Each store was recorded with its own write.'
                : 'The decision is recorded and the board is refreshing.'}
              {progress.skipped > 0
                ? ` ${progress.skipped} store${progress.skipped === 1 ? ' was' : 's were'} skipped - the guard refused or it was already decided.`
                : ''}
            </p>
          </div>
        ) : (
          <>
            <span className="dialog-eyebrow">Human decision</span>
            <h2 className="dialog-title">
              {verb} {display(target.store_id)}{review ? ' reviewed?' : '?'}
            </h2>
            <p className="dialog-sub">
              {review
                ? 'This records the data-gap review against your approval token.'
                : 'This records the recommended action against your approval token.'}
            </p>
            <p className="dialog-recommend">
              Approved records: <strong>{display(target.default_action_label || actionLabel(target.default_action))}</strong>
              {fallback ? <> · fallback, if the guards refuse it: {display(fallback)}</> : null}
            </p>
            {remembered ? (
              <p className="dialog-note">
                Using the token saved in this tab.{' '}
                <button type="button" className="link-button" onClick={forgetToken}>
                  Use a different token
                </button>
              </p>
            ) : (
              <p className="dialog-note dialog-token-intro">
                Your approval token comes from the programme operator - it records you as the deciding reviewer.
              </p>
            )}
            {remembered ? null : (
              <>
                <label className="dialog-label" htmlFor="approval-token">Approval token</label>
                <input
                  id="approval-token"
                  className="dialog-input"
                  type="password"
                  value={token}
                  onChange={event => setToken(event.target.value)}
                  placeholder="Paste the operator token"
                  autoComplete="off"
                  required
                />
              </>
            )}
            {/* Announced the moment it renders, so a refused approval is not
                silent: the re-run verdict is the decision's whole outcome. */}
            {error ? <p className="dialog-error" role="alert">{error}</p> : null}
            {busy && targets.length > 1 ? (
              <p className="dialog-note" role="status">
                {progress.recorded} of {targets.length} recorded
                {progress.skipped > 0 ? ` · ${progress.skipped} skipped` : ''}…
              </p>
            ) : null}
            <p className="dialog-note">
              Kept in this tab only. The backend re-runs the guardrails and verifier before it records anything.
            </p>
            <div className="dialog-actions">
              <button type="button" className="button-ghost" onClick={() => ref.current?.close()} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="button-primary" disabled={busy || !token.trim()}>
                {busy
                  ? review ? 'Marking…' : 'Approving…'
                  : targets.length > 1
                    ? `Mark ${targets.length} stores reviewed`
                    : review ? 'Mark reviewed' : 'Approve'}
              </button>
            </div>
          </>
        )}
      </form>
    </dialog>
  )
}

export default function BoardPage() {
  const [board, setBoard] = useState<Board | null>(null)
  const [error, setError] = useState('')
  const [waking, setWaking] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ needs_intervention: true })
  const [showAll, setShowAll] = useState<Record<string, boolean>>({})
  const [target, setTarget] = useState<BoardRow | null>(null)
  const [queue, setQueue] = useState<BoardRow[] | null>(null)
  // Quiet refresh feedback: the 60s poll otherwise moves stores under a
  // mid-read mouse with zero signal. One muted line, gone in a few seconds.
  const [refreshed, setRefreshed] = useState(false)

  // The 60s poll and the 30s cold-start retry can race, so every fetch gets
  // its own AbortController and an attempt counter: only the newest response
  // is allowed to set state, and the retry timer is cleared on unmount.
  const attemptRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadedOnceRef = useRef(false)

  // Cold start: free-tier services sleep after ~15 min idle, so on 502/504
  // wait 30s and retry once before showing an error
  // (docs/FRONTEND_CONTRACT.md "Poll").
  const load = useCallback(async () => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const attempt = ++attemptRef.current
    const stale = () => attempt !== attemptRef.current || controller.signal.aborted

    try {
      const response = await fetch(api('/board'), { cache: 'no-store', signal: controller.signal })
      if (response.status === 502 || response.status === 504) {
        if (stale()) return
        setWaking(true)
        if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
        retryTimerRef.current = setTimeout(async () => {
          if (stale()) return
          try {
            const retry = await fetch(api('/board'), { cache: 'no-store', signal: controller.signal })
            if (stale()) return
            if (!retry.ok) throw new Error()
            setBoard(await retry.json())
            setError('')
            setWaking(false)
          } catch {
            if (stale()) return
            setError('The data service may still be starting.')
            setWaking(false)
          }
        }, 30000)
        return
      }
      if (!response.ok) throw new Error(String(response.status))
      const payload = (await response.json()) as Board
      if (stale()) return
      setBoard(payload)
      setError('')
      setWaking(false)
      if (loadedOnceRef.current) {
        setRefreshed(true)
        if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
        refreshTimerRef.current = setTimeout(() => setRefreshed(false), 6000)
      }
      loadedOnceRef.current = true
    } catch {
      if (stale()) return
      setError("Couldn't load the latest figures. Please try again in a moment.")
      setWaking(false)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, 60000)
    return () => {
      clearInterval(timer)
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      controllerRef.current?.abort()
    }
  }, [load])

  // Tiles are links for no-JS resilience; with JS they expand their target
  // group first (two of three start closed, so a bare hash lands on a
  // collapsed header) and hand focus to the section, so keyboard and screen-
  // reader users hear the destination instead of a silent scroll.
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({})

  function openFromTile(group: string, event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault()
    setExpanded(state => ({ ...state, [group]: true }))
    window.requestAnimationFrame(() => {
      const section = sectionRefs.current[group]
      if (!section) return
      section.focus({ preventScroll: true })
      section.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  // Closing the sheet drops both the single target and any bulk queue.
  const closeDialog = useCallback(() => {
    setTarget(null)
    setQueue(null)
  }, [])

  const buckets = board?.buckets || {}
  const anyEarlySignal = Object.values(buckets).some(rows =>
    (rows || []).some(row => row.directional_note))
  // The brand rail is the one moving thing while the feed loads.
  const loading = !board && !error

  return (
    <>
      <AppChrome loading={loading} probe={Boolean(board)} footerExtra="Approvals are recorded, never automatic">

        {/* Identity. Not a marketing hero: series context, the page name, and
            the data's freshness on the same line. Stated here only - never
            repeated in the footer. The header stays three lines and the first
            figure stays above the fold. */}
        <header className="board-head">
          <div>
            <span className="eyebrow">{contextLine(board)}</span>
            <h1>Store recovery review</h1>
          </div>
          {board ? (
            board.as_of ? (
              <time className="board-asof" dateTime={board.as_of}>
                {board.as_of_display
                  ? `Data as of ${board.as_of_display}`
                  : 'Data freshness unavailable'}
              </time>
            ) : (
              // No datetime attribute when the API served no timestamp:
              // <time datetime> must carry a parsable machine value.
              <span className="board-asof">
                {board.as_of_display
                  ? `Data as of ${board.as_of_display}`
                  : 'Data freshness unavailable'}
              </span>
            )
          ) : null}
        </header>

        {error ? (
          <div className="notice">
            {error}
            {' '}
            <button type="button" className="link-button" onClick={() => load()}>
              Retry now
            </button>
          </div>
        ) : board && waking ? (
          <div className="notice">
            The data service is waking up. Showing the last loaded board; retrying shortly.
          </div>
        ) : null}

        {loading ? (
          <LoadingCard
            kicker={waking ? 'API waking up' : 'Live store data'}
            title={waking ? 'Waiting for the data API' : 'Fetching store data from the API'}
            copy={waking
              ? 'The free-tier API sleeps when idle. This can take up to a minute; the board retries automatically.'
              : 'Pulling the latest forecasts, recovery signals and approval status for the campaign cohort.'}
            note="The board opens as soon as the data lands."
            tiles
          />
        ) : board && (
          <>
            <section className="summary">
              {/* One number for the human workload. The tiles below carry the
                  decision/data-review split; repeating it here would print the
                  same counts twice. */}
              <p className="gate-line">
                <strong>{display(board.needs_decision)}</strong>
                {/* The flex gap is visual only; keep a real space in the text
                    so a screen reader does not say "85of 85". */}
                <span>{' '}of {display(board.total_stores)} stores are waiting on a human review.</span>
              </p>
              <div className="tiles">
                {GROUPS.map(group => (
                  <a
                    className={`tile ${GROUP_TONES[group]}`}
                    key={group}
                    href={`#${group}`}
                    onClick={event => openFromTile(group, event)}
                  >
                    <span className="tile-count">{display(countFor(board, group))}</span>
                    <span className="tile-label">{GROUP_LABELS[group]}</span>
                  </a>
                ))}
              </div>
              {/* The honest split sits under the row for every width: inside one
                  tile it wrapped to two lines and stretched all three to its
                  height, leaving the other two mostly empty. */}
              {captionFor(board, 'no_action') ? (
                <p className="tiles-note">{captionFor(board, 'no_action')}</p>
              ) : null}
              {refreshed ? <p className="refresh-note" role="status">Board updated just now.</p> : null}
            </section>

            {GROUPS.map(group => {
              const rows = rowsFor(board, group)
              const open = expanded[group] ?? false
              const all = showAll[group] ?? false
              const visible = all ? rows : rows.slice(0, PAGE_SIZE)
              // 47 stores with no forecast signal share one recommendation, so
              // the list is ids and the choice is stated once. A store that is
              // not gated keeps its own row either way. The equality check is
              // as load-bearing as the presence check: two rows with different
              // default actions must NOT print row 0's verdict as group truth.
              const sharedDecision = group === 'insufficient_data'
                && rows.length > 0
                && rows.every(row => row.default_action)
                && new Set(rows.map(row => row.default_action)).size === 1
              const pendingRows = rows.filter(isPending)
              return (
                <section
                  className={`section ${GROUP_TONES[group]}`}
                  id={group}
                  key={group}
                  tabIndex={-1}
                  ref={el => { sectionRefs.current[group] = el }}
                >
                  {/* Valid HTML: a <button> must not contain a heading, so the
                      h2 carries the toggle (heading-with-button pattern). All
                      boxed styling stays on the button via .bucket-head. */}
                  <h2 className="bucket-heading">
                    <button
                      className="bucket-head"
                      onClick={() => setExpanded(state => ({ ...state, [group]: !open }))}
                      aria-expanded={open}
                    >
                      <span className="bucket-marker" aria-hidden="true" />
                      <span className="bucket-head-label">
                        <span className="bucket-title">{GROUP_LABELS[group]}</span>
                        <span className="bucket-count">
                          {rows.length} {rows.length === 1 ? 'store' : 'stores'}
                        </span>
                      </span>
                      <span className={`bucket-toggle ${open ? 'open' : ''}`} aria-hidden="true">
                        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                          <path
                            d="M4 6l4 4 4-4"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                    </button>
                  </h2>

                  {open && rows.length === 0 ? <div className="empty">No stores here.</div> : null}

                  {open && GROUP_NOTES[group] ? (
                    <p className="bucket-note">{GROUP_NOTES[group]}</p>
                  ) : null}

                  {open && sharedDecision ? (
                    <div className="shared-decision">
                      <Decision row={rows[0]} />
                    </div>
                  ) : null}

                  {open && (sharedDecision ? (
                    // Each chip's check/Review writes its own store; the bulk
                    // button beneath the list runs the visible set in one
                    // token-passed sequence.
                    <>
                      <div className="id-list">
                        {visible.map(row => (
                          <span className="id-item" key={row.store_id}>
                            <Link
                              href={`/store/${row.store_id}`}
                              className="id-chip"
                              // Name the link in full: the visible label is only
                              // the bare id.
                              aria-label={`Why store ${row.store_id}: ${display(row.reason)}`}
                            >
                              {display(row.store_id)}
                            </Link>
                            {isPending(row) ? (
                              <button
                                type="button"
                                className="chip-approve"
                                aria-label={decisionActionLabel(row)}
                                onClick={() => setTarget(row)}
                              >
                                Review
                              </button>
                            ) : null}
                          </span>
                        ))}
                      </div>
                      <p className="id-hint">An id opens that store&apos;s why page.</p>
                      {pendingRows.length > 1 ? (
                        <button
                          type="button"
                          className="bulk-review"
                          onClick={() => {
                            setQueue(pendingRows)
                            setTarget(pendingRows[0])
                          }}
                        >
                          Mark these {pendingRows.length} stores reviewed
                        </button>
                      ) : null}
                    </>
                  ) : visible.map(row => (
                    <StoreRow
                      key={row.store_id}
                      row={row}
                      quietFeed={group === 'insufficient_data'}
                      onApprove={setTarget}
                    />
                  )))}

                  {open && rows.length > PAGE_SIZE ? (
                    <button
                      className="show-toggle"
                      onClick={() => setShowAll(state => ({ ...state, [group]: !all }))}
                    >
                      {all ? 'Show less' : `Show all ${rows.length} stores`}
                    </button>
                  ) : null}
                </section>
              )
            })}

            {/* One caveat for the whole board, instead of one per row. */}
            {anyEarlySignal ? (
              <p className="caveat">
                Measured uplift follows once an evaluation window closes. Until then a row
                carries the early forecast signal, which is directional and not a
                measurement.
              </p>
            ) : null}
          </>
        )}
      </AppChrome>
      {/* The dialog that makes the Approve buttons real: per-card reviews set
          `target`, the no-data group's bulk pass sets `queue` (with the first
          pending store as `target`), and this mount renders the token sheet.
          Reconciliation with the card rebuild dropped this render while
          keeping the buttons - the source-grep guard in
          tests/test_approve_dialog.py pins it now. */}
      {target ? (
        <ApproveDialog
          target={target}
          queue={queue ?? undefined}
          onClose={closeDialog}
          onApproved={load}
        />
      ) : null}
    </>
  )
}
