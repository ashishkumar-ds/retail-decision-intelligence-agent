import type { Metadata } from 'next'
import localFont from 'next/font/local'
import { IBM_Plex_Mono, Roboto } from 'next/font/google'
import './globals.css'

// The brand face is TESCOModern — the very files dunnhumby.com self-hosts
// (_next/static/media/TESCOModern_*.woff, fetched 2026-11-01) — wired via
// next/font/local so it is served from our own origin at build time. The face
// is Tesco/dunnhumby brand IP with no public license sizing; it ships here for
// this dunnhumby-branded surface per the user's branding direction, with Roboto
// (same OFL face of the body) as the loading fallback. IBM Plex Mono stays for
// the register's tabular figures (TESCOModern has no mono cut).
const tescoModern = localFont({
  src: [
    { path: './fonts/TESCOModern-Light.woff', weight: '300', style: 'normal' },
    { path: './fonts/TESCOModern-Regular.woff', weight: '400', style: 'normal' },
    { path: './fonts/TESCOModern-Bold.woff', weight: '700', style: 'normal' },
  ],
  display: 'swap',
  variable: '--font-dh',
})

const roboto = Roboto({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  display: 'swap',
  variable: '--font-roboto',
})

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  display: 'swap',
  variable: '--font-mono',
})

// README's live deployment (stakeholder board on Vercel). Overridable per
// environment so previews resolve relative OG URLs against their own origin.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://retail-decision-intelligence-agent.vercel.app'

const SITE_DESCRIPTION =
  'Unlock the value of data. Smarter decisions, stronger collaboration, sustainable growth - store recovery on live dunnhumby customer science.'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'Retail Decision Intelligence · dunnhumby',
    template: '%s · dunnhumby',
  },
  description: SITE_DESCRIPTION,
  openGraph: {
    type: 'website',
    siteName: 'dunnhumby',
    description: SITE_DESCRIPTION,
  },
  twitter: {
    card: 'summary',
  },
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${tescoModern.variable} ${roboto.variable} ${ibmPlexMono.variable}`}>{children}</body>
    </html>
  )
}
