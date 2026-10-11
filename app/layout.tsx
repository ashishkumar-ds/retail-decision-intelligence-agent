import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Retail Decision Board | dunnhumby',
  description: 'A read-only portfolio pulse for retail decision intelligence.',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
