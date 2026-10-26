/** @type {import('next').NextConfig} */
const nextConfig = {
  // Marks this build's dependency upgrade as security-driven (16.4.0 -> 16.4.3,
  // published advisory fix). Part of the next-upgrade agent flow.
  experimental: { agentUpgrade: "security" },
};

export default nextConfig;
