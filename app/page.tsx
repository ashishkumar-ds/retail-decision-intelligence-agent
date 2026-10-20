'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

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
    'No forecast signal reached these stores, so the campaign effect cannot be evaluated. No data is not zero - it is no verdict. Approve a store once its data gap is understood, using the check beside its id.',
  needs_intervention:
    'The engine has already chosen the options; a human picks one. Approving records the recommended action against your approval token.',
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

const PAGE_SIZE = 8
const TOKEN_KEY = 'rdia.approvalToken'

type Row = Record<string, any>
type Board = {
  total_stores?: any
  needs_decision?: any
  as_of?: any
  as_of_display?: any
  counts?: Record<string, any>
  buckets?: Record<string, Row[]>
  campaign?: { label?: string; timing_window?: string }
}

function api(path: string) {
  return `/api${path}`
}

function display(value: any) {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function rowsFor(board: Board | null, group: string): Row[] {
  if (group !== 'no_action') return [...(board?.buckets?.[group] || [])]
  return NO_ACTION_BUCKETS.flatMap(bucket => board?.buckets?.[bucket] || [])
}

function countFor(board: Board | null, group: string): any {
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

function stateOf(row: Row): string {
  return String(row.state || BUCKET_STATE[row.bucket] || '')
}

function campaignLine(campaign: Board['campaign']): string {
  return [campaign?.label, campaign?.timing_window].filter(Boolean).join(' · ')
}

// The one action on the board is gated: a record is approvable only when the
// API says the human gate holds it and it has not been decided already.
function isPending(row: Row): boolean {
  if (row.requires_human_approval !== true) return false
  return row.decision_status !== 'approved' && row.decision_status !== 'rejected'
}

// Freshness is a property of the data, not of the poll interval: the old board
// said "updated every 60 seconds" three times and never said when the figures
// were produced. The stamp is served display-ready, so the browser never
// re-formats it into the viewer's timezone.

// forecast_status stays a first-class signal: a delayed feed is never rendered
// as zero, and a missing forecast outside the no-data group still says so.
// AVAILABLE is the default case and carries no chip - 37 identical "Forecast
// available" badges were pure noise.
function FeedChip({ status, quiet = false }: { status: any; quiet?: boolean }) {
  const value = String(status || 'NO_DATA').toUpperCase()
  if (value === 'AVAILABLE' || (quiet && value === 'NO_DATA')) return null
  return (
    <span className={`status ${value.toLowerCase()}`}>
      {value === 'ERROR' ? 'Feed delayed' : 'No forecast signal'}
    </span>
  )
}

// The decision block: default vs alternative, what the action commits, and the
// counterfactual. The engine's own vocabulary (EXTEND_INTERVENTION, "cautious")
// is served pre-labelled as *_label fields so both board surfaces say the same
// sentence. A row the engine re-decided after a human ruling says so, instead
// of asking for the same decision twice.
function Decision({ row }: { row: Row }) {
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
          <strong>{display(row.default_action_label || row.default_action)}</strong>
        </div>
        <div>
          <span>Alternative</span>
          <strong>{display(row.fallback_action_label || row.fallback_action)}</strong>
        </div>
        <div>
          <span>If this is wrong</span>
          <strong>{display(row.commitment_label || row.risk)}</strong>
        </div>
      </div>
      {row.cost_of_inaction ? <p>{display(row.cost_of_inaction)}</p> : null}
      <small className="decision-note">
        {decided
          ? 'Recorded in the approval ledger.'
          : 'Approval re-runs the guardrails and verifier at decision time.'}
      </small>
    </div>
  )
}

// One card, three numbers and the one sentence that explains them. Confidence,
// velocity, forecast baseline, measured uplift and DiD are deliberately absent:
// they are evidence for the store page, not inputs to a portfolio decision.
function StoreRow({
  row,
  quietFeed = false,
  onApprove,
}: {
  row: Row
  quietFeed?: boolean
  onApprove: (row: Row) => void
}) {
  const days = row.days_remaining
  const decided = row.decision_status === 'approved' || row.decision_status === 'rejected'
  return (
    <article className="store-row">
      <div className="store-top">
        <div className="store-leading">
          <Link href={`/store/${row.store_id}`} className="store-id">{display(row.store_id)}</Link>
          <span className="state">{display(stateOf(row))}</span>
        </div>
        <Link href={`/store/${row.store_id}`} className="detail-link" aria-label={`Why store ${row.store_id}`}>
          Why <span aria-hidden="true">→</span>
        </Link>
      </div>
      <div className="row-title">
        <p className="reason">{display(row.why_not_working || row.reason)}</p>
        <FeedChip status={row.forecast_status} quiet={quietFeed} />
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
        <div className="fact">
          <dt className="fact-label">Window</dt>
          <dd className="fact-value">
            {days === null || days === undefined ? '—' : `${display(days)} days left`}
          </dd>
        </div>
      </dl>
      <Decision row={row} />
      <div className="card-actions">
        {isPending(row) ? (
          <>
            <span className="approve-hint">Token-gated</span>
            <button type="button" className="approve-button" onClick={() => onApprove(row)}>
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
function ApproveDialog({
  target,
  onClose,
  onApproved,
}: {
  target: Row
  onClose: () => void
  onApproved: () => void
}) {
  const ref = useRef<HTMLDialogElement | null>(null)
  const [token, setToken] = useState('')
  const [remembered, setRemembered] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

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

  async function submit(event: any) {
    event.preventDefault()
    const value = token.trim()
    if (!value || busy) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(api(`/approve/${target.store_id}`), {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${value}` },
        body: JSON.stringify({}),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        const detail = payload && typeof payload.detail === 'object' ? payload.detail : null
        const message = detail?.message || (typeof payload?.detail === 'string' ? payload.detail : '')
        if (response.status === 401) setError('That token was not accepted. Check it and try again.')
        else if (response.status === 403) setError(/unknown/i.test(message)
          ? 'That token was not recognized. Check it and try again.'
          : (message || 'This token is not allowed to approve.'))
        else if (response.status === 404) setError('This store is no longer pending a decision. The board will refresh.')
        else if (response.status === 409) setError(message || 'The decision-time gate refused this approval. Nothing was recorded.')
        else if (response.status === 503) setError('Approval is disabled on this deployment (no approval token is configured).')
        else setError(message || 'Approval failed. Nothing was recorded.')
        return
      }
      try {
        window.sessionStorage.setItem(TOKEN_KEY, value)
      } catch {
        // ignore
      }
      setDone(true)
      onApproved()
      window.setTimeout(() => ref.current?.close(), 1100)
    } catch {
      setError('Could not reach the approval service. Nothing was recorded.')
    } finally {
      setBusy(false)
    }
  }

  const fallback = target.fallback_action_label || target.fallback_action

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
          <>
            <span className="dialog-eyebrow">Recorded</span>
            <h2 className="dialog-title">Store {display(target.store_id)} approved</h2>
            <p className="dialog-sub">The decision is in the approval ledger and the board is refreshing.</p>
          </>
        ) : (
          <>
            <span className="dialog-eyebrow">Human decision</span>
            <h2 className="dialog-title">Approve store {display(target.store_id)}?</h2>
            <p className="dialog-sub">This records the recommended action against your approval token.</p>
            <p className="dialog-recommend">
              <strong>{display(target.default_action_label || target.default_action)}</strong>
              {fallback ? <> instead of {display(fallback)}</> : null}
            </p>
            {remembered ? (
              <p className="dialog-note">
                Using the token saved in this tab.{' '}
                <button type="button" className="link-button" onClick={forgetToken}>
                  Use a different token
                </button>
              </p>
            ) : (
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
            {error ? <p className="dialog-error">{error}</p> : null}
            <p className="dialog-note">
              Kept in this tab only. The backend re-runs the guardrails and verifier before it records anything.
            </p>
            <div className="dialog-actions">
              <button type="button" className="button-ghost" onClick={() => ref.current?.close()} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="button-primary" disabled={busy || !token.trim()}>
                {busy ? 'Approving…' : 'Approve'}
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
  const [target, setTarget] = useState<Row | null>(null)

  // Render free services sleep after ~15 min idle: on a cold start, wait 30s
  // and retry once before showing an error (docs/FRONTEND_CONTRACT.md).
  const load = useCallback(async () => {
    try {
      const response = await fetch(api('/board'), { cache: 'no-store' })
      if (response.status === 502 || response.status === 504) {
        setWaking(true)
        setTimeout(async () => {
          try {
            const retry = await fetch(api('/board'), { cache: 'no-store' })
            if (!retry.ok) throw new Error()
            setBoard(await retry.json())
            setError('')
            setWaking(false)
          } catch {
            setError('The data service may still be starting.')
            setWaking(false)
          }
        }, 30000)
        return
      }
      if (!response.ok) throw new Error()
      setBoard(await response.json())
      setError('')
      setWaking(false)
    } catch {
      setError("Couldn't load the latest figures. Please try again in a moment.")
      setWaking(false)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(load, 60000)
    return () => clearInterval(timer)
  }, [load])

  const buckets = board?.buckets || {}
  const anyEarlySignal = Object.values(buckets).some(rows =>
    (rows || []).some(row => row.directional_note))

  return (
    <>
      <div className="topline" />
      <main className="shell">
        <nav className="nav">
          <Link href="/" className="brand">
            <img src="/dunnhumby.svg" alt="dunnhumby" className="brand-logo" width={82} height={19} />
            <span className="brand-divider" aria-hidden="true" />
            <span className="brand-product">retail decision intelligence agent</span>
          </Link>
          <div className="navlinks">
            <span className="nav-status"><i aria-hidden="true" />Live board</span>
          </div>
        </nav>

        {/* Identity and freshness. Not a marketing hero: the numbers come next. */}
        <header className="board-head">
          <div>
            {campaignLine(board?.campaign) ? (
              <span className="eyebrow">{campaignLine(board?.campaign)}</span>
            ) : null}
            <h1>Store review</h1>
          </div>
          {board ? (
            <p className="freshness">
              {board.as_of_display
                ? `Data as of ${board.as_of_display}`
                : 'Data freshness unavailable'}
            </p>
          ) : null}
        </header>

        {(waking || error) && (
          <div className="notice">{waking ? 'Service starting. Retrying shortly.' : error}</div>
        )}

        {!board && !error ? (
          <div className="loading">Loading latest figures…</div>
        ) : board && (
          <>
            <section className="summary">
              {/* The one number the rest of the page has to add up to. */}
              <p className="gate-line">
                <strong>{display(board.needs_decision)}</strong>
                {/* The flex gap is visual only; keep a real space in the text
                    so a screen reader does not say "85of 85". */}
                <span>{' '}of {display(board.total_stores)} stores are waiting on a human decision.</span>
              </p>
              <div className="tiles">
                {GROUPS.map(group => (
                  <a className={`tile ${GROUP_TONES[group]}`} key={group} href={`#${group}`}>
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
            </section>

            {GROUPS.map(group => {
              const rows = rowsFor(board, group)
              const open = expanded[group] ?? false
              const all = showAll[group] ?? false
              const visible = all ? rows : rows.slice(0, PAGE_SIZE)
              // 47 stores with no forecast signal share one recommendation, so
              // the list is ids and the choice is stated once. A store that is
              // not gated keeps its own row either way.
              const sharedDecision = group === 'insufficient_data'
                && rows.length > 0
                && rows.every(row => row.default_action)
              return (
                <section className={`section ${GROUP_TONES[group]}`} id={group} key={group}>
                  <button
                    className="bucket-head"
                    onClick={() => setExpanded(state => ({ ...state, [group]: !open }))}
                    aria-expanded={open}
                  >
                    <div className="bucket-marker" aria-hidden="true" />
                    <div>
                      <h2>{GROUP_LABELS[group]}</h2>
                      <span className="bucket-count">
                        {rows.length} {rows.length === 1 ? 'store' : 'stores'}
                      </span>
                    </div>
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
                    <div className="id-list">
                      {visible.map(row => (
                        <span className="id-item" key={row.store_id}>
                          <Link
                            href={`/store/${row.store_id}`}
                            className="id-chip"
                            title={display(row.reason)}
                          >
                            {display(row.store_id)}
                          </Link>
                          {isPending(row) ? (
                            <button
                              type="button"
                              className="chip-approve"
                              aria-label={`Approve store ${row.store_id}`}
                              title={`Approve store ${row.store_id}`}
                              onClick={() => setTarget(row)}
                            >
                              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                                <path
                                  d="M3.5 8.5l3 3 6-7"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            </button>
                          ) : null}
                        </span>
                      ))}
                    </div>
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

        <footer className="footer">
          <span>© 2026 Ashish Kumar · data: dunnhumby</span>
          <span>Approvals are token-gated</span>
        </footer>
      </main>

      {target ? (
        <ApproveDialog
          target={target}
          onClose={() => setTarget(null)}
          onApproved={() => load()}
        />
      ) : null}
    </>
  )
}

export const dynamic = 'force-dynamic'
