import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Campaign 18 Decision Board | dunnhumby',
  description: 'Campaign 18 store review. Which stores are responding, which need a decision, and which are waiting on data.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
