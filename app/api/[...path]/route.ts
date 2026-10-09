import { NextResponse } from 'next/server'

type RouteContext = { params: Promise<{ path: string[] }> }

const ALLOWED_PATHS = new Set(['board', 'health', 'recommendations', 'pending-approvals'])

export async function GET(request: Request, { params }: RouteContext) {
  const { path } = await params
  const endpoint = path.join('/')

  if (!ALLOWED_PATHS.has(endpoint) && !(path[0] === 'why' && path.length === 2 && /^\d+$/.test(path[1]))) {
    return NextResponse.json({ detail: 'Not found' }, { status: 404 })
  }

  const base = process.env.NEXT_API_BASE || process.env.NEXT_PUBLIC_API_BASE || 'https://retail-decision-intelligence-agent.onrender.com'
  const upstream = new URL(`/${endpoint}`, `${base.replace(/\/$/, '')}/`)
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
