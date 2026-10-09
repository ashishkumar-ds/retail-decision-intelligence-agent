const nextConfig = {
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || process.env.NEXT_API_BASE || '',
  },
};

export default nextConfig;
