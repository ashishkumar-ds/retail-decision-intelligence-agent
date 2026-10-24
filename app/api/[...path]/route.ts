import { NextResponse } from 'next/server'

// The board self-heals a stale snapshot on its first read of the day; that
// re-evaluation fans out over the forecast API, so give the proxy room beyond
// the platform default instead of timing out mid-refresh.
export const maxDuration = 60

type RouteContext = { params: Promise<{ path: string[] }> }

const ALLOWED_PATHS = new Set(['board', 'health', 'recommendations', 'pending-approvals'])
// All allowlisted GETs are public read endpoints on the backend
// (docs/FRONTEND_CONTRACT.md: exactly one client write), so Authorization is
// intentionally not forwarded on GET today — the proxy attaches no credential
// it was not handed for the one approved POST.
// The only write the browser may trigger. The backend still refuses anything
// without a valid approval token (503 when unconfigured, 401/403 otherwise) -
// this proxy forwards the caller's Authorization header and never injects one.
const WRITE_PATTERN = /^approve\/\d+$/
// The one client write sends an empty JSON body, so a small cap keeps misused
// or accidental uploads from flowing to the approval endpoint (fail closed).
const MAX_BODY_BYTES = 64 * 1024

// Server-only config: the upstream hostname stays on the platform, so the
// browser bundle never sees it (no NEXT_PUBLIC_ name — client code only talks
// to the same-origin /api proxy).
function upstreamBase() {
  return (process.env.API_BASE || process.env.NEXT_API_BASE || 'https://retail-decision-intelligence-agent.onrender.com').replace(/\/$/, '')
}

export async function GET(request: Request, { params }: RouteContext) {
  const { path } = await params
  const endpoint = path.join('/')

  if (!ALLOWED_PATHS.has(endpoint) && !(path[0] === 'why' && path.length === 2 && /^\d+$/.test(path[1]))) {
    return NextResponse.json({ detail: 'Not found' }, { status: 404 })
  }

  const upstream = new URL(`/${endpoint}`, `${upstreamBase()}/`)
  upstream.search = new URL(request.url).search

  try {
    const response = await fetch(upstream, { cache: 'no-store' })
    return new NextResponse(response.body, {
      status: response.status,
      headers: { 'content-type': response.headers.get('content-type') || 'application/json' },
    })
  } catch {
    return NextResponse.json({ detail: 'Upstream API unavailable' }, { status: 502 })
  }
}

export async function POST(request: Request, { params }: RouteContext) {
  const { path } = await params
  const endpoint = path.join('/')

  if (!WRITE_PATTERN.test(endpoint)) {
    return NextResponse.json({ detail: 'Not found' }, { status: 404 })
  }

  // The one client write sends JSON. Anything else is refused here, before it
  // can reach the approval endpoint with an unexpected payload shape.
  // Accepts application/json plus the +json structured-syntax suffixes.
  const contentType = request.headers.get('content-type') || ''
  if (!/^application\/([\w$!#%^&*.~-]+\+)?json\b/i.test(contentType)) {
    return NextResponse.json({ detail: 'Unsupported Media Type: expected JSON.' }, { status: 415 })
  }

  const authorization = request.headers.get('authorization')
  if (!authorization) {
    return NextResponse.json({ detail: 'Missing Authorization header.' }, { status: 401 })
  }

  const upstream = new URL(`/${endpoint}`, `${upstreamBase()}/`)
  const body = await request.text()

  if (new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) {
    return NextResponse.json({ detail: 'Request body too large.' }, { status: 413 })
  }

  try {
    const response = await fetch(upstream, {
      method: 'POST',
      headers: {
        'content-type': request.headers.get('content-type') || 'application/json',
        authorization,
      },
      body: body || '{}',
      cache: 'no-store',
    })
    return new NextResponse(response.body, {
      status: response.status,
      headers: { 'content-type': response.headers.get('content-type') || 'application/json' },
    })
  } catch {
    return NextResponse.json({ detail: 'Upstream API unavailable' }, { status: 502 })
  }
}
