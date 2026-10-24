import type { Metadata } from 'next'
import { Roboto, Space_Grotesk } from 'next/font/google'
import './globals.css'

// Body is Roboto — the same OFL face dunnhumby.com serves — and display is
// Space Grotesk: dunnhumby's own display face TESCOModern is proprietary with
// no public license, so this is the closest neutral grotesk fallback (also
// OFL). next/font self-hosts both at build time, so the browser never sends a
// render-blocking request to Google and family metrics match the previous
// @import exactly.
const roboto = Roboto({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  display: 'swap',
  variable: '--font-roboto',
})

const spaceGrotesk = Space_Grotesk({
  subsets: ['latin'],
  weight: ['500', '600', '700'],
  display: 'swap',
  variable: '--font-space-grotesk',
})

// README's live deployment (stakeholder board on Vercel). Overridable per
// environment so previews resolve relative OG URLs against their own origin.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://retail-decision-intelligence-agent.vercel.app'

const SITE_DESCRIPTION =
  'Store review. Which stores need a decision, which have no signal to judge, and which need no action.'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'Retail Decision Intelligence Agent',
    template: '%s · Retail Decision Intelligence Agent',
  },
  description: SITE_DESCRIPTION,
  openGraph: {
    type: 'website',
    siteName: 'Retail Decision Intelligence Agent',
    description: SITE_DESCRIPTION,
  },
  twitter: {
    card: 'summary',
  },
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${roboto.variable} ${spaceGrotesk.variable}`}>{children}</body>
    </html>
  )
}
