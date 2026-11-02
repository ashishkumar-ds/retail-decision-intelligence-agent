'use client'

// Shared chrome for both stakeholder surfaces: the masthead band, the nav
// bar, the boot/spinner wait states and the footer (with the one-shot health
// probe). The board and the store ("why") page hand in the pieces that
// differ — nav links, footer right-hand text — everything else stays in one
// place so the pages cannot drift apart again.
import Link from 'next/link'
import { useEffect, useState, type ReactNode } from 'react'

import { api } from '../_lib/api'

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
      {/* Brand voice is dunnhumby's own copy (dunnhumby.com, fetched 2026-11-01):
          the house strapline and the "Unlock the value of data" positioning line. */}
      <span className="footer-brandline">
        The science of serving every customer. Together. At scale.
        <br />
        Unlock the value of data · © 2026 dunnhumby limited
      </span>
      <span>{right}</span>
    </footer>
  )
}

export function AppChrome({
  navlinks,
  footerExtra,
  probe = false,
  children,
}: {
  navlinks?: ReactNode
  footerExtra?: string
  probe?: boolean
  children: ReactNode
}) {
  return (
    <>
      <div className="brandband">
        <div className="shell">
          <nav className="nav">
            <Brand />
            <div className="navlinks">{navlinks ?? <span className="nav-status"><i aria-hidden="true" />Live board</span>}</div>
          </nav>
        </div>
      </div>
      <main className="shell">
        {children}
        <Footer extra={footerExtra} probe={probe} />
      </main>
    </>
  )
}

// The boot stage: the one moment the register speaks for itself. The API
// sleeps on the free tier, so a first load can take about a minute - this
// screen owns that wait instead of hiding it: a full-viewport indigo field,
// the official lockup at scale, one honest wake line, and a real elapsed
// timer. Nothing here invents progress. It plays for any surface's first
// wait of a session; later waits take the round mark below.
export function BootScreen() {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const startedAt = Date.now()
    const tick = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => clearInterval(tick)
  }, [])

  const clock = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`

  return (
    <section className="boot">
      <div className="boot-lockup">
        <img src="/dunnhumby.svg" alt="dunnhumby" className="boot-mark" width={188} height={44} />
        <span className="boot-divider" aria-hidden="true" />
        <span className="boot-product">retail decision intelligence agent</span>
      </div>
      {/* The print-head pass: a paper block steps across the lead rule on the
          cadence the retired LED rail used - steps, never eases. Hidden from
          the status reading and parked for reduced motion. */}
      <div className="boot-lead" aria-hidden="true"><span /></div>
      <div className="boot-strip">
        <p className="boot-wake" role="status" aria-live="polite" aria-busy="true">
          {elapsed >= 60
            ? 'Still waking - the page prints the moment the data lands.'
            : 'First load wakes the system. Up to about a minute.'}
        </p>
        <p className="boot-clock" aria-hidden="true">{clock}</p>
      </div>
    </section>
  )
}

// Tab-scoped boot gate, shared by every stakeholder surface: the full boot
// stage plays for the session's first *successful* load only - the flag
// records a working feed, not a bare visit, so a failed first visit still
// earns the boot stage on retry. Every later wait in the same tab takes the
// round mark instead; a new tab (a new cold start) earns the stage again.
const BOOT_SEEN_KEY = 'agent-booted'

export function useBootedOnce(): boolean {
  const [booted, setBooted] = useState(false)
  useEffect(() => {
    setBooted(sessionStorage.getItem(BOOT_SEEN_KEY) === '1')
  }, [])
  return booted
}

export function markBooted() {
  try {
    sessionStorage.setItem(BOOT_SEEN_KEY, '1')
  } catch {
    // Private-mode storage refusals just mean the boot stage shows again;
    // no stakeholder journey should break over a wait-state preference.
  }
}

// Repeat waits: a standard round mark in register indigo on the stock
// ground - one full track at 14% ink under a quarter arc in full ink, round-
// capped and drawn on an SVG ring so the thicker stroke stays crisp. Stepped
// rotation: even the spinner never eases. Status reads as plain text below.
export function SpinnerScreen() {
  return (
    <section className="spinner-stage" aria-busy="true">
      <svg className="spinner-ring" viewBox="0 0 32 32" aria-hidden="true">
        <circle className="ring-track" cx="16" cy="16" r="13" fill="none" strokeWidth="3.5" />
        <circle
          className="ring-arc"
          cx="16" cy="16" r="13"
          fill="none"
          strokeWidth="3.5"
          strokeLinecap="round"
          strokeDasharray="20.4 61.7"
          transform="rotate(-90 16 16)"
        />
      </svg>
      <p className="spinner-line" role="status">
        Loading. Up to about a minute.
      </p>
    </section>
  )
}
