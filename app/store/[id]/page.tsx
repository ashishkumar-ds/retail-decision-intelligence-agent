'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

// Store detail - the "why" page. The narrative is shown verbatim; its
// [rec:/event:/src:] markers become numbered references into a source list
// that carries no raw record ids. Every figure is printed as served. No client
// math, no rewriting, GET only.
type Citation = { type?: any; id?: any; detail?: any }
type Chunk = { title?: any; part?: any; score?: any; license_note?: any }
type Detail = { store_id?: any; question?: any; narrative?: string; citations?: Citation[]; methodology?: Chunk[]; guard?: Record<string, any>; evidence?: { latest_recommendation?: Record<string, any> } }

const MARKER = /\[(rec|event|src):([^\]\s]+)\]/g
const KINDS: Record<string, string> = { rec: 'Recommendation', event: 'Lifecycle event', src: 'Methodology' }

function api(path: string) { return `/api${path}` }
function display(value: any) { return value === null || value === undefined || value === '' ? '—' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value) }

// A marker becomes a numbered reference, never the record id: a stakeholder
// needs the provenance, not a hash. The number matches the source list below;
// the same record cited twice gets the same number.
function Narrative({ text, numbers }: { text: string; numbers: Map<string, number> }) {
  const parts = String(text || '').split(MARKER)
  return <p className="narrative">{parts.map((part, i) => {
    if (i % 3 === 0) {
      // The template writes "0.62 [rec:…]"; a reference attaches to the word,
      // not to the space before the marker.
      return <span key={i}>{parts[i + 1] === undefined ? part : part.replace(/\s+$/, '')}</span>
    }
    if (i % 3 === 2) return null
    // i % 3 === 1 is the marker kind; the record id is the next capture.
    const n = numbers.get(String(parts[i + 1] || ''))
    return n ? <a className="ref" key={i} href={`#source-${n}`} aria-label={`Source ${n}`}>{n}</a> : null
  })}</p>
}
function Field({ label, value }: { label: string; value: any }) { if (value === null || value === undefined || value === '') return null; const long = String(value).length > 44; return <div className={long ? 'kv kv-long' : 'kv'}><span>{label}</span><strong>{label.endsWith('ID') ? <code>{display(value)}</code> : display(value)}</strong></div> }
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

  const citations = data?.citations || []
  // Marker id -> source number. Repeated citations of the same record keep one
  // number, so identical references do not multiply the list.
  const numbers = new Map<string, number>()
  citations.forEach((citation, i) => {
    const key = String(citation.id || '')
    if (key && !numbers.has(key)) numbers.set(key, i + 1)
  })
  // The retriever returns several chunks of the same document (parts 1, 4, 2).
  // One title is the evidence; the part number and score are retrieval internals.
  const methods = [...new Map((data?.methodology || []).map(item => [String(item.title || ''), item])).values()].filter(item => item.title)
  const guard = data?.guard || {}
  const guardPassed = guard.numeric_grounding === 'passed' && guard.citations === 'passed'

  return <><div className="topline"/><main className="shell"><nav className="nav"><Link href="/" className="brand"><img src="/dunnhumby.svg" alt="dunnhumby" className="brand-logo" width={82} height={19}/><span className="brand-divider" aria-hidden="true"/><span className="brand-product">retail decision intelligence agent</span></Link><div className="navlinks"><Link href="/">Overview</Link><span className="nav-status"><i/>Read-only</span></div></nav>
    <section className="hero"><div><Link href="/" className="back">← Back to board</Link><div className="eyebrow" style={{ marginTop: 24 }}>Store detail</div><h1>Store {display(data?.store_id || id)}</h1><p>What the engine recommends for this store, and the evidence behind it.</p></div>{rec && <div className="hero-meta"><div className="live-dot"><i/>{display(rec.recommendation)}</div><strong>confidence {display(rec.confidence)}</strong>{rec.forecast_status && rec.forecast_status !== 'AVAILABLE' ? <span>forecast {display(rec.forecast_status)}</span> : null}</div>}</section>
    {error && <div className="notice">{error}</div>}
    {!data && !error ? <div className="loading">Loading store figures…</div> : data && <div className="detail-grid">
      <article className="panel">
        <h2 className="section-title">Why this recommendation</h2>
        <Narrative text={data.narrative || ''} numbers={numbers}/>
        {citations.length ? <><h3 className="section-title" style={{ marginTop: 26 }}>Sources</h3><ol className="sources">{citations.map((citation, i) => <li className="source" id={`source-${i + 1}`} key={i}><span className="source-kind">{KINDS[String(citation.type)] || display(citation.type)}</span>{' '}<span className="source-detail">{display(citation.detail)}</span></li>)}</ol></> : null}
      </article>
      <aside className="panel">
        <h2 className="section-title">Evidence the engine used</h2>
        {rows.length ? rows.map(([label, value]) => <Field key={label} label={label} value={value}/>) : <p className="directional-note">No recommendation on record for this store yet, so there is nothing to explain. Recommendations are produced by the next sweep.</p>}
        {Object.keys(guard).length ? <><h3 className="section-title" style={{ marginTop: 28 }}>Grounding checks</h3>{guardPassed ? <p className="grounding">✓ Numbers and citations verified</p> : <div className="chips">{Object.entries(guard).map(([name, value]) => <Guard key={name} name={name.replace(/_/g, ' ')} value={value}/>)}</div>}</> : null}
        {methods.length ? <><h3 className="section-title" style={{ marginTop: 28 }}>Methodology applied</h3><ul className="methods">{methods.map(item => <li className="method" key={display(item.title)}><strong>{display(item.title)}</strong>{item.license_note ? <span className="method-note">Source: {display(item.license_note)}</span> : null}</li>)}</ul></> : null}
        {data.evidence ? <details className="raw"><summary>Raw evidence (JSON)</summary><pre>{JSON.stringify(data.evidence, null, 2)}</pre></details> : null}
      </aside>
    </div>}
    <footer className="footer"><span>© 2026 Ashish Kumar · data: dunnhumby</span><span>Read-only</span></footer>
  </main></>
}
