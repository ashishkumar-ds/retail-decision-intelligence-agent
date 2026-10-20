'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

// Store detail - the "why" page. The narrative is shown as prose with its
// internal [rec:/event:/src:] citation markers stripped; the page never prints
// a record id, a retrieval chunk or a raw engine token from the citation layer.
// Every figure is printed as served. No client math, no rewriting, GET only.
type Detail = { store_id?: any; narrative?: string; guard?: Record<string, any>; evidence?: { latest_recommendation?: Record<string, any> } }

const MARKER = /\s*\[(rec|event|src):[^\]]+\]/g

function api(path: string) { return `/api${path}` }
function display(value: any) { return value === null || value === undefined || value === '' ? '—' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value) }
// The markers are citation plumbing, not sentence content. Removing the leading
// whitespace keeps "0.62 [rec:…]." from becoming "0.62 .".
function narrative(text: any) { return String(text || '').replace(MARKER, '') }
function Field({ label, value }: { label: string; value: any }) { if (value === null || value === undefined || value === '') return null; const long = String(value).length > 44; return <div className={long ? 'kv kv-long' : 'kv'}><span>{label}</span><strong>{display(value)}</strong></div> }
// A guard pass is green; a degrade (LLM off/unavailable) is amber; anything
// else is red - the same fail-closed signal the API reports.
function Guard({ name, value }: { name: string; value: any }) { const text = String(value); const tone = text === 'passed' ? 'ok' : (/unavailable|off/.test(text) ? 'warn' : ''); return <span className={`badge ${tone}`}>{name}: {text}</span> }

export default function StoreDetail({ params }: { params: Promise<{ id: string }> }) {
  const [id, setId] = useState('')
  const [data, setData] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { params.then(({ id }) => { setId(id); fetch(api(`/why/${id}`), { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setData).catch(() => setError("Couldn't load this store. Please try again in a moment.")) }) }, [params])

  const rec = data?.evidence?.latest_recommendation
  const rows: [string, any][] = rec ? ([
    ['Confidence', rec.confidence],
    ['Store health score', rec.store_health_score],
    ['Recovery', rec.recovery_pct != null ? `${rec.recovery_pct}% (${display(rec.recovery_direction)})` : null],
    ['Window', rec.days_elapsed != null && rec.days_remaining != null ? `Day ${rec.days_elapsed} · ${rec.days_remaining} days left` : null],
    ['Forecast', rec.forecast_signal_available === false ? `${display(rec.forecast_status)} - no forecast signal (business data gap, not an integration error)` : rec.forecast_status],
    ['Baseline → current', rec.baseline_forecast != null ? `${rec.baseline_forecast} → ${display(rec.current_forecast)}` : null],
    ['Reason (engine wording)', rec.reason],
    ['Generated at', rec.generated_at],
  ] as [string, any][]).filter(([, value]) => value !== null && value !== undefined && value !== '') : []

  const guard = data?.guard || {}
  const guardPassed = guard.numeric_grounding === 'passed' && guard.citations === 'passed'

  return <><div className="topline"/><main className="shell"><nav className="nav"><Link href="/" className="brand"><img src="/dunnhumby.svg" alt="dunnhumby" className="brand-logo" width={82} height={19}/><span className="brand-divider" aria-hidden="true"/><span className="brand-product">retail decision intelligence agent</span></Link><div className="navlinks"><Link href="/">Overview</Link><span className="nav-status"><i/>Read-only</span></div></nav>
    <section className="hero"><div><Link href="/" className="back">← Back to board</Link><div className="eyebrow" style={{ marginTop: 24 }}>Store detail</div><h1>Store {display(data?.store_id || id)}</h1><p>What the engine recommends for this store, and the evidence behind it.</p></div>{rec && <div className="hero-meta"><div className="live-dot"><i/>{display(rec.recommendation)}</div><strong>confidence {display(rec.confidence)}</strong>{rec.forecast_status && rec.forecast_status !== 'AVAILABLE' ? <span>forecast {display(rec.forecast_status)}</span> : null}</div>}</section>
    {error && <div className="notice">{error}</div>}
    {!data && !error ? <div className="loading">Loading store figures…</div> : data && <div className="detail-grid">
      <article className="panel">
        <h2 className="section-title">Why this recommendation</h2>
        <p className="narrative">{narrative(data.narrative)}</p>
      </article>
      <aside className="panel">
        <h2 className="section-title">Evidence the engine used</h2>
        {rows.length ? rows.map(([label, value]) => <Field key={label} label={label} value={value}/>) : <p className="directional-note">No recommendation on record for this store yet, so there is nothing to explain. Recommendations are produced by the next sweep.</p>}
        {Object.keys(guard).length ? <><h3 className="section-title" style={{ marginTop: 28 }}>Verification</h3>{guardPassed ? <p className="grounding">✓ Figures traced to the record</p> : <div className="chips">{Object.entries(guard).map(([name, value]) => <Guard key={name} name={name.replace(/_/g, ' ')} value={value}/>)}</div>}</> : null}
        {data.evidence ? <details className="raw"><summary>Raw evidence (JSON)</summary><pre>{JSON.stringify(data.evidence, null, 2)}</pre></details> : null}
      </aside>
    </div>}
    <footer className="footer"><span>© 2026 Ashish Kumar · data: dunnhumby</span><span>Read-only</span></footer>
  </main></>
}
