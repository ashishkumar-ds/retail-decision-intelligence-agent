'use client'

// Shared chrome for both stakeholder surfaces: the brand rail + status wedge,
// the nav bar, the loading skeleton and the footer (with the one-shot health
// probe). The board and the store page hand in the pieces that differ — nav
// links, footer right-hand text, loading copy — everything else stays in one
// place so the pages cannot drift apart again.
import Link from 'next/link'
import { useEffect, useState, type ReactNode } from 'react'

import { api } from '../_lib/api'

/** Storefront outline, matching the card/shelf language of the pages. */
export function StorefrontMark() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 9.5V20h16V9.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 9.5l1.4-4.2h15.2L21 9.5H3Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="M8 13h8M8 16.5h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M10 20v-4h4v4" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
    </svg>
  )
}

export function Brand() {
  return (
    <Link href="/" className="brand">
      <img src="/dunnhumby.svg" alt="dunnhumby" className="brand-logo" width={82} height={19} />
      <span className="brand-divider" aria-hidden="true" />
      <span className="brand-product">retail decision intelligence agent</span>
    </Link>
  )
}

// The contract describes one footer health probe: after the board loads, call
// GET /health once and state the connection quietly — "API live" or, for a
// wake/failure, "API waking". Fail-silent, no polling loop, and it stays in
// the footer's muted styling (no motion, no own animation) rather than its
// loading state.
function Footer({ extra, probe }: { extra?: string; probe?: boolean }) {
  const [status, setStatus] = useState<'live' | 'waking' | 'idle'>('idle')

  useEffect(() => {
    if (!probe) return
    let alive = true
    fetch(api('/health'), { cache: 'no-store' })
      .then(response => {
        if (alive) setStatus(response.ok ? 'live' : 'waking')
      })
      .catch(() => {
        if (alive) setStatus('waking')
      })
    return () => {
      alive = false
    }
  }, [probe])

  const probeText = status === 'live' ? 'API live' : status === 'waking' ? 'API waking' : ''
  const right = [probeText, extra].filter(Boolean).join(' · ')

  return (
    <footer className="footer">
      <span>© 2026 Ashish Kumar · data: dunnhumby</span>
      <span>{right}</span>
    </footer>
  )
}

export function AppChrome({
  loading = false,
  navlinks,
  footerExtra,
  probe = false,
  children,
}: {
  loading?: boolean
  navlinks?: ReactNode
  footerExtra?: string
  probe?: boolean
  children: ReactNode
}) {
  return (
    <>
      <div className={`topline${loading ? ' is-loading' : ''}`} />
      <main className="shell">
        <nav className="nav">
          <Brand />
          <div className="navlinks">{navlinks ?? <span className="nav-status"><i aria-hidden="true" />Live board</span>}</div>
        </nav>
        {children}
        <Footer extra={footerExtra} probe={probe} />
      </main>
    </>
  )
}

// Loading the store feed: a retail-shaped skeleton, not a blank page. Shared
// because both pages wait on the same cold-start-prone API.
export function LoadingCard({
  kicker,
  title,
  copy,
  note,
  tiles = false,
}: {
  kicker: string
  title: string
  copy: string
  note?: string
  tiles?: boolean
}) {
  return (
    <section className="loading-board" role="status" aria-live="polite" aria-busy="true">
      <div className="loading-intro">
        <span className="loading-mark" aria-hidden="true">
          <StorefrontMark />
        </span>
        <div>
          <p className="loading-kicker">{kicker}</p>
          <h2 className="loading-title">{title}</h2>
          <p className="loading-copy">{copy}</p>
        </div>
      </div>
      <div className="loading-skeleton" aria-hidden="true">
        <span className="skeleton-line" />
        {tiles ? (
          <div className="skeleton-tiles">
            <span className="skeleton-tile" />
            <span className="skeleton-tile" />
            <span className="skeleton-tile" />
          </div>
        ) : null}
        <span className="skeleton-card" />
      </div>
      {note ? <p className="loading-note">{note}</p> : null}
    </section>
  )
}
