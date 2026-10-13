import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Retail Decision Board | dunnhumby',
  description: 'A stakeholder view of retail performance, store response, and decisions that need attention.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
