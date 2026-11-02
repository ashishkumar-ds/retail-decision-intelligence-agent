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
      {/* Marketing lines retired (user, 2026-11-02): the strapline and the
          positioning line competed with the board's own signal. The line the
          footer does keep is the legal one. If a probe is live, it reads
          quietly on the right. */}
      <span className="footer-brandline">© 2026 dunnhumby limited</span>
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
// sleeps on the free tier, so a fresh load can take a while - this screen
// owns that wait instead of hiding it: a full-viewport indigo field, the
// official lockup at scale, and the print-head rule passing beneath. Nothing
// here invents progress - it doesn't even speak. It plays for every wait a
// fresh document produces - first visit or refresh; same-document route
// changes take the three-dot mark.
export function BootScreen() {
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
    </section>
  )
}

// Which wait screen belongs to this wait? A fresh document - a first visit
// or a refresh - always takes the full boot stage, and the answer is
// settled before first paint so the screen holds steady instead of
// collapsing into the small mark mid-repaint. A route change inside the
// same document ("back to board") takes the three-dot mark: the stage is a
// document-level moment, not a per-fetch one. Module state, not tab
// storage - it resets with the document by definition, which is also what
// keeps the server render and the first client paint identical.
let waitedThisDocument = false

// Frozen per mount on purpose: a wait screen must never swap mid-life. The
// decision is settled once (document age: fresh document boots, a remount
// from same-document navigation gets the mark) and kept until the wait
// ends, whatever state changes happen underneath.
export function useWaitScreen(): 'boot' | 'spinner' {
  const [screen] = useState<'boot' | 'spinner'>(() =>
    typeof window !== 'undefined' && waitedThisDocument ? 'spinner' : 'boot')
  useEffect(() => {
    waitedThisDocument = true
  }, [])
  return screen
}

// Repeat waits: a three-dot mark in register indigo on the stock ground.
// The dots light one after another - a third of the cycle each, on a stepped
// cadence, the retired LED rail's rhythm again (never an ease). Screen
// readers still get the plain status line below.
export function SpinnerScreen() {
  return (
    <section className="spinner-stage" aria-busy="true">
      <span className="spinner-dots" aria-hidden="true"><i /><i /><i /></span>
      <p className="spinner-line" role="status">
        Loading.
      </p>
    </section>
  )
}
