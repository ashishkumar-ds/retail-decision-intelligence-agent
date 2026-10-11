import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Campaign 18 Decision Board | dunnhumby',
  description: 'Is Campaign 18 working — store by store. A read-only recovery and intervention view.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
