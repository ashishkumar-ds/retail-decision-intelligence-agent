/** @type {import('next').NextConfig} */
const nextConfig = {
  // Marks this build's dependency upgrade as security-driven (16.4.0 -> 16.4.3,
  // published advisory fix). Part of the next-upgrade agent flow.
  experimental: { agentUpgrade: "security" },
  // The on-screen dev indicator (the black "N" dot) reads as an unofficial
  // overlay on a stakeholder surface; compile errors still surface in the
  // terminal. See node_modules/next/dist/docs/.../devIndicators.md.
  devIndicators: false,
  // Keep the HTML document uncacheable so the page shell, the client bundle
  // hash and (for tools like the video generator) the initial frame never come
  // from a stale edge copy. Hashed _next/static assets already ship immutable
  // from Next/Vercel, so they need no override here.
  async headers() {
    return [
      {
        source: "/",
        headers: [
          {
            key: "Cache-Control",
            value: "no-cache, no-store, must-revalidate, max-age=0",
          },
          { key: "Pragma", value: "no-cache" },
          { key: "Expires", value: "0" },
          { key: "Surrogate-Control", value: "no-store" },
          { key: "CDN-Cache-Control", value: "no-store, max-age=0" },
        ],
      },
    ];
  },
};

export default nextConfig;
