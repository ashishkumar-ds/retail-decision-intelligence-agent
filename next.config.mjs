/** @type {import('next').NextConfig} */
const nextConfig = {
  // Marks this build's dependency upgrade as security-driven (16.4.0 -> 16.4.3,
  // published advisory fix). Part of the next-upgrade agent flow.
  experimental: { agentUpgrade: "security" },
  // The on-screen dev indicator (the black "N" dot) reads as an unofficial
  // overlay on a stakeholder surface; compile errors still surface in the
  // terminal. See node_modules/next/dist/docs/.../devIndicators.md.
  devIndicators: false,
  // Prevent Vercel CDN from caching the homepage so users always see the
  // current board data instead of a stale cached shell.
  async headers() {
    return [
      {
        source: "/",
        headers: [
          {
            key: "Cache-Control",
            value: "no-cache, no-store, must-revalidate",
          },
          {
            key: "Pragma",
            value: "no-cache",
          },
          {
            key: "Expires",
            value: "0",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
