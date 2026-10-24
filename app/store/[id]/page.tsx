import type { Metadata } from 'next'
import StoreDetail from './StoreDetail'

// Server wrapper around the client detail page so the route gets a real
// per-route title (the payload fetch itself stays client-side in StoreDetail).
export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params
  return { title: `Store ${id} — why this recommendation` }
}

export default function StorePage({ params }: { params: Promise<{ id: string }> }) {
  return <StoreDetail params={params} />
}
