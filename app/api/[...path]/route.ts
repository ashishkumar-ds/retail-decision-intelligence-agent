import { NextResponse } from 'next/server'

type RouteContext = { params: Promise<{ path: string[] }> }

const ALLOWED_PATHS = new Set(['board', 'health', 'recommendations', 'pending-approvals'])
// The only write the browser may trigger. The backend still refuses anything
// without a valid approval token (503 when unconfigured, 401/403 otherwise) -
// this proxy forwards the caller's Authorization header and never injects one.
const WRITE_PATTERN = /^approve\/\d+$/

function upstreamBase() {
  return (process.env.NEXT_API_BASE || process.env.NEXT_PUBLIC_API_BASE || 'https://retail-decision-intelligence-agent.onrender.com').replace(/\/$/, '')
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

  const authorization = request.headers.get('authorization')
  if (!authorization) {
    return NextResponse.json({ detail: 'Missing Authorization header.' }, { status: 401 })
  }

  const upstream = new URL(`/${endpoint}`, `${upstreamBase()}/`)
  const body = await request.text()

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
